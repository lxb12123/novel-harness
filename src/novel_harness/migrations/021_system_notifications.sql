-- 021：只告警的总结核对 + 统一系统通知（ADR 0030 / 计划 Task 10）。
--
-- ── 三种「验证」的分工（§1）───────────────────────────────────────────────
--   章节结构预检   → 写盘前，失败拒绝保存        （Task 2）
--   正文验证器规则 → 快照提交后，blocked 停下游    （Task 4）
--   总结核对       → 总结生效后，**只写通知**      （本迁移）
-- 核对永远不能撤销总结、阻止使用或修改 Canon —— 它是仅仅告警的窄能力。
--
-- ── 持久 outbox 为什么非建不可（不变量 9 / 20 / 29）────────────────────────
-- 「current snapshot / head / evidence fingerprint 改变」和「要核对这件事」
-- 必须在一个事务里提交；进程在两步之间退出，重启后 dispatcher 仍能补上。
-- 禁止「状态提交后再内存 enqueue」。

-- ══════════════════════════════════════════════════════════════════════════
-- 1. model_call 两阶段审计（不变量 30）
-- ── 一次「真正准备发出的 provider attempt」先落 RUNNING，成功/失败/崩溃分别
--    finalize；重试新增行，不覆盖旧账。旧行回填 SUCCEEDED。
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE model_call ADD COLUMN call_state TEXT NOT NULL DEFAULT 'SUCCEEDED'
  CHECK (call_state IN ('RUNNING','SUCCEEDED','FAILED','ABANDONED'));
ALTER TABLE model_call ADD COLUMN provider_name TEXT;
ALTER TABLE model_call ADD COLUMN profile_name TEXT;
ALTER TABLE model_call ADD COLUMN error_type TEXT;
ALTER TABLE model_call ADD COLUMN error_message TEXT;
ALTER TABLE model_call ADD COLUMN finished_at TEXT;

-- ══════════════════════════════════════════════════════════════════════════
-- 2. summary_reconciliation：核对 run + 每次 transport attempt 的链接
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE summary_reconciliation_run (
  id                        TEXT PRIMARY KEY,
  project_id                TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event')),
  subject_id                TEXT NOT NULL,
  chapter_number            INTEGER,
  checked_against_snapshot_id TEXT NOT NULL REFERENCES chapter_snapshot(id),
  source_generation         INTEGER CHECK (source_generation >= 1),
  source_sha256             TEXT NOT NULL,
  summary_sha256            TEXT,
  checker_schema            TEXT,
  checker_prompt_hash       TEXT,
  status                    TEXT NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','SUPERSEDED')),
  verdict                   TEXT CHECK (verdict IN ('supported','possible_conflict')),
  explanation               TEXT,
  created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
-- 幂等键：subject + summary hash + source hash + checker（不变量 10）。
CREATE UNIQUE INDEX idx_recon_run_dedupe
  ON summary_reconciliation_run(project_id, subject_type, subject_id,
                                summary_sha256, source_sha256, checker_schema,
                                checker_prompt_hash);

-- 每次真实 provider attempt 一行（同 run 可以有多行；fencing token 只允许
-- 至多一次业务结果生效）。
CREATE TABLE summary_reconciliation_call (
  run_id                    TEXT NOT NULL REFERENCES summary_reconciliation_run(id)
                            ON DELETE CASCADE,
  model_call_id             TEXT NOT NULL REFERENCES model_call(id),
  attempt_no                INTEGER NOT NULL CHECK (attempt_no >= 1),
  fencing_token             INTEGER NOT NULL,
  PRIMARY KEY (run_id, attempt_no)
);

-- outbox：current snapshot / summary head / event head / Canon evidence
-- fingerprint 改变时，写侧必须**同一事务**插这里。
CREATE TABLE summary_reconciliation_outbox (
  id                        TEXT PRIMARY KEY,
  project_id                TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event')),
  subject_id                TEXT NOT NULL,
  chapter_number            INTEGER,
  checked_against_snapshot_id TEXT NOT NULL REFERENCES chapter_snapshot(id),
  source_generation         INTEGER,
  source_sha256             TEXT NOT NULL,
  summary_sha256            TEXT,
  checker_schema            TEXT,
  checker_prompt_hash       TEXT,
  status                    TEXT NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','SUPERSEDED')),
  lease_owner               TEXT,
  lease_expires_at          TEXT,
  fencing_token             INTEGER NOT NULL DEFAULT 0,
  created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
-- outbox 幂等：同一 (subject, summary hash, source hash, checker) 只排一次。
CREATE UNIQUE INDEX idx_recon_outbox_dedupe
  ON summary_reconciliation_outbox(project_id, subject_type, subject_id,
                                  summary_sha256, source_sha256, checker_schema,
                                  checker_prompt_hash);

-- ══════════════════════════════════════════════════════════════════════════
-- 3. system_notification：统一通知的去重 / 忽略 / 解决
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE system_notification (
  id                        TEXT PRIMARY KEY,
  project_id                TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  kind                      TEXT NOT NULL
                            CHECK (kind IN ('summary_mismatch','background_failure','validation_blocked')),
  status                    TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN','IGNORED','RESOLVED')),
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event','chapter')),
  subject_id                TEXT NOT NULL,
  chapter_number            INTEGER,
  title                     TEXT NOT NULL,
  summary_sha256            TEXT,
  source_sha256             TEXT,
  jump_para_index           INTEGER,
  jump_quote_text           TEXT,
  jump_occurrence_k         INTEGER,
  actions_json              TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(actions_json)),
  dedupe_key                TEXT NOT NULL,
  created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  resolved_at               TEXT,
  ignored_at                TEXT,
  UNIQUE (project_id, dedupe_key)
);
CREATE INDEX idx_notification_open ON system_notification(project_id, status);

-- 通知 outbox：final gate BLOCKED ↔ 通知必须同事务（不变量 29）。
CREATE TABLE system_notification_outbox (
  id                        TEXT PRIMARY KEY,
  project_id                TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  intent                    TEXT NOT NULL CHECK (intent IN ('CREATE_OR_UPDATE','RESOLVE')),
  kind                      TEXT NOT NULL
                            CHECK (kind IN ('summary_mismatch','background_failure','validation_blocked')),
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event','chapter')),
  subject_id                TEXT NOT NULL,
  chapter_number            INTEGER,
  title                     TEXT NOT NULL,
  summary_sha256            TEXT,
  source_sha256             TEXT,
  jump_para_index           INTEGER,
  jump_quote_text           TEXT,
  jump_occurrence_k         INTEGER,
  actions_json              TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(actions_json)),
  dedupe_key                TEXT NOT NULL,
  status                    TEXT NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING','DONE','FAILED')),
  lease_owner               TEXT,
  lease_expires_at          TEXT,
  fencing_token             INTEGER NOT NULL DEFAULT 0,
  created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_notification_outbox_pending
  ON system_notification_outbox(project_id, status, created_at);

PRAGMA user_version = 21;
