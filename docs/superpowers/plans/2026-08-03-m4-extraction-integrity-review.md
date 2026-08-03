# M4 Extraction Integrity Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Task4b quality-review gaps so every paid analysis call is audited or terminally failed, prompt bytes cannot drift from analyzer input, and same-key state updates deterministically use the last validated item.

**Architecture:** Put immutable request and strict completion-audit types in `extract/control.py`; keep runner orchestration in `extract/runner.py`. Prepare and group state updates before any graph write in `extract/service.py`, using pure resolved-key helpers in `extract/ingest_helpers.py`. Keep all graph persistence behind the existing stores.

**Tech Stack:** Python 3.12, Pydantic 2, SQLite, pytest, Ruff.

---

### Task 1: Prompt-bound request and terminal paid-call audit

**Files:**
- Modify: `src/novel_harness/extract/control.py`
- Modify: `src/novel_harness/extract/runner.py`
- Modify: `tests/test_extract_runner.py`

- [ ] **Step 1: Write failing request/audit tests**

  Add tests whose analyzer receives an `AnalysisRequest`, and assert:

  ```python
  request.chapter.snapshot_id == queued.snapshot_id
  request.wire_messages() == build_analysis_messages(request.chapter.text)
  artifact_id(request.prompt_bytes) == model_call.in_artifact
  ```

  Add a prompt-drift test that monkeypatches the runner's request builder after enqueue and asserts `FAILED/prompt_drift` before analyzer invocation. Add parameterized completion tests for lone-surrogate `model`/`text`, negative/bool tokens, and a call-id failure; each must end `FAILED/call_record_failure`, stay generic, and never call the analyzer twice.

- [ ] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run pytest tests/test_extract_runner.py -q
  ```

  Expected: analyzer argument assertions fail because it still receives `ChapterText`; malformed completion/call recording raises or leaves `RUNNING`; prompt drift is not detected.

- [ ] **Step 3: Implement immutable request and strict audit**

  Add a frozen `AnalysisRequest` whose constructor accepts only a chapter and derives canonical tuple messages, bytes, and SHA-256:

  ```python
  class AnalysisRequest(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")
      chapter: ChapterText
      messages: tuple[AnalysisMessage, ...]
      prompt_bytes: bytes
      prompt_hash: str

      @classmethod
      def for_chapter(cls, chapter: ChapterText) -> AnalysisRequest: ...
      def wire_messages(self) -> list[AnalysisMessage]: ...
  ```

  Revalidate all model-returned fields into a frozen strict audit type. Strings must encode as strict UTF-8; tokens must be `None` or non-bool, non-negative integers. In `run()`, build exactly one request, compare its hash with the persisted hash before calling the analyzer, and use its bytes for `in_artifact`. Catch only `Exception` around validation plus call recording and mark the run:

  ```python
  ExtractionRunError(
      code="call_record_failure",
      message="chapter analysis call could not be audited",
  )
  ```

  Leave `BaseException` uncaught at business boundaries.

- [ ] **Step 4: Verify GREEN**

  Run:

  ```bash
  uv run pytest tests/test_extract_runner.py -q
  ```

  Expected: all runner tests pass.

### Task 2: Resolve-first last-wins state grouping

**Files:**
- Modify: `src/novel_harness/extract/ingest_helpers.py`
- Modify: `src/novel_harness/extract/service.py`
- Modify: `tests/test_extract_service.py`

- [ ] **Step 1: Write three failing last-wins tests**

  For two validated updates sharing each resolved key, assert only the second update writes:

  ```python
  assert report.valid_state_update_count == 1
  assert report.discarded[0].outcome is DiscardOutcome.SUPERSEDED_IN_ANALYSIS
  assert final_edge.status is EdgeStatus.ACTIVE
  assert final_edge.props.value == "second"
  assert evidence.audit.quote_text == second_quote
  ```

  Cover:

  - `(LOCATED_AT, subject_id)`
  - `(HAS_STATE, subject_id, dimension_id)`
  - `(RELATED_TO, min(character_ids), max(character_ids))`

  Seed CANON conflicts where useful and assert proposal links/items mention only the final edge/value/quote, with no retracted provisional edge.

- [ ] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run pytest tests/test_extract_service.py -q
  ```

  Expected: both items currently write; the first provisional edge becomes retracted or remains linked, and valid counts are too high.

- [ ] **Step 3: Implement prepare/group/write**

  Add a frozen internal prepared-update model holding original index, raw update, resolved IDs, and located source anchor. Resolve labels and locate evidence for every item first. Derive keys only from server-resolved IDs:

  ```python
  if raw.kind == "location":
      key = (EdgeType.LOCATED_AT.value, subject_id)
  elif raw.kind == "state":
      key = (EdgeType.HAS_STATE.value, subject_id, target_id)
  else:
      key = (EdgeType.RELATED_TO.value, *sorted((subject_id, target_id)))
  ```

  Retain the last prepared item per key. Emit `SUPERSEDED_IN_ANALYSIS` for earlier prepared items and call the existing write/proposal path only for retained items.

- [ ] **Step 4: Verify GREEN**

  Run:

  ```bash
  uv run pytest tests/test_extract_service.py -q
  ```

  Expected: all service tests pass.

### Task 3: Explicit input and ID contracts

**Files:**
- Modify: `src/novel_harness/ids.py`
- Modify: `src/novel_harness/extract/runner.py`
- Modify: `tests/test_ids.py`
- Modify: `tests/test_extract_runner.py`

- [ ] **Step 1: Write failing validation and ID tests**

  Parameterize `enqueue(..., chapter_number=value)` with `True`, `1.0`, `0`, and `-1`; assert `TypeError` for non-integers/bool and `ValueError` below one. Assert the default run ID begins with `extraction_run:<project-short>:`.

- [ ] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run pytest tests/test_extract_runner.py tests/test_ids.py -q
  ```

  Expected: bool/float reaches SQLite and the default prefix remains `report:`.

- [ ] **Step 3: Implement the narrow contracts**

  Add:

  ```python
  EXTRACTION_RUN = "extraction_run"
  ```

  to `EntityType`, use it in `_default_run_id`, and reject invalid chapter numbers before opening a connection.

- [ ] **Step 4: Verify GREEN**

  Run the same focused command and expect all selected tests to pass.

### Task 4: Final verification and commit

**Files:**
- Verify all modified files above.

- [ ] **Step 1: Run focused regressions**

  ```bash
  uv run pytest tests/test_extract_service.py tests/test_extract_runner.py tests/test_ids.py tests/test_arch_guard.py tests/test_structured_provider.py tests/test_event_store.py tests/test_proposal_store.py -q
  ```

- [ ] **Step 2: Run full verification**

  ```bash
  uv run pytest -q
  uv run ruff check .
  uv run python -c "import novel_harness.extract.control; import novel_harness.extract.runner; import novel_harness.extract.service"
  git diff --check
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add docs/superpowers/plans/2026-08-03-m4-extraction-integrity-review.md src/novel_harness/ids.py src/novel_harness/extract tests/test_ids.py tests/test_extract_runner.py tests/test_extract_service.py
  git commit -m "fix: harden extraction execution integrity"
  ```
