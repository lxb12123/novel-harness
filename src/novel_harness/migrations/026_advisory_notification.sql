-- 026：第四种通知 —— 只告警、不阻断的正文事后核对（`text_advisory`）。
--
-- ── 为什么非得多一档 kind ────────────────────────────────────────────────
-- 022 那三种里唯一「正文出了事」的是 `validation_blocked`，而它**带副作用**：
-- 它那句「新正文不会再自动生成总结与情节」是真的 —— `chapter_refresh` 见到
-- gate=blocked 就把总结和抽取两支一起停掉，通知只是那件事的回执。
--
-- 保存之后的语义核对（这一章有没有对不该知道的人说破秘密、这一段跟后面章节
-- 已经写死的设定抵不抵触）按 [ADR 0030](../../../docs/adr/0030-versioned-summaries-and-advisory-reconciliation.md)
-- 的窄例外**只告警不阻断**。把它挂到 `validation_blocked` 上去，作者改一个老章
-- 就会把那一章的自动整理停掉 —— 而**那不会有任何东西报错**，只会表现成「总结
-- 怎么一直不更新」。所以这一档必须在类型上就和阻断那一档分开。
--
-- ── 两张表都要改 ────────────────────────────────────────────────────────
-- outbox 那张的 CHECK 先拦：只改物化表的话，新 kind 连 enqueue 都进不去。
-- SQLite 改不了 CHECK，只能建新表 → 拷过去 → 丢掉旧的 → 改名（同 012 / 021 / 023）。
-- 中转表进不了成品库，**建表数不变**。

PRAGMA legacy_alter_table = ON;

-- ══════════════════════════════════════════════════════════════════════════
-- 1. system_notification：物化表
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE system_notification_new (
  id                        TEXT PRIMARY KEY,
  project_id                TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  kind                      TEXT NOT NULL
                            CHECK (kind IN ('summary_mismatch','background_failure',
                                            'validation_blocked','text_advisory')),
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

INSERT INTO system_notification_new (
  id, project_id, kind, status, subject_type, subject_id, chapter_number, title,
  summary_sha256, source_sha256, jump_para_index, jump_quote_text, jump_occurrence_k,
  actions_json, dedupe_key, created_at, resolved_at, ignored_at
)
SELECT
  id, project_id, kind, status, subject_type, subject_id, chapter_number, title,
  summary_sha256, source_sha256, jump_para_index, jump_quote_text, jump_occurrence_k,
  actions_json, dedupe_key, created_at, resolved_at, ignored_at
FROM system_notification;

DROP TABLE system_notification;
ALTER TABLE system_notification_new RENAME TO system_notification;

CREATE INDEX idx_notification_open ON system_notification(project_id, status);

-- ══════════════════════════════════════════════════════════════════════════
-- 2. system_notification_outbox：同事务落账的那张（不变量 29）
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE system_notification_outbox_new (
  id                        TEXT PRIMARY KEY,
  project_id                TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  intent                    TEXT NOT NULL CHECK (intent IN ('CREATE_OR_UPDATE','RESOLVE')),
  kind                      TEXT NOT NULL
                            CHECK (kind IN ('summary_mismatch','background_failure',
                                            'validation_blocked','text_advisory')),
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

INSERT INTO system_notification_outbox_new (
  id, project_id, intent, kind, subject_type, subject_id, chapter_number, title,
  summary_sha256, source_sha256, jump_para_index, jump_quote_text, jump_occurrence_k,
  actions_json, dedupe_key, status, lease_owner, lease_expires_at, fencing_token, created_at
)
SELECT
  id, project_id, intent, kind, subject_type, subject_id, chapter_number, title,
  summary_sha256, source_sha256, jump_para_index, jump_quote_text, jump_occurrence_k,
  actions_json, dedupe_key, status, lease_owner, lease_expires_at, fencing_token, created_at
FROM system_notification_outbox;

DROP TABLE system_notification_outbox;
ALTER TABLE system_notification_outbox_new RENAME TO system_notification_outbox;

CREATE INDEX idx_notification_outbox_pending
  ON system_notification_outbox(project_id, status, created_at);

PRAGMA legacy_alter_table = OFF;

PRAGMA user_version = 26;
