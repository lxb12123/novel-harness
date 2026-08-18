-- 018：三类总结 append-only 版本 + 显式 head（ADR 0030 / 计划 Task 6/7）。
--
-- ── 为什么重建 chapter_summary ──────────────────────────────────────────
-- 013 用 ALTER 加过 source/status 两列，但「当前总结」仍靠 rowid 猜、撤回行还背着
-- 一段原文。新模型要三样 ALTER 给不了的东西：summary 可空（RETRACTED tombstone）、
-- `source_snapshot_id` 绑定、`replaces_summary_id` 版本链。SQLite 不能改 CHECK
-- 约束，只能重建表；014 的 summary_index 不依赖本表结构，只认 summary_id，安全。

-- ── 既有数据迁移口径 ────────────────────────────────────────────────────
-- · summary_sha256：ACTIVE 行 = 正文 UTF-8 原始字节的 SHA-256；RETRACTED 行 =
--   sha256(b"")（统一空字节 hash，与 §4.3 的 tombstone 口径一致）。
-- · source_snapshot_id：只有一章一条快照的章可可靠反推（总结只可能从那一版写成），
--   回填；多快照章无法诚实反推 → 保留 NULL 并标 source='legacy'，不假绑当前正文。
-- · replaces_summary_id：按 (project, chapter, rowid) 顺序指向上一条。
-- · chapter_summary_head：每章回填「最新那一行」（rowid 最大），与迁移前读端同解。
-- · 每个现存 story_event 从 evidence → chapter_snapshot 回填一条基线 event 版本。

CREATE TABLE chapter_summary_new (
  id                  TEXT PRIMARY KEY,
  project_id          TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_id          TEXT NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
  chapter_number      INTEGER NOT NULL CHECK (chapter_number >= 1),
  summary             TEXT,
  summary_sha256      TEXT NOT NULL,
  schema_version      TEXT NOT NULL,
  prompt_hash         TEXT NOT NULL,
  model_call_id       TEXT REFERENCES model_call(id),
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  source              TEXT NOT NULL DEFAULT 'model'
                      CHECK (source IN ('model','author','legacy')),
  status              TEXT NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('ACTIVE','RETRACTED')),
  source_snapshot_id  TEXT REFERENCES chapter_snapshot(id),
  replaces_summary_id TEXT REFERENCES chapter_summary_new(id),
  -- ACTIVE 必须有正文；RETRACTED 必须没正文且 hash 是统一空字节 hash。
  CHECK ((status = 'ACTIVE' AND summary IS NOT NULL AND length(summary) > 0
          AND summary_sha256 = nh_sha256_text(summary))
      OR (status = 'RETRACTED' AND summary IS NULL
          AND summary_sha256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')),
  UNIQUE (project_id, chapter_number, schema_version, prompt_hash)
);

-- 只有一条快照的章：来源可可靠反推。
CREATE TEMP TABLE _inferable_snapshot AS
  SELECT c.id AS chapter_id, s.id AS snapshot_id
    FROM chapter c
    JOIN chapter_snapshot s ON s.chapter_id = c.id
   GROUP BY c.id
  HAVING COUNT(DISTINCT s.text_sha256) = 1;

-- 每行的前一行（按 rowid 序）＝ replaces_summary_id。
CREATE TEMP TABLE _summary_prev AS
  SELECT id, LAG(id) OVER (
           PARTITION BY project_id, chapter_number ORDER BY rowid
         ) AS prev_id
    FROM chapter_summary;

INSERT INTO chapter_summary_new (
  id, project_id, chapter_id, chapter_number, summary, summary_sha256,
  schema_version, prompt_hash, model_call_id, created_at, source, status,
  source_snapshot_id, replaces_summary_id
)
SELECT
  old.id,
  old.project_id,
  ch.id,
  old.chapter_number,
  CASE WHEN old.status = 'RETRACTED' THEN NULL ELSE old.summary END,
  CASE WHEN old.status = 'RETRACTED'
       THEN 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
       ELSE nh_sha256_text(old.summary) END,
  old.schema_version,
  old.prompt_hash,
  old.model_call_id,
  old.created_at,
  CASE WHEN inf.snapshot_id IS NOT NULL THEN old.source ELSE 'legacy' END,
  old.status,
  inf.snapshot_id,
  prev.prev_id
FROM chapter_summary old
JOIN chapter ch
  ON ch.project_id = old.project_id AND ch.number = old.chapter_number
LEFT JOIN _inferable_snapshot inf ON inf.chapter_id = ch.id
LEFT JOIN _summary_prev prev ON prev.id = old.id;

DROP TABLE chapter_summary;
ALTER TABLE chapter_summary_new RENAME TO chapter_summary;
CREATE INDEX idx_chapter_summary_latest
  ON chapter_summary(project_id, chapter_number, created_at DESC, id DESC);

-- ── 显式 head：每章一行，current_summary_id 可空 ───────────────────────
CREATE TABLE chapter_summary_head (
  chapter_id           TEXT PRIMARY KEY REFERENCES chapter(id) ON DELETE CASCADE,
  current_summary_id   TEXT REFERENCES chapter_summary(id),
  machine_intent_seq   INTEGER NOT NULL DEFAULT 0,
  updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

INSERT INTO chapter_summary_head (chapter_id, current_summary_id)
SELECT c.id, latest.id
FROM chapter c
LEFT JOIN (
  SELECT project_id, chapter_number, id,
         ROW_NUMBER() OVER (
           PARTITION BY project_id, chapter_number ORDER BY rowid DESC
         ) AS rn
    FROM chapter_summary
) latest
  ON latest.project_id = c.project_id
 AND latest.chapter_number = c.number
 AND latest.rn = 1;

-- ── 总结生成任务 / 结果审计（机器结果先写 result，CAS 成功才投影成版本）──
CREATE TABLE summary_generation_job (
  id                        TEXT PRIMARY KEY,
  project_id                TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  target_type               TEXT NOT NULL CHECK (target_type IN ('CHAPTER','EVENT')),
  chapter_id                TEXT REFERENCES chapter(id) ON DELETE CASCADE,
  event_id                  TEXT,
  source_snapshot_id        TEXT,
  source_generation         INTEGER,
  source_sha256             TEXT NOT NULL,
  refresh_attempt_id        TEXT,
  required_ruleset_epoch    INTEGER,
  required_ruleset_hash     TEXT,
  expected_head_version_id  TEXT,
  required_machine_intent_seq INTEGER,
  trigger_key               TEXT NOT NULL,
  trigger_source            TEXT NOT NULL DEFAULT 'save',
  status                    TEXT NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN
                              ('PENDING','RUNNING','SUCCEEDED','REUSED','FAILED','SUPERSEDED')),
  lease_owner               TEXT,
  lease_expires_at          TEXT,
  fencing_token             INTEGER NOT NULL DEFAULT 0,
  created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK ((target_type = 'CHAPTER'
          AND chapter_id IS NOT NULL AND event_id IS NULL
          AND source_snapshot_id IS NOT NULL AND source_generation IS NOT NULL
          -- attempt/ruleset 在 Task 16 接通协调器前可为 NULL（手动 regenerate）；
          -- 一旦绑定 refresh attempt，final gate 纪律由协调器 enforce。
          AND ((refresh_attempt_id IS NULL AND required_ruleset_epoch IS NULL
                AND required_ruleset_hash IS NULL)
               OR (refresh_attempt_id IS NOT NULL
                   AND required_ruleset_epoch IS NOT NULL
                   AND required_ruleset_hash IS NOT NULL)))
      OR (target_type = 'EVENT'
          AND event_id IS NOT NULL AND chapter_id IS NULL
          AND source_snapshot_id IS NOT NULL AND source_generation IS NOT NULL
          AND refresh_attempt_id IS NULL
          AND required_ruleset_epoch IS NULL AND required_ruleset_hash IS NULL))
);

CREATE TABLE summary_generation_result (
  id                 TEXT PRIMARY KEY,
  job_id             TEXT NOT NULL UNIQUE REFERENCES summary_generation_job(id)
                      ON DELETE CASCADE,
  summary            TEXT NOT NULL CHECK (length(summary) > 0),
  summary_sha256     TEXT NOT NULL,
  model_call_id      TEXT REFERENCES model_call(id),
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ── 事件摘要版本（Task 7 的读端；story_event.summary 仍是迁移基线）──────
CREATE TABLE event_summary_version (
  id                 TEXT PRIMARY KEY,
  project_id         TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  event_id           TEXT NOT NULL REFERENCES story_event(id) ON DELETE CASCADE,
  source_snapshot_id TEXT REFERENCES chapter_snapshot(id),
  evidence_sha256    TEXT,
  summary            TEXT NOT NULL CHECK (length(summary) > 0),
  summary_sha256     TEXT NOT NULL,
  source             TEXT NOT NULL CHECK (source IN ('model','author','legacy')),
  status             TEXT NOT NULL CHECK (status IN ('ACTIVE','RETRACTED')),
  replaces_version_id TEXT REFERENCES event_summary_version(id),
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE event_summary_head (
  event_id           TEXT PRIMARY KEY REFERENCES story_event(id) ON DELETE CASCADE,
  current_version_id TEXT REFERENCES event_summary_version(id),
  machine_intent_seq INTEGER NOT NULL DEFAULT 0,
  updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- 既有事件基线：evidence → chapter_snapshot 能反推的绑定快照；反推不了留 NULL
-- 并标 legacy。head 指向基线，保证「每事件有 head 行」从迁移第一天就成立。
INSERT INTO event_summary_version (
  id, project_id, event_id, source_snapshot_id, evidence_sha256,
  summary, summary_sha256, source, status
)
SELECT
  'event-summary:legacy:' || e.id,
  e.project_id,
  e.id,
  ev.chapter_snapshot_id,
  CASE WHEN ev.id IS NOT NULL THEN nh_sha256_text(ev.quote_text) END,
  e.summary,
  nh_sha256_text(e.summary),
  CASE WHEN ev.id IS NOT NULL THEN 'model' ELSE 'legacy' END,
  'ACTIVE'
FROM story_event e
LEFT JOIN evidence ev ON ev.id = e.evidence_id;

INSERT INTO event_summary_head (event_id, current_version_id)
SELECT e.id, v.id
FROM story_event e
LEFT JOIN event_summary_version v ON v.event_id = e.id;

PRAGMA user_version = 18;
