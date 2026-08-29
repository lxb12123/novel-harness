-- 034：第七种通知 —— 一件事的在场/知情名单掉了一个人（`event_cast_changed`）。
--
-- ── 这条通知配的是哪条裁定 ────────────────────────────────────────────────
-- 维护者裁定（2026-08-28）：删花名册条目**不再拒绝，直接删**。删之前那道闸
-- （`NodeInUse`，见 `graph/models.py::NodeUsage` 的旧论证）换成了「事后可见可改」：
-- 一个人被删掉之后，他参与过的每一条情节都掉了一个参与者——这件事要能被
-- 剩下的人查到、定位到，作者自己决定要不要去改。这条通知就是那个「查到」。
--
-- ── 为什么不挂现成六档 ────────────────────────────────────────────────────
--   · `validation_blocked` —— **绝不能挂这档**：它会把这一章的总结/抽取停掉
--     （`BLOCKING_KINDS`），而删一个人不该换来「这一章的自动整理再也不更新」。
--   · `text_advisory` —— 语义不对：那一档是"模型核对正文语义"，这一条是
--     "花名册变了、事件名单跟着变"，触发源、判据、payload 形状都不是一回事，
--     混用会让"resolve 一条 text_advisory"这个动作在两种完全不同的场景下
--     发生，含义就不再单一。
--   · 其余四档（`summary_mismatch`/`background_failure`/
--     `extraction_yielded_nothing`/`import_toc_skipped`）主语分别是总结/后台
--     任务/抽取产出/导入，没有一个对得上"一条已存在的情节掉了一个人"。
--
-- `subject_type` 不用新加：复用现成的 `canon_event`（`summary_reconciliation.py`
-- 已经在用它表示"subject_id = event_id"）。`jump` 用事件自己的 evidence 锚
-- （`Evidence.anchor()`）——不新造一套定位，同 `Issue`/`text_advisory` 那批
-- 一样复用 `(para_index, quote_text, occurrence_k)`。
--
-- 去重：`dedupe_key_for(kind, subject_type, subject_id, None, None)` ——
-- 只按 kind+subject 算，不掺内容 hash。**一个事件同时只有一条 OPEN 的
-- `event_cast_changed`**：再删一个人只刷新这条通知（标题/锚跟着最新一次算），
-- 不会在同一个事件上堆出好几条。历史上删过谁不是这张表要记的事
-- （`decision_log` 才是那种账），这里只需要说清"现在还需要作者去看一眼"。
--
-- ── 两张表都要改（同 022/026/027/032）────────────────────────────────────
-- outbox 那张的 CHECK 先拦：只改物化表的话，新 kind 连 enqueue 都进不去。
-- SQLite 改不了 CHECK，只能建新表 → 拷过去 → 丢掉旧的 → 改名。
-- 中转表进不了成品库，**建表数不变**。033 加的 title_code/title_params_json
-- 两列原样带过来——那次是纯 ADD COLUMN，没有 CHECK 需要重建，这次不能漏抄。

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
                                            'import_toc_skipped',
                                            'event_cast_changed')),
  status                    TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN','IGNORED','RESOLVED')),
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event','chapter',
                                                    'chapter_snapshot','project')),
  subject_id                TEXT NOT NULL,
  chapter_number            INTEGER,
  title                     TEXT NOT NULL,
  title_code                TEXT,
  title_params_json         TEXT CHECK (title_params_json IS NULL OR json_valid(title_params_json)),
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
  title_code, title_params_json,
  summary_sha256, source_sha256, jump_para_index, jump_quote_text, jump_occurrence_k,
  actions_json, payload_json, dedupe_key, created_at, resolved_at, ignored_at
)
SELECT
  id, project_id, kind, status, subject_type, subject_id, chapter_number, title,
  title_code, title_params_json,
  summary_sha256, source_sha256, jump_para_index, jump_quote_text, jump_occurrence_k,
  actions_json, payload_json, dedupe_key, created_at, resolved_at, ignored_at
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
                                            'import_toc_skipped',
                                            'event_cast_changed')),
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event','chapter',
                                                    'chapter_snapshot','project')),
  subject_id                TEXT NOT NULL,
  chapter_number            INTEGER,
  title                     TEXT NOT NULL,
  title_code                TEXT,
  title_params_json         TEXT CHECK (title_params_json IS NULL OR json_valid(title_params_json)),
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
  title_code, title_params_json,
  summary_sha256, source_sha256, jump_para_index, jump_quote_text, jump_occurrence_k,
  actions_json, payload_json, dedupe_key, status, lease_owner, lease_expires_at, fencing_token, created_at
)
SELECT
  id, project_id, intent, kind, subject_type, subject_id, chapter_number, title,
  title_code, title_params_json,
  summary_sha256, source_sha256, jump_para_index, jump_quote_text, jump_occurrence_k,
  actions_json, payload_json, dedupe_key, status, lease_owner, lease_expires_at, fencing_token, created_at
FROM system_notification_outbox;

DROP TABLE system_notification_outbox;
ALTER TABLE system_notification_outbox_new RENAME TO system_notification_outbox;

CREATE INDEX idx_notification_outbox_pending
  ON system_notification_outbox(project_id, status, created_at);

PRAGMA legacy_alter_table = OFF;

PRAGMA user_version = 34;
