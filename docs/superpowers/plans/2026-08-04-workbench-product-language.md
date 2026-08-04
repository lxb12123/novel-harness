# Workbench Product Language Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove experiment language and fixture content from the workbench while constraining navigation to real chapters.

**Architecture:** Keep API contracts and internal enums unchanged. Translate them at the React presentation boundary, use `/chapters` as the only navigation source, and render focused empty states when project data is absent.

**Tech Stack:** React, TypeScript, Zustand, TanStack Query, Vitest, Testing Library, Python source guards.

---

### Task 1: Real chapter navigation

**Files:**
- Modify: `frontend/src/components/TopBar.tsx`
- Modify: `frontend/src/components/TopBar.test.tsx`
- Modify: `frontend/src/components/ChapterPrepPage.tsx`

- [ ] Add a failing TopBar test asserting there is no chapter number spinbutton, the chapter selector contains only fixture chapters, and no example character names or implementation hint is rendered.
- [ ] Run `npm test -- --run src/components/TopBar.test.tsx` and confirm the new assertion fails.
- [ ] Read chapters with `useChapters(projectId)`, render a labelled `<select>` from those rows, clamp stale chapter coordinates to the first returned chapter, remove the disabled AI-planning control, and replace cast placeholders with instructions.
- [ ] Apply the same neutral cast placeholder to `ChapterPrepPage`.
- [ ] Run the focused tests and commit.

### Task 2: Blank-project content hierarchy

**Files:**
- Modify: `frontend/src/components/LeftRail.tsx`
- Modify: `frontend/src/components/LeftRail.test.tsx`
- Modify: `frontend/src/components/RightPanel.tsx`
- Modify: `frontend/src/components/BottomBar.tsx`
- Test: `frontend/src/components/StateCards.test.tsx`

- [ ] Add failing tests for the short roster action, the single right-panel empty state when the roster is empty, and the absence of an empty bottom timeline.
- [ ] Run the focused tests and confirm they fail for the current prototype copy.
- [ ] Replace the left-rail implementation explanation with “还没有人物或设定。添加第一个条目”.
- [ ] Gate the right-panel tabs behind a non-empty roster and show one author-facing explanation otherwise.
- [ ] Return `null` from BottomBar when neither scenes nor a selected node provides content.
- [ ] Run the focused tests and commit.

### Task 3: Translate advanced workbench panels

**Files:**
- Modify: `frontend/src/components/RightPanel.tsx`
- Modify: `frontend/src/components/KnowledgeMatrix.tsx`
- Modify: `frontend/src/components/KnowledgeMatrix.test.tsx`
- Modify: `frontend/src/components/LocalGraph.tsx`
- Modify: `frontend/src/components/EvidenceTab.tsx`
- Modify: `frontend/src/components/BottomBar.tsx`
- Modify: `frontend/src/components/DeclareDrawer.tsx`
- Modify: `frontend/src/components/ChapterPrepPage.tsx`

- [ ] Add failing assertions that rendered panels contain no `R4`, `must_not_reveal`, `valid_from`, raw relationship enums, `chN`, rule IDs, or fail-closed terminology.
- [ ] Run the focused tests and confirm the old copy fails them.
- [ ] Rename tabs and constraints using author language; summarize checks without raw rule names or issue codes.
- [ ] Translate graph edges, evidence chapter labels, declaration receipts, timeline labels, and chapter-preparation constraints.
- [ ] Run the focused tests and commit.

### Task 4: Remove experiment and fixture copy from authoring controls

**Files:**
- Modify: `frontend/src/components/DraftDrawer.tsx`
- Modify: `frontend/src/components/DraftDrawer.test.tsx`
- Modify: `frontend/src/components/DraftLengthControls.tsx`
- Modify: `frontend/src/components/DraftLengthControls.test.tsx`
- Modify: `frontend/src/components/SettingsDrawer.tsx`
- Modify: `frontend/src/components/RosterDrawer.tsx`
- Modify: `frontend/src/components/RosterDrawer.test.tsx`
- Modify: `frontend/src/api/types.ts`

- [ ] Add failing tests that user-visible controls contain no M2/ADR/kill-gate/X-arm text or fixture names.
- [ ] Run the focused tests and confirm the assertions fail.
- [ ] Keep the single production drafting form internally, remove experiment selectors and diagnostics, use neutral placeholders, and simplify roster/settings help.
- [ ] Run the focused tests and commit.

### Task 5: Product-language guard and full verification

**Files:**
- Create: `tests/test_frontend_product_language.py`
- Modify: `docs/ARCHITECTURE.md`

- [ ] Add a guard that scans production TSX user-copy literals for the banned terminology and fixture names while allowing API property access and test files.
- [ ] Run the guard and remove any remaining user-visible leaks it reports.
- [ ] Update documented TypeScript and test counts.
- [ ] Run `uv run pytest -q`, `uv run ruff check .`, `npm test`, `npm run build`, and `git diff --check`.
- [ ] Commit the verified cleanup and refresh the local preview.
