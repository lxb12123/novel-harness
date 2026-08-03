# Critical Canon Version Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make author Canon declarations advance `canon_version` atomically and make proposal review reject a stale `edge_conflict.current` even when a caller bypasses `Ledger` without advancing the version.

**Architecture:** `Ledger` will compare each declaration's stored before/after value and perform the version CAS inside the existing graph transaction, so a no-op retry does not advance the version and a CAS failure rolls back the graph mutation. The review repository will expose a narrow Canon hydration method; proposal validation will compare each conflict item's claimed current edge with the locked, live Canon edge and reject changed, closed, retracted, stale, cross-project, or textually mismatched state.

**Tech Stack:** Python 3.12, SQLite, Pydantic, pytest, Ruff, uv

---

### Task 1: Ledger Canon mutation/version atomicity

**Files:**
- Modify: `src/novel_harness/declare.py`
- Test: `tests/test_declare.py`

- [x] **Step 1: Write failing real-Ledger tests**

Add tests that record `require_canon_version(conn, pid)`, then assert a new node, alias, and edge each advance it once, while an identical node and edge retry do not. Monkeypatch `project.compare_and_bump_canon_version` to raise and assert that the corresponding graph write is absent after rollback.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest tests/test_declare.py -q`

Expected: version delta assertions fail because declarations currently leave `canon_version` unchanged.

- [x] **Step 3: Implement the minimal Ledger transaction changes**

Import `project`, read the current version after entering `GraphStore.transaction()`, determine whether the declaration changes Canon, and call:

```python
project.compare_and_bump_canon_version(
    self._conn,
    self._project_id,
    current_version,
)
```

before leaving that same transaction. For node and edge upserts, compare the prior typed graph value with the returned value so exact retries are no-ops; alias insertion always changes Canon when it succeeds.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run: `uv run pytest tests/test_declare.py -q`

Expected: all declaration tests pass.

### Task 2: Locked current-Canon edge revalidation

**Files:**
- Modify: `src/novel_harness/graph/review_store.py`
- Modify: `src/novel_harness/graph/sqlite_review.py`
- Modify: `src/novel_harness/extract/proposal_validation.py`
- Modify: `src/novel_harness/extract/proposals.py`
- Test: `tests/test_event_proposals.py`

- [x] **Step 1: Write failing bypass-Ledger regression tests**

Build a real `edge_conflict` proposal from a Canon location edge, bypass `Ledger` by writing a same-chapter replacement directly through `SqliteStoryGraph.upsert_edge`, keep the project version unchanged, and assert `review_proposal` raises `ProposalShapeError`, leaves the proposal pending, performs no promotion, and does not advance the version. Add direct stored-fact mismatch/lifecycle coverage for the current edge contract.

- [x] **Step 2: Run the focused regression and verify RED**

Run: `uv run pytest tests/test_event_proposals.py -q`

Expected: stale conflict acceptance succeeds before the new locked revalidation exists.

- [x] **Step 3: Add the narrow graph-boundary hydration contract**

Add `hydrate_current_canon(project_id, edge_ids)` to `EdgeReviewStore` and implement it in `SqliteEdgeReviewStore`. It must return only same-project Canon edges that are ACTIVE, unclosed, and not STALE, with typed endpoint references; otherwise raise `EdgeReviewValidationError`.

- [x] **Step 4: Compare conflict claims against live Canon facts under the review lock**

Immediately after `_hydrate_cluster` in `review_proposal`, hydrate `EdgeConflictItem.current.edge_id` values while `_business_transaction` holds `BEGIN IMMEDIATE`. Validate type, directional/unordered endpoints, value, and temporal applicability at the proposed edge chapter. Convert repository failures into `ProposalShapeError` before any Canon promotion or proposal resolution; keep audit recovery on its existing hydration path.

- [x] **Step 5: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_declare.py tests/test_event_proposals.py -q`

Expected: all focused tests pass.

### Task 3: Verification and commit

**Files:**
- Verify all files above

- [x] **Step 1: Run formatting and static checks**

Run: `uv run ruff check src tests`

Expected: no violations.

- [x] **Step 2: Run the full suite from a clean fixture state**

Run: `uv run pytest -q`

Expected: all tests pass.

- [x] **Step 3: Inspect the exact diff and commit**

Run: `git diff --check && git status --short && git diff --stat`

Then stage only the scoped files and commit with `fix: guard canon version during author review`.
