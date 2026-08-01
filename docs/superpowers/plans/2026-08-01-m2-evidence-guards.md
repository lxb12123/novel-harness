# M2 Evidence Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve M2 experiment evidence by rejecting stale derived ground truth and refusing to overwrite any existing run JSONL before the first real 225-generation run.

**Architecture:** Extend the existing exact, non-semantic condition-2 selfcheck to compare all trap source fields. Change the runner's file creation mode from truncating creation to exclusive creation and translate collisions into the runner's existing `ValueError` contract.

**Tech Stack:** Python 3.12, Pydantic, pytest, Typer, uv, Git

---

### Task 1: Add failing freshness regression

**Files:**
- Modify: `tests/test_synth.py`
- Test: `tests/test_synth.py`

- [x] **Step 1: Write the failing test**

Add a test that builds the valid fixture, edits only K01's `goal` in `booklet.toml` after the build, calls `selfcheck()`, and asserts that condition 2 names `K01` and `goal` as stale.

- [x] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_synth.py::test_selfcheck_catches_source_fields_changed_after_build`

Expected: FAIL because the current selfcheck returns no condition-2 problem for the changed goal.

### Task 2: Implement complete source-field comparison

**Files:**
- Modify: `synth/leak_selfcheck.py`
- Test: `tests/test_synth.py`

- [x] **Step 1: Compare every source field**

Compare `kind`, `chapter`, `cast`, `target`, `goal`, `prior`, and `reference` for the booklet trap and matching ground-truth trap. Emit one condition-2 message per mismatching field and continue the existing derived-boundary checks.

- [x] **Step 2: Verify GREEN**

Run: `uv run pytest -q tests/test_synth.py::test_selfcheck_catches_source_fields_changed_after_build tests/test_synth.py::test_selfcheck_catches_a_stale_ground_truth tests/test_synth.py::test_selfcheck_passes_a_well_formed_booklet`

Expected: `6 passed`.

### Task 3: Add failing no-overwrite regression

**Files:**
- Modify: `tests/test_runner.py`
- Test: `tests/test_runner.py`

- [x] **Step 1: Write the failing test**

Create an output file containing `SENTINEL_EXISTING_EVIDENCE`, call `run_gate()` with the existing real store and fake client, assert `ValueError` mentions that the run file already exists, assert zero client calls, and assert the sentinel bytes are unchanged.

- [x] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_runner.py::test_run_gate_refuses_to_overwrite_existing_evidence`

Expected: FAIL because the current `open("w")` truncates the sentinel and runs the model.

### Task 4: Implement exclusive run-file creation

**Files:**
- Modify: `src/novel_harness/eval/runner.py`
- Test: `tests/test_runner.py`

- [x] **Step 1: Use exclusive creation**

Open `out_path` with mode `"x"`. Catch `FileExistsError` around the open operation and raise an actionable `ValueError` while preserving the original exception as the cause. Keep per-line flush behavior unchanged.

- [x] **Step 2: Verify GREEN**

Run: `uv run pytest -q tests/test_runner.py::test_run_gate_refuses_to_overwrite_existing_evidence tests/test_runner.py::test_one_full_round`

Expected: `2 passed`.

### Task 5: Synchronize documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/UI_ARCHITECTURE.md`
- Modify: `docs_dev/2026-07-25-架构现状与完成度快照.md` (activity board section only)
- Modify as required by exact searches: `README.md`, `CLAUDE.md`, `frontend/README.md`

- [x] **Step 1: Update evidence behavior**

Document full source-field freshness comparison and exclusive JSONL creation. Correct `assemble.py` status, the 15/16 CLI count drift, the amendment-4 checkbox, and obsolete module line counts.

- [x] **Step 2: Update the authoritative test count**

After the full suite runs, update only `docs/ARCHITECTURE.md` with the fresh pytest count; leave pointer documents number-free.

### Task 6: Verify and make the pre-run commit

**Files:**
- Stage all current M2 implementation, preregistration, tests, and synchronized documentation; exclude ignored generated artifacts and secrets.

- [x] **Step 1: Run full verification**

Run:

```bash
uv run ruff check .
uv run pytest -q
cd frontend && npm test && npm run build
cd .. && uv build
```

Expected: lint, all Python tests, 18 frontend tests, frontend build, sdist, and wheel all pass.

- [x] **Step 2: Review the staged diff**

Confirm no API key, generated `ground_truth.json`, `gate.db`, chapter copies, web build output, or run result is staged. Confirm the frozen protocol body still matches commit `0393088`.

- [x] **Step 3: Commit before any real run**

Stage the explicit M2 asset set and commit it. Verify `git status` and record the commit hash; this hash is the evidence that amendment 4 and ADR 0010 predate the first result.

### Task 7: Discover endpoint and run the registered experiment

**Files:**
- Create after the pre-run commit: `runs/<UTC timestamp>.jsonl`

- [ ] **Step 1: Validate endpoint without spending the experiment budget**

Use a reachable local OpenAI-compatible endpoint if one is already installed and has a listed model. Otherwise require explicit `NH_LLM_BASE_URL`, `NH_LLM_MODEL`, and `NH_LLM_API_KEY`; do not infer a paid provider or credential.

- [x] **Step 2: Run the selfcheck**

Run: `uv run python -m synth.leak_selfcheck`

Expected: `自检全绿，放行。`

- [ ] **Step 3: Run the 225 generations**

With the frozen endpoint configuration, run:

```bash
uv run nh gate --db synth/gate.db -p project:01KYV174HMJ8BQD47KRF6K6FQK --ground-truth synth/ground_truth.json
```

Expected: 25 traps x 3 arms x 3 repeats = 225 generation records plus one header, 25 references, and 25 confound records.

### Task 8: Audit JSONL and record ADR 0009

**Files:**
- Create: `docs/adr/0009-m2-kill-gate-verdict.md`
- Modify: `docs/adr/README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs_dev/2026-07-25-架构现状与完成度快照.md` (activity board section only)

- [ ] **Step 1: Audit the JSONL structurally**

Verify exactly 276 JSONL lines: 1 header, 25 references, 225 generations, and 25 confound reports. Verify all 25 trap IDs, arms `x0/x1/x2`, repeats `0/1/2`, full messages, model/config metadata without API keys, and `confound.ok=true` for every trap.

- [ ] **Step 2: Recompute and transcribe the preregistered verdict**

Use the committed scoring code and the JSONL records to reproduce the CLI's floor/ceiling checks, comparisons, Holm-adjusted p-values, sign stability, eligible arms, and final `Verdict`. Do not reinterpret thresholds.

- [ ] **Step 3: Write and verify ADR 0009**

Record the pre-run commit hash, run filename, endpoint/model metadata excluding credentials, exact metrics, matched decision-table rule, verdict, action, caveats, and amendment-4 disclosure. Synchronize milestone status according to the verdict, rerun full verification, and commit the JSONL plus ADR and status updates.
