-- 027：第五种通知 —— 这一章整理完了，但**一件都没留下**（`extraction_yielded_nothing`）。
--
-- ── 为什么非得多一档 kind，而不是复用现成的四档 ──────────────────────────
-- 2026-08-23 在作者的 158 章真书上实测出来的：
--
--     章    模型抽到的事件   引擎留下的   丢掉的      run 状态
--     1          12            0          12      SUCCEEDED / errors=[]
--     2          11            0          11      SUCCEEDED / errors=[]
--     158        12            0          12      SUCCEEDED / errors=[]
--
-- 丢弃条件只有一条（`extract/service.py`：事件里的人在花名册里认不出来 ⇒ 整条丢），
-- 而那本书的花名册是空的（`node` 里只有 158 个 Chapter，Character 零个）。于是：
--
--     花名册空 → 事件里的人认不出 → 事件全丢 → 花名册还是空 → 下一章接着全丢
--
-- **这是一个哑告警**：引擎每次都报成功，而整本书的图谱是空的（人物 0 / 边 0 /
-- 事件 0 / 证据 0），没有任何一处告诉过作者。
--
-- 四档都不能挂：
--   · `validation_blocked` —— 带副作用（`chapter_refresh` 见 gate=blocked 会停掉总结
--     和抽取两支），而这一次两支都正常跑完了。挂它 = 通知在撒谎（同 022 / 026 的论证）。
--   · `background_failure` —— 它的定义是「后台任务失败（provider 崩溃、重放过不去…）」。
--     **这一次没失败**，模型答了、我们也处理完了，只是产出为零。叫它失败是另一种撒谎，
--     而且会让作者去查一个不存在的故障。
--   · `summary_mismatch` —— 那是总结与正文对不上，跟抽取无关。
--   · `text_advisory` —— 它的 `jump` **必填且引语非空**（026 有意定的：说不出「在哪一句」
--     的告警作者点不过去）。而这一条说的是整整一章的产出，不指向某一句。为了复用而编一个
--     假锚，等于把 026 那条纪律从内部拆掉。
--
-- **它天然在不阻断那一侧**：`BLOCKING_KINDS` 只有 `validation_blocked` 一个成员，
-- 新档不加进去就不停任何下游。
--
-- ── subject_type 多一档 `chapter_snapshot` ──────────────────────────────
-- 去重按**这一版正文**，不按章：同一版重跑几次抽取只提醒一次（幂等），而作者改了正文
-- 再跑那是新的一版，值得再说一次——他可能正是为了修这件事才去改的。按章去重第二次就哑了。
--
-- ── 两张表都要改 ────────────────────────────────────────────────────────
-- outbox 那张的 CHECK 先拦：只改物化表的话，新 kind 连 enqueue 都进不去。
-- SQLite 改不了 CHECK，只能建新表 → 拷过去 → 丢掉旧的 → 改名（同 012 / 021 / 023 / 026）。
-- 中转表进不了成品库，**建表数不变**。
--
-- 这份 DDL 的列定义是**从 026 逐字沿用**的（生成时只替换了两处 CHECK 和 user_version）：
-- 手抄一遍漏掉一列的代价是静默丢数据，而 026 那张表有 19 列。

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
                                            'extraction_yielded_nothing')),
  status                    TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN','IGNORED','RESOLVED')),
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event','chapter',
                                                    'chapter_snapshot')),
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
                                            'validation_blocked','text_advisory',
                                            'extraction_yielded_nothing')),
  subject_type              TEXT NOT NULL
                            CHECK (subject_type IN ('chapter_summary','proposal_event','canon_event','chapter',
                                                    'chapter_snapshot')),
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

PRAGMA user_version = 27;
