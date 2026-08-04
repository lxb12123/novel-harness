# M4 Event Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M4's explicit, background incremental extraction loop: evidence-backed event hyperedges and character profiles enter PROVISIONAL storage, only exceptions reach author review, accepted facts become CANON, and the writer receives safe deterministic event memory.

**Architecture:** Keep the frozen eight node/nine edge schema intact. Events are independent hyperedges stored in `story_event` plus participant/knower/reveal incidence tables; `EventStore` is a sibling protocol rather than an expansion of `StoryGraph`. The analysis LLM returns untrusted surfaces and quotes, the server resolves IDs and exact/fuzzy evidence, and an exception-driven ingestion service owns proposal creation. Product drafting wraps the frozen M2 assembler with a separate event-memory preface; it never changes the kill-gate prompt forms.

**Tech Stack:** Python 3.12, Pydantic 2, SQLite migrations, FastAPI, OpenAI-compatible provider, React 19, TypeScript, TanStack Query, Zustand, pytest, Vitest.

---

## Locked decisions

- Event granularity is a story beat: one chapter may yield 1–12 independent event summaries, each backed by exactly one source quote in this slice.
- Only `CANON` and `PROVISIONAL` events are queryable. `PROVISIONAL` is gray/read-only context and is never asserted as true, used by rules, or sent to the writer.
- A high-confidence, non-conflicting extraction is stored as `PROVISIONAL` without a queue item. Queue items are limited to a direct CANON conflict, confidence below `0.70` involving a main character, and a new persistent-character decision. The passive provisional list still offers explicit single/batch confirmation, so no extracted assertion becomes CANON without an author action.
- Accept/edit creates a new CANON projection and preserves the PROVISIONAL row. Reject preserves the provisional row and records the rejected review. `bystander` is a character-candidate rejection reason and never creates a Character node.
- Evidence is `(para_index, matched original text, occurrence_k)`. Exact location wins; fuzzy location is accepted only for a unique best match with `SequenceMatcher.ratio() >= 0.90`; the stored quote and hash always use the source substring.
- Quotes are 4–120 characters. Quotes shorter than 10 characters are allowed for verbatim short utterances (e.g. 「卧槽，陨石！」) but capped at 20% of the chapter's quotes, with a floor of one — frequency control instead of a hard ban (2026-08-04 real-run finding; the first live chapters failed because a single 6-character verbatim quote violated the old 10-character minimum).
- Retrieval is deterministic SQL and lexical incidence only. There is no embedding or ANN path in M4 because ADR 0002 requires a measured retrieval failure first.
- Writer safety is initially strict: only CANON events before the current chapter that involve a current cast member and are known by every resolved cast member may enter the prompt.
- The rolling background is a deterministic summary of accepted events: all CANON events from the previous 8 chapters plus up to 12 older cast-related events, sorted by `(chapter, event_id)`. No second summarizer call is introduced until this context shape is evaluated.

### Task 1: Schema, IDs, and domain contracts

**Files:**
- Create: `src/novel_harness/migrations/002_m4_events.sql`
- Create: `src/novel_harness/events/__init__.py`
- Create: `src/novel_harness/events/models.py`
- Create: `src/novel_harness/events/store.py`
- Modify: `src/novel_harness/ids.py`
- Modify: `src/novel_harness/graph/models.py`
- Modify: `tests/test_migrate.py`
- Modify: `tests/test_ids.py`
- Create: `tests/test_event_models.py`

- [x] **Step 1: Write migration and model tests first**

  Add tests that migrate a populated v1 database to version 2 without changing existing rows; assert the presence and FK/CHECK behavior of `story_event`, `event_participant`, `event_knower`, `event_reveal`, `proposal_event`, `proposal_edge`, and `extraction_run`; assert `proposal_set` has `chapter_number`, `snapshot_id`, `base_canon_version`, `schema_version`, and `prompt_hash`. Add model tests proving input models are frozen/`extra='forbid'`, scopes cannot be supplied through `ProvisionalEventSpec`, and `EntityType.EVENT` produces `event:<project-short>:<ULID>`.

  The public contracts must be:

  ```python
  class EventCharacterRole(StrEnum):
      PARTICIPANT = "participant"
      KNOWER = "knower"

  class StoryEvent(BaseModel):
      model_config = ConfigDict(frozen=True)
      id: str
      project_id: str
      chapter_number: int = Field(ge=1)
      summary: str = Field(min_length=1)
      information_scope: InformationScope
      status: EdgeStatus
      confidence: float | None = Field(default=None, ge=0, le=1)
      source: EdgeSource
      evidence_id: str
      evidence_status: EvidenceStatus
      derived_from_event_id: str | None = None

  class EventView(BaseModel):
      model_config = ConfigDict(frozen=True)
      event: StoryEvent
      participants: list[NodeRef] = Field(default_factory=list)
      knowers: list[NodeRef] = Field(default_factory=list)
      revealed_facts: list[NodeRef] = Field(default_factory=list)

  class ProvisionalEventSpec(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")
      project_id: str
      summary: str = Field(min_length=1)
      evidence_id: str
      participant_ids: list[str] = Field(default_factory=list)
      knower_ids: list[str] = Field(default_factory=list)
      revealed_fact_ids: list[str] = Field(default_factory=list)
      confidence: float = Field(ge=0, le=1)

  class CharacterProfilePatch(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")
      gender: str | None = None
      personality: str | None = None
      background: str | None = None
      character_notes: str | None = None
      main_character: bool | None = None
  ```

  `EventStore` must expose `put_provisional`, `clone_to_scope`, `events_for_characters`, `events_for_chapter`, `event`, `profile`, and `update_profile`. A sibling `ProposalStore` exposes `create`, `pending`, `get`, and `mark_resolved`; no method is added to `StoryGraph`.

- [x] **Step 2: Run the focused tests and verify RED**

  Run: `uv run pytest tests/test_migrate.py tests/test_ids.py tests/test_event_models.py -q`

  Expected: failures for missing migration version 2, missing event package, and missing `EntityType.EVENT`.

- [x] **Step 3: Add the minimal schema and models**

  The migration must use strict checks copied from the existing temporal graph contract, compound project/label FKs for Character and Secret incidence, and `UNIQUE(project_id, evidence_id, information_scope)` for the one-anchor/one-event first slice. `extraction_run.status` is one of `PENDING/RUNNING/SUCCEEDED/FAILED`; its unique key is `(project_id, snapshot_id, schema_version, prompt_hash)`. Extend `NodeProps` only with the five `CharacterProfilePatch` fields; do not add Event to `NodeLabel`.

- [x] **Step 4: Run focused tests and migration regression**

  Run: `uv run pytest tests/test_migrate.py tests/test_ids.py tests/test_event_models.py -q`

  Expected: all selected tests pass and `migrate()` twice returns 2.

- [x] **Step 5: Commit**

  ```bash
  git add src/novel_harness/migrations/002_m4_events.sql src/novel_harness/events src/novel_harness/ids.py src/novel_harness/graph/models.py tests/test_migrate.py tests/test_ids.py tests/test_event_models.py
  git commit -m "feat: add M4 event memory schema"
  ```

### Task 2: SQLite EventStore and temporal retrieval

**Files:**
- Create: `src/novel_harness/graph/sqlite_events.py`
- Create: `src/novel_harness/graph/sqlite_proposals.py`
- Modify: `src/novel_harness/graph/queries.py`
- Modify: `src/novel_harness/graph/__init__.py`
- Create: `tests/test_event_store.py`
- Modify: `tests/test_arch_guard.py`

- [x] **Step 1: Write EventStore behavior tests**

  Use the real migrated in-memory database and `SqliteStoryGraph` to seed Character/Secret nodes and evidence. Assert:

  ```python
  stored = events.put_provisional(ProvisionalEventSpec(...))
  assert stored.event.information_scope is InformationScope.PROVISIONAL
  assert [n.id for n in stored.participants] == sorted({alice_id, bob_id})
  assert events.events_for_characters(pid, [alice_id], chapter=10,
                                      scope=InformationScope.CANON) == []
  assert events.events_for_characters(pid, [alice_id], chapter=10,
                                      scope=InformationScope.PROVISIONAL) == [stored]
  ```

  Cover ch9/ch10/ch142/ch143 closed-open boundaries; cross-project characters; wrong labels; duplicate incidence; `STALE`/`RETRACTED` suppression; CANON/PROVISIONAL isolation; invalid PLANNED/REJECTED reads; idempotent re-put of the same evidence; stable `(chapter_number, event_id)` ordering; profile patch merge without erasing unspecified fields; and adding/changing aliases after event creation while every stored event reference still resolves through its stable node ID.

- [x] **Step 2: Run the new test and verify RED**

  Run: `uv run pytest tests/test_event_store.py -q`

  Expected: import failure for `novel_harness.graph.sqlite_events`.

- [x] **Step 3: Implement the repository**

  Put all SQL and row-to-Pydantic conversion in `graph/sqlite_events.py`/`graph/queries.py`. Reuse `TEMPORAL_WHERE`; do not copy its five conditions into `extract/`, `events/`, API, or draft code. Constructor shape:

  ```python
  class SqliteEventStore:
      def __init__(self, conn: Connection) -> None:
          self._conn = conn

      def put_provisional(self, spec: ProvisionalEventSpec) -> EventView: ...
      def clone_to_scope(self, event_id: str, scope: InformationScope,
                         *, summary: str | None = None) -> EventView: ...
      def events_for_characters(self, project_id: str, character_ids: Sequence[str],
                                chapter: int, scope: InformationScope) -> list[EventView]: ...
  ```

  `put_provisional` derives chapter number through evidence→snapshot→chapter, hard-codes PROVISIONAL/ACTIVE/FRESH/extractor, validates every incidence belongs to the project and expected label, and performs the event plus all incidence writes in one `BEGIN IMMEDIATE` transaction.

- [x] **Step 4: Tighten the architecture guard**

  Add the event tables to `GRAPH_TABLES`; allow their SQL only in `graph/sqlite_events.py` and migration files. Assert `StoryGraph`'s protocol method set is unchanged.

- [x] **Step 5: Run focused and conformance tests**

  Run: `uv run pytest tests/test_event_store.py tests/test_arch_guard.py tests/test_store_conformance.py -q`

  Expected: all pass.

- [x] **Step 6: Commit**

  ```bash
  git add src/novel_harness/graph src/novel_harness/events tests/test_event_store.py tests/test_arch_guard.py
  git commit -m "feat: persist and retrieve event hyperedges"
  ```

### Task 3: Structured extraction, name resolution, and fuzzy evidence

**Files:**
- Create: `src/novel_harness/extract/__init__.py`
- Create: `src/novel_harness/extract/models.py`
- Create: `src/novel_harness/extract/prompt.py`
- Create: `src/novel_harness/extract/locate.py`
- Create: `src/novel_harness/extract/analyze.py`
- Create: `tests/test_extract_models.py`
- Create: `tests/test_extract_locate.py`
- Create: `tests/test_extract_analyze.py`

- [x] **Step 1: Write parser and locator tests**

  Freeze this untrusted LLM contract:

  ```python
  class RawEvent(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")
      summary: str = Field(min_length=1)
      quote: str = Field(min_length=4, max_length=120)
      participants: list[str] = Field(default_factory=list)
      knowers: list[str] = Field(default_factory=list)
      revealed_facts: list[str] = Field(default_factory=list)
      confidence: float = Field(ge=0, le=1)

  class RawCharacterProfile(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")
      surface: str = Field(min_length=2)
      gender: str | None = None
      personality: str | None = None
      background: str | None = None
      character_notes: str | None = None
      confidence: float = Field(ge=0, le=1)

  class RawStateUpdate(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")
      kind: Literal["location", "state", "relationship"]
      subject: str = Field(min_length=1)
      object: str | None = None
      dimension: str | None = None
      value: str | None = None
      quote: str = Field(min_length=4, max_length=120)
      confidence: float = Field(ge=0, le=1)

  class RawChapterAnalysis(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")
      events: list[RawEvent] = Field(default_factory=list, max_length=12)
      state_updates: list[RawStateUpdate] = Field(default_factory=list, max_length=24)
      character_profiles: list[RawCharacterProfile] = Field(default_factory=list)
  ```

  Tests must reject markdown fences, trailing prose, unknown keys, IDs, chapter/scope/status fields, malformed JSON, invalid state-update field combinations, more than 12 events, and more than 24 state updates. Locator tests must prove exact match precedence, unique 0.90 fuzzy acceptance, 0.899 discard, ambiguity discard, and that the returned `matched_text` is byte-for-byte from source.

- [x] **Step 2: Run tests and verify RED**

  Run: `uv run pytest tests/test_extract_models.py tests/test_extract_locate.py tests/test_extract_analyze.py -q`

  Expected: missing `novel_harness.extract` modules.

- [x] **Step 3: Implement the pure extraction boundary**

  `parse_analysis(text)` calls `RawChapterAnalysis.model_validate_json(text)` exactly once and raises `AnalysisFormatError`; it never repairs JSON or retries. `locate_quote(paragraphs, quote, min_ratio=0.90)` first uses `text.anchor.find_all`; fuzzy matching uses deterministic same-length and sentence-window candidates, requires one strictly best candidate, and returns `Located` with the original substring. `resolve_surfaces` accepts only a unique existing alias resolution; unknown and ambiguous surfaces remain explicit candidate data and are never guessed.

- [x] **Step 4: Run the extraction unit tests**

  Run: `uv run pytest tests/test_extract_models.py tests/test_extract_locate.py tests/test_extract_analyze.py -q`

  Expected: all pass.

- [x] **Step 5: Commit**

  ```bash
  git add src/novel_harness/extract tests/test_extract_models.py tests/test_extract_locate.py tests/test_extract_analyze.py
  git commit -m "feat: validate and anchor chapter extraction"
  ```

### Task 4: Provider-backed background extraction and exception ingestion

**Files:**
- Modify: `src/novel_harness/draft/capabilities.py`
- Modify: `src/novel_harness/draft/provider.py`
- Create: `src/novel_harness/extract/service.py`
- Create: `src/novel_harness/extract/runner.py`
- Create: `tests/test_structured_provider.py`
- Create: `tests/test_extract_service.py`
- Create: `tests/test_extract_runner.py`

- [x] **Step 1: Write tests for structured calls and ingestion**

  Add a sibling `StructuredCallPlan` with a caller-declared output-token budget and no prose `LengthSpec`. Prove `provider.complete()` emits the same route/reasoning/stream wire fields for either validated plan and that the M2 `ResolvedCallPlan` snapshots remain byte-for-byte unchanged.

  Service tests inject a fake analyzer function and real stores. Cover:

  - valid unique quote + resolvable names → PROVISIONAL event;
  - valid location/state/relationship updates map to existing `LOCATED_AT`/`HAS_STATE`/`RELATED_TO` PROVISIONAL edges;
  - ratio below 0.90 or ambiguous evidence → discarded with a counted reason;
  - an exclusive state update that disagrees with current CANON → one `edge_conflict` proposal linked through `proposal_edge`;
  - confidence `<0.70` and a profile-marked main character → one `low_confidence_main` proposal;
  - unknown profile surface → one `new_character` proposal, no node created;
  - unknown incidental surface in an event → event discarded, never guessed;
  - high-confidence non-conflict → no proposal row;
  - rerun of the same `(snapshot, schema version, prompt hash)` → same run/no second paid call;
  - model response is parsed once; malformed JSON marks run FAILED and performs no graph writes.

- [x] **Step 2: Run focused tests and verify RED**

  Run: `uv run pytest tests/test_structured_provider.py tests/test_extract_service.py tests/test_extract_runner.py -q`

  Expected: missing structured plan and extraction service.

- [x] **Step 3: Implement the service and runner**

  The boundary must be:

  ```python
  class ExtractionService:
      def ingest(self, project_id: str, chapter: ChapterText,
                 analysis: RawChapterAnalysis, *, prompt_hash: str) -> ExtractionReport: ...

  class ExtractionRunner:
      def enqueue(self, project_id: str, chapter_number: int) -> ExtractionRun: ...
      def run(self, run_id: str) -> ExtractionRun: ...
  ```

  `enqueue` creates/reuses the idempotency row and returns before the paid call. `run` opens its own migrated connection, changes PENDING→RUNNING→SUCCEEDED/FAILED, records a `model_call(capability='extractor')`, and stores only server-resolved IDs/evidence. Conflict detection is exact and deterministic and applies to existing graph exclusivity: a proposed `LOCATED_AT`, `HAS_STATE`, or `RELATED_TO` fact conflicts only when the corresponding current CANON exclusivity key already has a different destination/value. Event summaries themselves are never semantically compared in Python.

- [x] **Step 4: Run focused tests and M2 provider regressions**

  Run: `uv run pytest tests/test_structured_provider.py tests/test_extract_service.py tests/test_extract_runner.py tests/test_draft_provider.py tests/test_draft_capabilities.py -q`

  Expected: all pass; frozen M2 tests remain unchanged.

- [x] **Step 5: Commit**

  ```bash
  git add src/novel_harness/draft src/novel_harness/extract tests/test_structured_provider.py tests/test_extract_service.py tests/test_extract_runner.py
  git commit -m "feat: run exception-driven extraction"
  ```

### Task 5: Author review, canon versioning, profiles, and bystanders

**Files:**
- Create: `src/novel_harness/extract/proposals.py`
- Modify: `src/novel_harness/project.py`
- Modify: `src/novel_harness/decisions.py`
- Create: `tests/test_event_proposals.py`
- Modify: `tests/test_project.py`
- Modify: `tests/test_decisions.py`

- [x] **Step 1: Write review transaction tests**

  Define request/response contracts:

  ```python
  class ProposalAction(StrEnum):
      ACCEPT = "accept"
      REJECT = "reject"
      EDIT = "edit"
      BYSTANDER = "bystander"

  class ProposalReview(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")
      action: ProposalAction
      expected_canon_version: int = Field(ge=0)
      edited_summary: str | None = None

  class ProposalResolution(BaseModel):
      model_config = ConfigDict(frozen=True)
      proposal_id: str
      status: Literal["ACCEPTED", "REJECTED", "EDITED"]
      canon_version: int
      decision_id: str
      event: EventView | None = None
      character: NodeRef | None = None
  ```

  Assert accept clones PROVISIONAL→CANON and bumps once; edit changes only the CANON summary and bumps once; reject and bystander do not create CANON/bump; new-character accept creates a Character with the proposed profile and canonical alias; bystander creates none; stale base gives `StaleBaseVersion` with zero writes; duplicate resolution gives `ProposalAlreadyResolved` with no second bump; and every decision payload contains textual names, summary/profile, original quote and hash rather than only IDs.

  Also test `confirm_provisional_event` and `confirm_provisional_edges`: each is an explicit author action, performs stale-version checking, clones/upserts only the selected PROVISIONAL facts into CANON, records `PROPOSAL_REVIEW` with `kind='provisional_confirm'`, and bumps canon version once per submitted batch.

- [x] **Step 2: Run tests and verify RED**

  Run: `uv run pytest tests/test_event_proposals.py tests/test_project.py tests/test_decisions.py -q`

  Expected: missing review service and canon-version compare-and-bump.

- [x] **Step 3: Implement atomic review**

  `review_proposal(conn, graph, events, proposal_id, review)` uses one `BEGIN IMMEDIATE` transaction to load PENDING proposal, compare both proposal base and request expected version with `project.canon_version`, create CANON data, update proposal status, and increment the project exactly once. After successful business commit, append `DecisionKind.PROPOSAL_REVIEW`; then attach its ID to the resolved proposal in a narrow follow-up update. If decision logging fails, return a loud `DecisionAuditError` and leave the resolved proposal recoverably detectable by `decision_log_id IS NULL`; never roll back a committed canon change by deleting data.

- [x] **Step 4: Run review tests**

  Run: `uv run pytest tests/test_event_proposals.py tests/test_project.py tests/test_decisions.py -q`

  Expected: all pass.

- [x] **Step 5: Commit**

  ```bash
  git add src/novel_harness/extract/proposals.py src/novel_harness/project.py src/novel_harness/decisions.py tests/test_event_proposals.py tests/test_project.py tests/test_decisions.py
  git commit -m "feat: review M4 proposals into canon"
  ```

### Task 6: Safe event-memory context and product drafting

**Files:**
- Create: `src/novel_harness/draft/product_context.py`
- Create: `src/novel_harness/draft/product_assemble.py`
- Modify: `src/novel_harness/api/app.py`
- Create: `tests/test_product_context.py`
- Create: `tests/test_product_assemble.py`
- Modify: `tests/test_draft_api.py`
- Modify: `tests/test_no_chapter_input.py`

- [x] **Step 1: Write retrieval and prompt tests**

  Required immutable output:

  ```python
  class ResolvedProductContext(BaseModel):
      model_config = ConfigDict(frozen=True)
      cast: list[NodeRef]
      profiles: list[CharacterProfileView]
      recent_events: list[EventView]
      background_events: list[EventView]
  ```

  Prove retrieval includes only CANON events with `event.chapter_number < draft_chapter`, at least one participant in cast, and every cast member in knowers. Prove it excludes PROVISIONAL/PLANNED/REJECTED, future/same-chapter events, stale evidence, and a secret known by only part of the cast. Prove deterministic previous-8-chapter and 12-older-event limits/order.

  `assemble_product()` must call the existing `draft.assemble.assemble()` unchanged and prepend one system message headed `已确认的故事记忆`; profile/event text must not appear in X0/X1/X2 kill-gate calls.

- [x] **Step 2: Run tests and verify RED**

  Run: `uv run pytest tests/test_product_context.py tests/test_product_assemble.py tests/test_draft_api.py tests/test_no_chapter_input.py -q`

  Expected: missing product context modules/new product draft form.

- [x] **Step 3: Implement deterministic context**

  Retrieve via `EventStore.events_for_characters`, partition recent/background in Python after the SQL's stable ordering, and serialize names/summaries only. Add API `DraftRequest.form='PRODUCT'`; PRODUCT uses `assemble_product`, while X0/X1/X2 execute their exact prior code path.

- [x] **Step 4: Run draft regression suite**

  Run: `uv run pytest tests/test_product_context.py tests/test_product_assemble.py tests/test_draft_assemble.py tests/test_draft_context.py tests/test_draft_api.py tests/test_draft_boundary.py tests/test_no_chapter_input.py -q`

  Expected: all pass.

- [x] **Step 5: Commit**

  ```bash
  git add src/novel_harness/draft src/novel_harness/api/app.py tests/test_product_context.py tests/test_product_assemble.py tests/test_draft_api.py tests/test_no_chapter_input.py
  git commit -m "feat: add safe event memory to product drafts"
  ```

### Task 7: Extraction and proposal HTTP API

**Files:**
- Modify: `src/novel_harness/api/deps.py`
- Modify: `src/novel_harness/api/app.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_frontend_contract.py`
- Modify: `frontend/src/__fixtures__/api.json` (generated only)

- [x] **Step 1: Write real API tests before replacing M4 stubs**

  Cover:

  ```text
  POST /api/projects/{pid}/chapters/{n}/extract -> 202 ExtractionRun
  GET  /api/projects/{pid}/extractions/{run_id} -> ExtractionRun
  GET  /api/projects/{pid}/chapters/{n}/events?scope=PROVISIONAL|CANON -> EventView[]
  POST /api/projects/{pid}/chapters/{n}/provisional/confirm -> ProposalResolution
  GET  /api/projects/{pid}/chapters/{n}/proposals?status=PENDING -> ProposalList
  POST /api/projects/{pid}/proposals/{id}/accept -> ProposalResolution
  POST /api/projects/{pid}/proposals/{id}/reject -> ProposalResolution
  ```

  Assert extract is explicit (no save/import endpoint invokes it), unknown project/chapter/proposal gives 404, invalid scope/action gives 422, stale/double review gives 409, provider failure is visible through run status rather than a delayed 500, and all endpoints use the same request connection for business stores. Preserve the existing `/accept` route path while replacing its 501 body; add `/reject` for reject/bystander. Retain only the three M2 501 cases in the old stub parameterization.

- [x] **Step 2: Run API tests and verify RED**

  Run: `uv run pytest tests/test_api.py -q`

  Expected: M4 routes still return 501/404.

- [x] **Step 3: Implement narrow routes and exception mapping**

  Add `get_event_store(conn=Depends(get_conn))`; make `get_conn` request-cached (FastAPI already caches identical dependencies) and do not open a second connection inside a review request. Routes only validate/load/call services and dump Pydantic models; no SQL belongs in `api/app.py`.

- [x] **Step 4: Regenerate and inspect the real frontend contract**

  Run: `NH_UPDATE_FIXTURES=1 uv run pytest tests/test_frontend_contract.py -q`

  Expected: PASS and a reviewed diff adding real event/proposal payloads. Never hand-edit `frontend/src/__fixtures__/api.json`.

- [x] **Step 5: Run API/contract tests**

  Run: `uv run pytest tests/test_api.py tests/test_frontend_contract.py -q`

  Expected: all pass.

- [x] **Step 6: Commit**

  ```bash
  git add src/novel_harness/api tests/test_api.py tests/test_frontend_contract.py frontend/src/__fixtures__/api.json
  git commit -m "feat: expose M4 extraction review API"
  ```

### Task 8: Review tab and provisional event UI

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/hooks.ts`
- Modify: `frontend/src/store.ts`
- Create: `frontend/src/components/ProposalReviewTab.tsx`
- Create: `frontend/src/components/ProposalReviewTab.test.tsx`
- Create: `frontend/src/components/StateCards.tsx`
- Create: `frontend/src/components/StateCards.test.tsx`
- Modify: `frontend/src/components/RightPanel.tsx`
- Modify: `frontend/src/styles.css`

- [x] **Step 1: Write component tests first**

  Consume the generated fixture. Assert a `待确认 · N` tab; conflict cards render current vs proposed; low-confidence cards render confidence/names/evidence; new-character cards expose `接受为角色` and `标为路人`; resolved mutations invalidate proposal/event/roster/state queries; provisional events render gray with a `未确认` label and offer explicit selected/batch confirmation; and no component displays raw `items_json`.

- [x] **Step 2: Run Vitest and verify RED**

  Run: `npm test -- --run frontend/src/components/ProposalReviewTab.test.tsx frontend/src/components/StateCards.test.tsx`

  Expected: missing components/tab/types.

- [x] **Step 3: Add typed hooks and the review UI**

  Type the narrowed server view (`ProposalKind`, `ProposalTrigger`, `ProposalAction`, `ProposalList`, `ProposalResolution`, `EventView`). Add `useProposals`, `useEvents`, `useStartExtraction`, `useExtractionRun`, and `useReviewProposal`. Keep server data out of Zustand; only add `review` to `Tab`.

- [x] **Step 4: Run frontend tests and typecheck/build**

  Run: `npm test -- --run && npm run build`

  Expected: all Vitest tests and TypeScript/Vite build pass.

- [x] **Step 5: Commit**

  ```bash
  git add frontend/src
  git commit -m "feat: add M4 proposal review panel"
  ```

### Task 9: Documentation, acceptance metrics, and full verification

**Files:**
- Create: `src/novel_harness/extract/metrics.py`
- Create: `tests/test_extract_metrics.py`
- Modify: `docs/UI_ARCHITECTURE.md`
- Modify: `docs/PLAN.md`
- Modify: `docs/M4_DESIGN.md`
- Modify: `README.md`
- Modify: `tests/test_doc_numbers.py`

- [x] **Step 1: Write metric tests**

  `metrics_for_range(conn, project_id, first_chapter, last_chapter)` returns total/succeeded/failed runs, valid/discarded event counts, pending/resolved proposal counts, conflicts per chapter, and accepted/(accepted+edited+rejected) review rate. Assert a three-chapter fixture computes `max_conflicts_per_chapter <= 2` and acceptance `> 0.60` without treating bystanders as accepted events.

- [x] **Step 2: Run the metric test and verify RED**

  Run: `uv run pytest tests/test_extract_metrics.py -q`

  Expected: missing metrics module.

- [x] **Step 3: Implement metrics and update docs**

  Mark the two M4 501 routes implemented, document explicit background extraction, the independent event hypergraph, strict CANON writer safety, deterministic rolling background, proposal actions, and the exact three-chapter acceptance command/data needed before declaring the milestone passed. Reconcile old UI text that permanently cut Event nodes by saying M4 adds an independent hyperedge table, not `NodeLabel.Event` or causal edge types.

- [x] **Step 4: Run all verification from a clean status snapshot**

  Run:

  ```bash
  uv run ruff check .
  uv run pytest -q
  npm test -- --run
  npm run build
  git diff --check
  ```

  Expected: lint passes, all backend/frontend tests pass, build succeeds, and diff check is empty. The pre-existing Starlette deprecation warning is recorded but no new warning is introduced.

- [x] **Step 5: Commit**

  ```bash
  git add src/novel_harness/extract/metrics.py tests/test_extract_metrics.py docs README.md tests/test_doc_numbers.py
  git commit -m "docs: complete M4 event memory slice"
  ```

- [x] **Step 6: Request final spec and quality review**

  Review the complete branch against `docs/M4_DESIGN.md`, PLAN M4 acceptance criteria, ADR 0002/0004/0005/0006, and the test-first commit history. Fix every important finding, rerun the full verification block, and only then report completion.
