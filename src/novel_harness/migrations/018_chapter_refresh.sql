-- 018：快照刷新 —— generation / ruleset 基线 / refresh run（Task 3 起逐步接协调器）。
--
-- ── 为什么要有这一列 ─────────────────────────────────────────────────────
--
-- 保存后所有自动任务（验证 / 总结 / 抽取）都绑定「第几版正文」。没有单调 generation，
-- S1→S2→S1 会让第一轮 S1 的晚到任务通过 snapshot/hash 等值 CAS 复活（ABA）。
-- 判据是 `text_sha256` 精确等值，不是 `snapshot_created`（同内容重存不加，
-- 从 S2 还原历史 S1 要加）。
--
ALTER TABLE chapter ADD COLUMN snapshot_generation INTEGER NOT NULL DEFAULT 0;
UPDATE chapter SET snapshot_generation = 1 WHERE snapshot_generation = 0;

-- ── validation_report 扩展：报告从「给 R2/R3 看一眼」变成快照绑定的闸门 ──
-- 旧行无法诚实反推来源快照：snapshot/generation/attempt/epoch/hash 全留 NULL，
-- phase/gate 标 legacy。新写入一律非空（service 负责）。
ALTER TABLE validation_report ADD COLUMN chapter_snapshot_id TEXT
  REFERENCES chapter_snapshot(id);
ALTER TABLE validation_report ADD COLUMN source_generation INTEGER;
ALTER TABLE validation_report ADD COLUMN refresh_attempt_id TEXT;
ALTER TABLE validation_report ADD COLUMN phase TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE validation_report ADD COLUMN text_sha256 TEXT;
ALTER TABLE validation_report ADD COLUMN ruleset_epoch INTEGER;
ALTER TABLE validation_report ADD COLUMN ruleset_hash TEXT;
ALTER TABLE validation_report ADD COLUMN gate TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE validation_report ADD COLUMN rules_json TEXT NOT NULL DEFAULT '[]';

-- ── 项目级规则集基线 ─────────────────────────────────────────────────────
-- epoch/hash 的唯一真值在 `validation_ruleset_state`（Task 5 冻结 attempt 的依据）。
-- 既有项目回填 epoch=1 + SYSTEM_RULESET_V1_HASH；`project.create()` 在创建项目的
-- 同一事务插入同样基线。hash 的语义编码见 `checks/catalog.py`，迁移测试断言下面
-- 这个字面量与常量一致——**改它 = 改历史，必须先开新迁移递增 epoch**。
CREATE TABLE validation_ruleset_state (
  project_id   TEXT PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
  epoch        INTEGER NOT NULL CHECK (epoch >= 1),
  ruleset_hash TEXT NOT NULL,
  updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
INSERT INTO validation_ruleset_state (project_id, epoch, ruleset_hash)
SELECT id, 1, 'eebe10055a2487574ea5ae03f1fcd0ac14211301dff6f46ea136482d91b2b166'
FROM project;

-- ── 固定刷新流程的 outbox 父表 ────────────────────────────────────────────
-- 一个 (project, chapter, generation) 一行；与「新 generation 的 chapter/snapshot
-- 指针」同事务插入。不能只按 snapshot 唯一：S1→S2→S1 复用旧 run 会让第一轮 S1 的
-- 晚到任务借 ABA 复活。run 记录本次退休的机器事实 ID 与 canon version before/after
-- （Task 3 起由 commit_chapter_snapshot 写；零 Canon 变化时 before==after）。
CREATE TABLE chapter_refresh_run (
  id                          TEXT PRIMARY KEY,
  project_id                  TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_id                  TEXT NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
  source_snapshot_id          TEXT NOT NULL REFERENCES chapter_snapshot(id),
  source_generation           INTEGER NOT NULL CHECK (source_generation >= 1),
  workflow_version            INTEGER NOT NULL DEFAULT 1,
  retired_edge_ids_json       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(retired_edge_ids_json)),
  retired_event_ids_json      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(retired_event_ids_json)),
  retired_knower_event_ids_json TEXT NOT NULL DEFAULT '[]'
                              CHECK (json_valid(retired_knower_event_ids_json)),
  canon_version_before        INTEGER,
  canon_version_after         INTEGER,
  created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (project_id, chapter_id, source_generation)
);

-- 既有 chapter 回填 synthetic legacy run（generation=1）；不伪造 PASSED attempt，
-- 首次显式重整/新保存再建真实 attempt。id 用 'refresh:legacy:' 前缀标记迁移数据。
INSERT INTO chapter_refresh_run
  (id, project_id, chapter_id, source_snapshot_id, source_generation,
   canon_version_before, canon_version_after)
SELECT 'refresh:legacy:' || c.id, c.project_id, c.id, s.id, 1, p.canon_version, p.canon_version
FROM chapter c
JOIN project p ON p.id = c.project_id
JOIN chapter_snapshot s ON s.chapter_id = c.id AND s.text_sha256 = c.text_sha256;

-- ── 子表：一次刷新 attempt（Task 5 的协调器消费）────────────────────────
-- 唯一键的三种形状：
--   自动 save/reconcile：一个 (run, workflow_version, ruleset_epoch, trigger_kind)
--                         只允许一个未完成 attempt；
--   同 hash coverage：    (run, workflow_version, ruleset_epoch, 'coverage',
--                         missing_branch_mask) 幂等复用；
--   手动「重新整理」：    带唯一 trigger_key，不能被父唯一键吞掉。
-- 初次 PASSED 不能直接令 final gate PASSED：只有 alias 阶段确认未变化/失败前未落
-- alias，或 post-alias report 完成后，才原子填写 final report/state。
CREATE TABLE chapter_refresh_attempt (
  id                          TEXT PRIMARY KEY,
  run_id                      TEXT NOT NULL REFERENCES chapter_refresh_run(id)
                              ON DELETE CASCADE,
  workflow_version            INTEGER NOT NULL DEFAULT 1,
  ruleset_epoch               INTEGER NOT NULL CHECK (ruleset_epoch >= 1),
  ruleset_hash                TEXT NOT NULL,
  trigger_kind                TEXT NOT NULL
                              CHECK (trigger_kind IN
                                ('save','reconcile','manual','ruleset','coverage','replay')),
  trigger_key                 TEXT NOT NULL,
  missing_branch_mask         INTEGER NOT NULL DEFAULT 0,
  expected_summary_head       TEXT,
  initial_validation_report_id TEXT,
  alias_phase                 TEXT NOT NULL DEFAULT 'NOT_STARTED'
                              CHECK (alias_phase IN
                                ('NOT_STARTED','UNCHANGED','CHANGED','FAILED_BEFORE_CHANGE','COMPLETE')),
  final_gate_state            TEXT NOT NULL DEFAULT 'PENDING'
                              CHECK (final_gate_state IN ('PENDING','PASSED','BLOCKED','ERROR')),
  final_validation_report_id  TEXT,
  summary_candidate_ready     INTEGER NOT NULL DEFAULT 0,
  extraction_candidate_ready  INTEGER NOT NULL DEFAULT 0,
  validation_state            TEXT NOT NULL DEFAULT 'PENDING'
                              CHECK (validation_state IN
                                ('PENDING','RUNNING','SUCCEEDED','REUSED','BLOCKED','FAILED','SUPERSEDED')),
  summary_state               TEXT NOT NULL DEFAULT 'PENDING'
                              CHECK (summary_state IN
                                ('PENDING','RUNNING','SUCCEEDED','REUSED','BLOCKED','FAILED','SUPERSEDED')),
  extraction_state            TEXT NOT NULL DEFAULT 'PENDING'
                              CHECK (extraction_state IN
                                ('PENDING','RUNNING','SUCCEEDED','REUSED','BLOCKED','FAILED','SUPERSEDED')),
  reused_validation_report_id TEXT,
  reused_summary_version_id   TEXT,
  reused_application_id       TEXT,
  not_before                  TEXT,
  lease_owner                 TEXT,
  lease_expires_at            TEXT,
  fencing_token               INTEGER NOT NULL DEFAULT 0,
  created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (run_id, workflow_version, ruleset_epoch, trigger_kind, trigger_key)
);
CREATE UNIQUE INDEX idx_refresh_attempt_save
  ON chapter_refresh_attempt(run_id, workflow_version, ruleset_epoch, trigger_kind)
  WHERE trigger_kind IN ('save','reconcile');
CREATE UNIQUE INDEX idx_refresh_attempt_coverage
  ON chapter_refresh_attempt(run_id, workflow_version, ruleset_epoch, trigger_kind, missing_branch_mask)
  WHERE trigger_kind = 'coverage';

PRAGMA user_version = 18;
