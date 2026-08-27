-- 032：第六种通知 —— 导入时丢掉了目录页复制出来的假章（`import_toc_skipped`）。
--
-- ── 为什么非得多一档 kind，而不是复用现成的五档 ──────────────────────────
-- `text/chapterize.py::drop_toc_duplicates()`（2026-08-27）在导入时会丢掉
-- 「正文是空的，且全书还有另一章 (marker, title) 跟它一模一样、正文非空」的那些
-- 命中——目录页把正文的章标题复制了一遍，切章器会把它们当成真章数两遍。
-- 不许静默丢：作者要知道「章数比看起来少」不是切章器漏切，是真的丢了目录页。
--
-- 五档都不能挂：
--   · `extraction_yielded_nothing` —— 那是**抽取**产出为零，这一条发生在抽取
--     之前，跟抽取毫无关系。
--   · `validation_blocked` / `text_advisory` —— 都是**验证/语义核对**发现的问题，
--     这一条不是验证发现的，是切章器自己的结构判断，且不带 `Issue`。
--   · `background_failure` —— 不是失败，切章成功了、导入也成功了，只是丢了几个
--     假章。叫它失败会让作者去查一个不存在的故障（同 027 的论证）。
--   · `summary_mismatch` —— 跟总结无关。
--
-- ── 带一个新东西：`撤销` 动作 + 它要存的数据 ─────────────────────────────
-- 撤销要把丢掉的占位章插回原来的位置（`importer.undo_toc_skip`），需要两样：
-- 丢了哪些（位置 + 标题行原样）、以及「这本书没变过」的判据（指纹）。现成的列
-- （`jump_*` / `summary_sha256` / `source_sha256`）没有一个形状对得上，硬塞
-- 只会让下一个读这张表的人猜错它们的含义。新增 `payload_json`——**通用逃生舱**，
-- 只有本档使用（其余五档恒 NULL），下一种需要额外数据的通知不必再开一次迁移。
--
-- `subject_type` 也多一档 `project`：这条通知说的是**整本书**（导入这件事本身），
-- 不是某一章、某一版快照、某一条提案/事件——现成五档没有一个对得上。
-- `subject_id` 就用 `project_id`：没有一张「导入事件」表可指，这本书自己就是主语。
--
-- ── 两张表都要改（同 022/026/027）────────────────────────────────────────
-- outbox 那张的 CHECK 先拦：只改物化表的话，新 kind 连 enqueue 都进不去。
-- SQLite 改不了 CHECK/加列都要重建表：建新表 → 拷过去 → 丢掉旧的 → 改名。
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
                                            'validation_blocked','text_advisory',
                                            'extraction_yielded_nothing',
                                            'import_toc_skipped')),
  status                    TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN','IGNORED','RESOLVED')),
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event','chapter',
                                                    'chapter_snapshot','project')),
  subject_id                TEXT NOT NULL,
  chapter_number            INTEGER,
  title                     TEXT NOT NULL,
  summary_sha256            TEXT,
  source_sha256             TEXT,
  jump_para_index           INTEGER,
  jump_quote_text           TEXT,
  jump_occurrence_k         INTEGER,
  actions_json              TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(actions_json)),
  payload_json              TEXT CHECK (payload_json IS NULL OR json_valid(payload_json)),
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
                                            'validation_blocked','text_advisory',
                                            'extraction_yielded_nothing',
                                            'import_toc_skipped')),
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event','chapter',
                                                    'chapter_snapshot','project')),
  subject_id                TEXT NOT NULL,
  chapter_number            INTEGER,
  title                     TEXT NOT NULL,
  summary_sha256            TEXT,
  source_sha256             TEXT,
  jump_para_index           INTEGER,
  jump_quote_text           TEXT,
  jump_occurrence_k         INTEGER,
  actions_json              TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(actions_json)),
  payload_json              TEXT CHECK (payload_json IS NULL OR json_valid(payload_json)),
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

PRAGMA user_version = 32;
