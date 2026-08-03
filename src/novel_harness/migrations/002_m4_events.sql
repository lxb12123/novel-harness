-- M4: evidence-backed event hyperedges and background extraction bookkeeping.
--
-- Events deliberately do not extend node / NodeLabel.  A story_event is an
-- independent hyperedge whose incidence is stored in the three event_* tables.

-- Compound parent keys let every new relationship enforce project ownership in
-- the foreign key itself, matching the node/edge pattern established by 001.
CREATE UNIQUE INDEX idx_evidence_id_project ON evidence(id, project_id);
CREATE UNIQUE INDEX idx_edge_id_project_scope
  ON edge(id, project_id, information_scope);
CREATE UNIQUE INDEX idx_proposal_set_id_project ON proposal_set(id, project_id);


-- ═══════════════════════════════════════════════════════════════════════════
-- Event hyperedges and incidence
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE story_event (
  id                    TEXT PRIMARY KEY,
  project_id            TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_number        INTEGER NOT NULL,
  summary               TEXT NOT NULL,
  valid_from_chapter    INTEGER NOT NULL,
  valid_to_chapter      INTEGER,
  information_scope     TEXT NOT NULL,
  status                TEXT NOT NULL DEFAULT 'ACTIVE',
  confidence            REAL,
  source                TEXT NOT NULL DEFAULT 'extractor',
  evidence_id           TEXT NOT NULL,
  evidence_status       TEXT NOT NULL DEFAULT 'FRESH',
  derived_from_event_id TEXT,
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  UNIQUE (id, project_id),
  UNIQUE (id, project_id, information_scope),
  -- One located quote produces at most one event in each scope in this first slice.
  UNIQUE (project_id, evidence_id, information_scope),
  CHECK (chapter_number >= 1),
  CHECK (valid_from_chapter >= 1),
  CHECK (chapter_number = valid_from_chapter),
  CHECK (length(summary) > 0),
  CHECK (information_scope IN ('CANON','PROVISIONAL','PLANNED','REJECTED')),
  CHECK (status IN ('ACTIVE','RETRACTED')),
  CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
  CHECK (evidence_status IN ('FRESH','STALE')),
  CHECK (valid_to_chapter IS NULL OR valid_to_chapter > valid_from_chapter),
  CHECK (derived_from_event_id IS NULL OR derived_from_event_id <> id),
  FOREIGN KEY (evidence_id, project_id)
    REFERENCES evidence(id, project_id),
  FOREIGN KEY (derived_from_event_id, project_id)
    REFERENCES story_event(id, project_id)
);

CREATE INDEX idx_story_event_chapter
  ON story_event(project_id, information_scope, chapter_number, id);
CREATE INDEX idx_story_event_derived
  ON story_event(derived_from_event_id) WHERE derived_from_event_id IS NOT NULL;

CREATE TABLE event_participant (
  event_id       TEXT NOT NULL,
  project_id     TEXT NOT NULL,
  character_id   TEXT NOT NULL,
  character_label TEXT NOT NULL DEFAULT 'Character' CHECK (character_label = 'Character'),
  PRIMARY KEY (event_id, character_id),
  FOREIGN KEY (event_id, project_id)
    REFERENCES story_event(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (character_id, project_id, character_label)
    REFERENCES node(id, project_id, label) ON DELETE CASCADE
);

CREATE INDEX idx_event_participant_character
  ON event_participant(project_id, character_id, event_id);

CREATE TABLE event_knower (
  event_id             TEXT NOT NULL,
  project_id           TEXT NOT NULL,
  character_id         TEXT NOT NULL,
  character_label      TEXT NOT NULL DEFAULT 'Character' CHECK (character_label = 'Character'),
  valid_from_chapter   INTEGER NOT NULL,
  valid_to_chapter     INTEGER,
  information_scope   TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'ACTIVE',
  evidence_id          TEXT NOT NULL,
  evidence_status      TEXT NOT NULL,
  created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (event_id, character_id, valid_from_chapter, information_scope),
  CHECK (valid_from_chapter >= 1),
  CHECK (valid_to_chapter IS NULL OR valid_to_chapter > valid_from_chapter),
  CHECK (information_scope IN ('CANON','PROVISIONAL','PLANNED','REJECTED')),
  CHECK (status IN ('ACTIVE','RETRACTED')),
  CHECK (evidence_status IN ('FRESH','STALE')),
  FOREIGN KEY (event_id, project_id)
    REFERENCES story_event(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (character_id, project_id, character_label)
    REFERENCES node(id, project_id, label) ON DELETE CASCADE,
  FOREIGN KEY (evidence_id, project_id)
    REFERENCES evidence(id, project_id)
);

-- Knowledge of an event can be learned later and from different evidence; the
-- only temporal relationship is that it cannot begin before the event itself.
CREATE TRIGGER event_knower_not_before_event_insert
BEFORE INSERT ON event_knower
WHEN NEW.valid_from_chapter < (
  SELECT chapter_number
  FROM story_event
  WHERE id = NEW.event_id AND project_id = NEW.project_id
)
BEGIN
  SELECT RAISE(ABORT, 'event_knower cannot predate its story_event');
END;

CREATE TRIGGER event_knower_not_before_event_update
BEFORE UPDATE OF event_id, project_id, valid_from_chapter ON event_knower
WHEN NEW.valid_from_chapter < (
  SELECT chapter_number
  FROM story_event
  WHERE id = NEW.event_id AND project_id = NEW.project_id
)
BEGIN
  SELECT RAISE(ABORT, 'event_knower cannot predate its story_event');
END;

-- 子行与父行两侧都要守住同一不变量；否则移动事件章节会绕过上面两条触发器。
CREATE TRIGGER story_event_not_after_existing_knower_update
BEFORE UPDATE OF chapter_number, valid_from_chapter ON story_event
WHEN EXISTS (
  SELECT 1
  FROM event_knower
  WHERE event_id = OLD.id
    AND project_id = OLD.project_id
    AND valid_from_chapter < NEW.chapter_number
)
BEGIN
  SELECT RAISE(ABORT, 'story_event cannot move after an existing event_knower');
END;

CREATE INDEX idx_event_knower_character
  ON event_knower(project_id, character_id, information_scope, valid_from_chapter);

CREATE TABLE event_reveal (
  event_id     TEXT NOT NULL,
  project_id   TEXT NOT NULL,
  secret_id    TEXT NOT NULL,
  secret_label TEXT NOT NULL DEFAULT 'Secret' CHECK (secret_label = 'Secret'),
  PRIMARY KEY (event_id, secret_id),
  FOREIGN KEY (event_id, project_id)
    REFERENCES story_event(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (secret_id, project_id, secret_label)
    REFERENCES node(id, project_id, label) ON DELETE CASCADE
);


-- ═══════════════════════════════════════════════════════════════════════════
-- Proposal clusters link only to provisional facts
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE proposal_set
  ADD COLUMN chapter_number INTEGER CHECK (chapter_number IS NULL OR chapter_number >= 1);
ALTER TABLE proposal_set
  ADD COLUMN snapshot_id TEXT REFERENCES chapter_snapshot(id);
ALTER TABLE proposal_set
  ADD COLUMN base_canon_version INTEGER NOT NULL DEFAULT 0 CHECK (base_canon_version >= 0);
ALTER TABLE proposal_set ADD COLUMN schema_version TEXT;
ALTER TABLE proposal_set ADD COLUMN prompt_hash TEXT;

CREATE TRIGGER proposal_snapshot_coherent_insert
BEFORE INSERT ON proposal_set
WHEN NEW.snapshot_id IS NOT NULL AND NOT EXISTS (
  SELECT 1
  FROM chapter_snapshot AS snapshot
  JOIN chapter AS chapter ON chapter.id = snapshot.chapter_id
  WHERE snapshot.id = NEW.snapshot_id
    AND chapter.project_id = NEW.project_id
    AND (NEW.chapter_number IS NULL OR chapter.number = NEW.chapter_number)
)
BEGIN
  SELECT RAISE(ABORT, 'proposal snapshot must belong to its project and chapter');
END;

CREATE TRIGGER proposal_snapshot_coherent_update
BEFORE UPDATE OF project_id, chapter_number, snapshot_id ON proposal_set
WHEN NEW.snapshot_id IS NOT NULL AND NOT EXISTS (
  SELECT 1
  FROM chapter_snapshot AS snapshot
  JOIN chapter AS chapter ON chapter.id = snapshot.chapter_id
  WHERE snapshot.id = NEW.snapshot_id
    AND chapter.project_id = NEW.project_id
    AND (NEW.chapter_number IS NULL OR chapter.number = NEW.chapter_number)
)
BEGIN
  SELECT RAISE(ABORT, 'proposal snapshot must belong to its project and chapter');
END;

CREATE TABLE proposal_event (
  proposal_id      TEXT NOT NULL,
  project_id       TEXT NOT NULL,
  event_id         TEXT NOT NULL,
  information_scope TEXT NOT NULL DEFAULT 'PROVISIONAL'
    CHECK (information_scope = 'PROVISIONAL'),
  PRIMARY KEY (proposal_id, event_id),
  FOREIGN KEY (proposal_id, project_id)
    REFERENCES proposal_set(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (event_id, project_id, information_scope)
    REFERENCES story_event(id, project_id, information_scope) ON DELETE CASCADE
);

CREATE TABLE proposal_edge (
  proposal_id      TEXT NOT NULL,
  project_id       TEXT NOT NULL,
  edge_id          TEXT NOT NULL,
  information_scope TEXT NOT NULL DEFAULT 'PROVISIONAL'
    CHECK (information_scope = 'PROVISIONAL'),
  PRIMARY KEY (proposal_id, edge_id),
  FOREIGN KEY (proposal_id, project_id)
    REFERENCES proposal_set(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (edge_id, project_id, information_scope)
    REFERENCES edge(id, project_id, information_scope) ON DELETE CASCADE
);


-- ═══════════════════════════════════════════════════════════════════════════
-- Background extraction runs
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE extraction_run (
  id                    TEXT PRIMARY KEY,
  project_id            TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_number        INTEGER NOT NULL,
  snapshot_id           TEXT NOT NULL REFERENCES chapter_snapshot(id),
  status                TEXT NOT NULL DEFAULT 'PENDING',
  errors_json           TEXT NOT NULL DEFAULT '[]',
  valid_event_count     INTEGER NOT NULL DEFAULT 0,
  discarded_event_count INTEGER NOT NULL DEFAULT 0,
  proposal_count        INTEGER NOT NULL DEFAULT 0,
  model_call_id         TEXT REFERENCES model_call(id) ON DELETE SET NULL,
  schema_version        TEXT NOT NULL,
  prompt_hash           TEXT NOT NULL,
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  started_at            TEXT,
  finished_at           TEXT,
  UNIQUE (project_id, snapshot_id, schema_version, prompt_hash),
  CHECK (chapter_number >= 1),
  CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
  CHECK (json_valid(errors_json)),
  CHECK (valid_event_count >= 0),
  CHECK (discarded_event_count >= 0),
  CHECK (proposal_count >= 0)
);

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

CREATE INDEX idx_extraction_run_status
  ON extraction_run(project_id, status, created_at);

-- 快照改挂到另一章时，只拦会让现有提案/抽取运行失去项目或章节一致性的更新。
CREATE TRIGGER chapter_snapshot_children_coherent_update
BEFORE UPDATE OF chapter_id ON chapter_snapshot
WHEN EXISTS (
  SELECT 1
  FROM proposal_set
  WHERE snapshot_id = OLD.id
    AND NOT EXISTS (
      SELECT 1
      FROM chapter
      WHERE id = NEW.chapter_id
        AND project_id = proposal_set.project_id
        AND (
          proposal_set.chapter_number IS NULL
          OR number = proposal_set.chapter_number
        )
    )
)
OR EXISTS (
  SELECT 1
  FROM extraction_run
  WHERE snapshot_id = OLD.id
    AND NOT EXISTS (
      SELECT 1
      FROM chapter
      WHERE id = NEW.chapter_id
        AND project_id = extraction_run.project_id
        AND number = extraction_run.chapter_number
    )
)
BEGIN
  SELECT RAISE(ABORT, 'chapter_snapshot update would break extraction coherence');
END;

-- 章节的顺序号或项目归属同样是快照一致性的一部分，不能从父行方向绕过。
CREATE TRIGGER chapter_snapshot_consumers_coherent_chapter_update
BEFORE UPDATE OF number, project_id ON chapter
WHEN EXISTS (
  SELECT 1
  FROM chapter_snapshot
  JOIN proposal_set ON proposal_set.snapshot_id = chapter_snapshot.id
  WHERE chapter_snapshot.chapter_id = OLD.id
    AND (
      proposal_set.project_id <> NEW.project_id
      OR (
        proposal_set.chapter_number IS NOT NULL
        AND proposal_set.chapter_number <> NEW.number
      )
    )
)
OR EXISTS (
  SELECT 1
  FROM chapter_snapshot
  JOIN extraction_run ON extraction_run.snapshot_id = chapter_snapshot.id
  WHERE chapter_snapshot.chapter_id = OLD.id
    AND (
      extraction_run.project_id <> NEW.project_id
      OR extraction_run.chapter_number <> NEW.number
    )
)
BEGIN
  SELECT RAISE(ABORT, 'chapter update would break extraction coherence');
END;


PRAGMA user_version = 2;
