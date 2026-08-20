-- 021：抽取晚到防护 —— SUPERSEDED run、proposal currentness、规范 analysis、
--      按 generation 的 application head（ADR 0029 / 计划 Task 9）。
--
-- ── 这条迁移要堵的洞 ─────────────────────────────────────────────────────
-- 保存 S2 之后又保存 S3：S2 的抽取 run 晚到。修之前它的结果照旧 ingest、照旧
-- auto-Canon——S1(g1)→S2(g2)→S1(g3) 时甚至能用「snapshot/hash 又相同」复活
-- 第一轮 g1 的晚到结果（ABA）。所以从这一版起：
--   · extraction_run 记下**创建时冻结**的 source_generation / ruleset epoch+hash /
--     fencing token；run 完成时若这些已不是当前值 → SUPERSEDED，只留审计。
--   · proposal 不再由「status=PENDING」单独决定待确认：旧 snapshot 的提案标
--     OBSOLETE（status 保持 PENDING 审计状态，`superseded_by_snapshot_id` 记原因）。
--   · 机器事实挂到按 generation 的 extraction_application；只有 CURRENT application
--     的机器事实对生产读可见（Task 12 的重放吃同一张 head）。
--
-- ── 迁移口径（既有 v16 数据）──────────────────────────────────────────────
-- 018 已经为每个 chapter 建了 legacy refresh run（generation=1）。这里为每个
-- refresh run 补 application head + LEGACY application：该 generation 还有 FRESH
-- extractor 事实的标 CURRENT，其余标 SUPERSEDED。LEGACY application 没有规范
-- analysis_json（`replayable=false`），别名纠正碰到它只能停火并通知，不能重放。

-- ══════════════════════════════════════════════════════════════════════════
-- 1. extraction_run：重建（SQLite 不能改 CHECK），补 SUPERSEDED + 冻结字段
-- ══════════════════════════════════════════════════════════════════════════

-- 重建期间关掉 ALTER TABLE RENAME 的触发器重写：`chapter_snapshot_children_*`
-- 等触发器引用 `extraction_run`，默认行为会在 DROP 后重编译它们而炸掉
-- （`no such table: extraction_run`）。legacy 模式只影响 RENAME 的引用改写，
-- 表结构本身不受影响；重建完立即恢复默认。
PRAGMA legacy_alter_table = ON;

CREATE TABLE extraction_run_new (
  id                    TEXT PRIMARY KEY,
  project_id            TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_number        INTEGER NOT NULL,
  snapshot_id           TEXT NOT NULL REFERENCES chapter_snapshot(id),
  status                TEXT NOT NULL DEFAULT 'PENDING',
  errors_json           TEXT NOT NULL DEFAULT '[]',
  valid_event_count     INTEGER NOT NULL DEFAULT 0,
  discarded_event_count INTEGER NOT NULL DEFAULT 0,
  proposal_count        INTEGER NOT NULL DEFAULT 0,
  model_call_id         TEXT,
  schema_version        TEXT NOT NULL,
  prompt_hash           TEXT NOT NULL,
  source_generation     INTEGER,
  required_ruleset_epoch INTEGER,
  required_ruleset_hash TEXT,
  fencing_token         INTEGER NOT NULL DEFAULT 0,
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  started_at            TEXT,
  finished_at           TEXT,
  -- 内容寻址 + basis：S1(g1)→S2(g2)→S1(g3) 时 snapshot/prompt 再次相同，
  -- 但 g3 必须有自己的 run（g1 的晚到结果不能借 ABA 复活）。同一 generation
  -- 同 ruleset 的同内容仍然复用同一条 run（`test_enqueue_..._reuses_the_same_run`）。
  UNIQUE (project_id, snapshot_id, schema_version, prompt_hash,
          source_generation, required_ruleset_epoch, required_ruleset_hash),
  CHECK (chapter_number >= 1),
  CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','SUPERSEDED')),
  CHECK (json_valid(errors_json)),
  CHECK (valid_event_count >= 0),
  CHECK (discarded_event_count >= 0),
  CHECK (proposal_count >= 0),
  CHECK (source_generation IS NULL OR source_generation >= 1),
  CHECK (required_ruleset_epoch IS NULL OR required_ruleset_epoch >= 1),
  FOREIGN KEY (model_call_id, project_id)
    REFERENCES model_call(id, project_id)
);

INSERT INTO extraction_run_new (
  id, project_id, chapter_number, snapshot_id, status, errors_json,
  valid_event_count, discarded_event_count, proposal_count, model_call_id,
  schema_version, prompt_hash, created_at, started_at, finished_at
)
SELECT id, project_id, chapter_number, snapshot_id, status, errors_json,
       valid_event_count, discarded_event_count, proposal_count, model_call_id,
       schema_version, prompt_hash, created_at, started_at, finished_at
FROM extraction_run;

DROP TABLE extraction_run;
ALTER TABLE extraction_run_new RENAME TO extraction_run;

PRAGMA legacy_alter_table = OFF;

CREATE TRIGGER extraction_run_snapshot_coherent_insert
BEFORE INSERT ON extraction_run
WHEN NOT EXISTS (
  SELECT 1
  FROM chapter_snapshot AS snapshot
  JOIN chapter AS chapter ON chapter.id = snapshot.chapter_id
  WHERE snapshot.id = NEW.snapshot_id
    AND chapter.project_id = NEW.project_id
    AND chapter.number = NEW.chapter_number
)
BEGIN
  SELECT RAISE(ABORT, 'extraction snapshot must belong to its project and chapter');
END;

CREATE TRIGGER extraction_run_snapshot_coherent_update
BEFORE UPDATE OF project_id, chapter_number, snapshot_id ON extraction_run
WHEN NOT EXISTS (
  SELECT 1
  FROM chapter_snapshot AS snapshot
  JOIN chapter AS chapter ON chapter.id = snapshot.chapter_id
  WHERE snapshot.id = NEW.snapshot_id
    AND chapter.project_id = NEW.project_id
    AND chapter.number = NEW.chapter_number
)
BEGIN
  SELECT RAISE(ABORT, 'extraction snapshot must belong to its project and chapter');
END;

-- 规范 analysis JSON（严格 Pydantic 校验后落库，供别名纠正后确定性重放；
-- 不保存不可验证的自由文本）。
CREATE TABLE extraction_analysis (
  run_id        TEXT PRIMARY KEY REFERENCES extraction_run(id) ON DELETE CASCADE,
  project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  snapshot_id   TEXT NOT NULL REFERENCES chapter_snapshot(id),
  schema_version TEXT NOT NULL,
  analysis_json TEXT NOT NULL CHECK (json_valid(analysis_json)),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_extraction_analysis_project
  ON extraction_analysis(project_id, snapshot_id);

-- ══════════════════════════════════════════════════════════════════════════
-- 2. proposal_set：currentness（status 仍是作者裁决；currentness 是正文时效）
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE proposal_set ADD COLUMN currentness TEXT NOT NULL DEFAULT 'CURRENT'
  CHECK (currentness IN ('CURRENT','OBSOLETE'));
ALTER TABLE proposal_set ADD COLUMN superseded_at TEXT;
ALTER TABLE proposal_set ADD COLUMN superseded_by_snapshot_id TEXT;

-- 回填：PENDING 提案若锚的不是 chapter 当前快照 → OBSOLETE。
-- 003 的审计触发器只看 status（非 PENDING 才算作者裁决），currentness 不动它。
UPDATE proposal_set
   SET currentness = 'OBSOLETE',
       superseded_by_snapshot_id = (
         SELECT cs.id
           FROM chapter ch
           JOIN chapter_snapshot cs
             ON cs.chapter_id = ch.id AND cs.text_sha256 = ch.text_sha256
          WHERE ch.project_id = proposal_set.project_id
            AND ch.number = proposal_set.chapter_number
       )
 WHERE status = 'PENDING'
   AND snapshot_id IS NOT NULL
   AND NOT EXISTS (
     SELECT 1
     FROM chapter ch
     JOIN chapter_snapshot cs ON cs.chapter_id = ch.id
     WHERE cs.id = proposal_set.snapshot_id
       AND cs.text_sha256 = ch.text_sha256
   );

CREATE INDEX idx_proposal_set_current
  ON proposal_set(project_id, status, currentness);

-- ══════════════════════════════════════════════════════════════════════════
-- 3. extraction_application：一个 chapter generation 一行的机器事实归属
-- ══════════════════════════════════════════════════════════════════════════

-- head：每个 refresh run（= 一个 chapter generation）的当前 application + intent。
-- 任何 save/manual/ruleset/replay 意图在建 job 时对同一个 refresh run 原子递增
-- intent；激活新 application 时 CAS head 与 intent。
CREATE TABLE extraction_application_head (
  refresh_run_id       TEXT PRIMARY KEY REFERENCES chapter_refresh_run(id)
                       ON DELETE CASCADE,
  current_application_id TEXT,
  intent_seq           INTEGER NOT NULL DEFAULT 0,
  updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE extraction_application (
  id                     TEXT PRIMARY KEY,
  project_id             TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  refresh_run_id         TEXT NOT NULL REFERENCES chapter_refresh_run(id)
                         ON DELETE CASCADE,
  analysis_run_id        TEXT NOT NULL REFERENCES extraction_run(id),
  snapshot_id            TEXT NOT NULL REFERENCES chapter_snapshot(id),
  source_generation      INTEGER NOT NULL CHECK (source_generation >= 1),
  resolution_hash        TEXT,
  ruleset_epoch          INTEGER CHECK (ruleset_epoch >= 1),
  ruleset_hash           TEXT,
  required_intent_seq    INTEGER NOT NULL,
  expected_application_head TEXT,
  status                 TEXT NOT NULL DEFAULT 'STAGED'
                         CHECK (status IN ('STAGED','CURRENT','SUPERSEDED')),
  replayable             INTEGER NOT NULL DEFAULT 1,
  created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
-- 每个 refresh run（等价于一个 chapter generation）至多一个 CURRENT application。
CREATE UNIQUE INDEX idx_extraction_application_current
  ON extraction_application(refresh_run_id) WHERE status = 'CURRENT';

-- application 对可变投影（node profile 等）的 before/after 审计。
-- hash 只用于 CAS；实际回滚必须读 before_json（作者后来改过就 CAS 失败保留作者值）。
CREATE TABLE extraction_application_effect (
  application_id        TEXT NOT NULL REFERENCES extraction_application(id)
                        ON DELETE CASCADE,
  entity_type           TEXT NOT NULL,
  entity_id             TEXT NOT NULL,
  before_json           TEXT,
  after_json            TEXT,
  before_sha256         TEXT,
  after_sha256          TEXT,
  expected_entity_revision TEXT,
  PRIMARY KEY (application_id, entity_type, entity_id)
);

-- ══════════════════════════════════════════════════════════════════════════
-- 4. story_event：cast 归属（作者改过的名单机器重放不得再碰）
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE story_event ADD COLUMN cast_owner TEXT NOT NULL DEFAULT 'extractor'
  CHECK (cast_owner IN ('extractor','author'));
ALTER TABLE story_event ADD COLUMN cast_decision_log_id TEXT
  REFERENCES decision_log(id);

-- 回填：既有 `correct_event_cast` 的 decision log（kind='event_edit'）能定位到的
-- event 视为作者覆盖；其余 legacy extractor event 保持 extractor。
UPDATE story_event
   SET cast_owner = 'author',
       cast_decision_log_id = (
         SELECT d.id FROM decision_log d
          WHERE d.kind = 'event_edit'
            AND d.project_id = story_event.project_id
            AND json_extract(d.payload_json, '$.event_id') = story_event.id
          ORDER BY d.ts, d.id LIMIT 1
       )
 WHERE cast_owner = 'extractor'
   AND EXISTS (
     SELECT 1 FROM decision_log d
      WHERE d.kind = 'event_edit'
        AND d.project_id = story_event.project_id
        AND json_extract(d.payload_json, '$.event_id') = story_event.id
   );

-- ══════════════════════════════════════════════════════════════════════════
-- 5. 既有数据回填：LEGACY application（017 的 legacy refresh run 一一对应）
-- ══════════════════════════════════════════════════════════════════════════

-- 每个 refresh run 一个 head。LEGACY application：有 FRESH extractor 事实的
-- generation 是 CURRENT（作者现在看得见的东西不动），其余 SUPERSEDED。
-- 没有规范 analysis_json → replayable=false；别名纠正碰到它只能停火 + 通知。
INSERT INTO extraction_application_head (refresh_run_id, current_application_id, intent_seq)
SELECT r.id, NULL, 0
FROM chapter_refresh_run r;

-- LEGACY application 的 analysis_run_id 需要一个真实存在的外键锚：既有 v16 数据
-- 的 run 没有规范 analysis_json，统一给一个 per-refresh-run 的 legacy 占位 run
-- （SUPERSEDED + replayable=0，不参与任何业务，只当外键锚）。
INSERT OR IGNORE INTO extraction_run (
  id, project_id, chapter_number, snapshot_id, status, schema_version, prompt_hash,
  source_generation
)
SELECT 'run:legacy:' || r.id, r.project_id, c.number, r.source_snapshot_id,
       'SUPERSEDED', 'legacy', 'legacy', r.source_generation
FROM chapter_refresh_run r
JOIN chapter c ON c.id = r.chapter_id;

INSERT INTO extraction_application (
  id, project_id, refresh_run_id, analysis_run_id, snapshot_id, source_generation,
  resolution_hash, ruleset_epoch, ruleset_hash, required_intent_seq,
  expected_application_head, status, replayable
)
SELECT
  'app:legacy:' || r.id,
  r.project_id,
  r.id,
  'run:legacy:' || r.id,
  r.source_snapshot_id,
  r.source_generation,
  NULL,
  NULL,
  NULL,
  0,
  NULL,
  CASE WHEN EXISTS (
    SELECT 1 FROM story_event e
    WHERE e.project_id = r.project_id AND e.evidence_id IN (
      SELECT ev.id FROM evidence ev WHERE ev.chapter_snapshot_id = r.source_snapshot_id
    ) AND e.source = 'extractor' AND e.evidence_status = 'FRESH'
  ) OR EXISTS (
    SELECT 1 FROM edge ed
    WHERE ed.project_id = r.project_id AND ed.evidence_id IN (
      SELECT ev.id FROM evidence ev WHERE ev.chapter_snapshot_id = r.source_snapshot_id
    ) AND ed.source = 'extractor' AND ed.evidence_status = 'FRESH'
  ) THEN 'CURRENT' ELSE 'SUPERSEDED' END,
  0
FROM chapter_refresh_run r;

-- head 指向 CURRENT 的那条（LEGACY 时期没有 intent 竞争，直接填）。
UPDATE extraction_application_head
SET current_application_id = (
  SELECT a.id FROM extraction_application a
  WHERE a.refresh_run_id = extraction_application_head.refresh_run_id
    AND a.status = 'CURRENT'
  LIMIT 1
);

PRAGMA user_version = 21;
