-- 023：别名生命周期 —— ACTIVE 过滤、证据有效性、改归属与撤回 (ADR 0031 / Task 11)。
--
-- ── 这条迁移要补的洞 ─────────────────────────────────────────────────────
-- 之前 alias 只有 (project_id, node_id, surface) 唯一 + kind；没有「谁写的、
-- 现在还有效吗、证据在哪」——机器别名一旦落库就只能手工删（甚至没有软撤回），
-- 而 §4.5 的界面是一排可以改 / 撤回 / 改归属的 chip。这一版把 alias 变成有
-- 生命周期的实体：
--   · `source`：extractor（机器自动）／author（作者声明/修改）；canonical 是 node 本名
--     （upsert_node 独占，仍不可改）；
--   · `status`：ACTIVE 参与解析，RETRACTED 是软撤回 tombstone（历史保留、允许重登记）；
--   · `derived_from_alias_id`：作者改机器别名 → 新 author 行指回机器行；
--   · `alias_evidence`：机器别名至少一条 FRESH 证据才参与解析（§4.5 的自动条件）；
--     作者别名不要求伪造证据。
--
-- ── 迁移口径（既有数据）──────────────────────────────────────────────────
-- 上线前 alias 只来自声明/作者入口，没有自动抽取链 → 全部回填
-- `source=author, status=ACTIVE`，不伪造 alias_evidence。canonical 行照旧
-- 是 node 本名的索引项，参与解析但不受别名接口改（§4.5）。

-- ══════════════════════════════════════════════════════════════════════════
-- 1. alias：重建（SQLite 不能改 CHECK / UNIQUE）
-- ══════════════════════════════════════════════════════════════════════════

PRAGMA legacy_alter_table = ON;

CREATE TABLE alias_new (
  id                    TEXT PRIMARY KEY,
  project_id            TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  node_id               TEXT NOT NULL,
  surface               TEXT NOT NULL,
  kind                  TEXT NOT NULL,
  usable_for_rules      INTEGER NOT NULL DEFAULT 1,
  source                TEXT NOT NULL DEFAULT 'author'
                        CHECK (source IN ('extractor','author')),
  status                TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE','RETRACTED')),
  derived_from_alias_id TEXT REFERENCES alias(id),
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (usable_for_rules IN (0,1)),
  CHECK (kind IN ('canonical','alias','nickname','title')),
  CHECK (usable_for_rules = 0 OR length(surface) >= 2),
  CHECK (derived_from_alias_id IS NULL OR derived_from_alias_id <> id),
  FOREIGN KEY (node_id, project_id) REFERENCES node(id, project_id) ON DELETE CASCADE
);

INSERT INTO alias_new (id, project_id, node_id, surface, kind, usable_for_rules)
SELECT id, project_id, node_id, surface, kind, usable_for_rules FROM alias;

DROP TABLE alias;
ALTER TABLE alias_new RENAME TO alias;

-- ACTIVE partial unique：撤回后保留历史并允许重新登记。
CREATE UNIQUE INDEX idx_alias_active
  ON alias(project_id, node_id, surface) WHERE status = 'ACTIVE';
-- 并发自动任务不许把同一 surface 激活到不同人物（插入 guard 见 store 层）。
CREATE UNIQUE INDEX idx_alias_auto_surface
  ON alias(project_id, surface) WHERE status = 'ACTIVE' AND source = 'extractor';

CREATE INDEX idx_alias_surface ON alias(project_id, surface);
CREATE UNIQUE INDEX idx_alias_canonical ON alias(project_id, node_id) WHERE kind = 'canonical';

-- ══════════════════════════════════════════════════════════════════════════
-- 2. alias_evidence：机器别名哪几条证据支撑它
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE alias_evidence (
  alias_id                 TEXT NOT NULL REFERENCES alias(id) ON DELETE CASCADE,
  evidence_id              TEXT NOT NULL REFERENCES evidence(id),
  source_snapshot_id       TEXT NOT NULL REFERENCES chapter_snapshot(id),
  source_generation        INTEGER NOT NULL CHECK (source_generation >= 1),
  extraction_application_id TEXT,
  status                   TEXT NOT NULL DEFAULT 'FRESH'
                           CHECK (status IN ('FRESH','STALE')),
  created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  -- 幂等：同 alias + 同 evidence + 同 generation 只记一次（§5 022）。
  PRIMARY KEY (alias_id, evidence_id, source_generation)
);
CREATE INDEX idx_alias_evidence_snapshot
  ON alias_evidence(source_snapshot_id, status);
CREATE INDEX idx_alias_evidence_alias
  ON alias_evidence(alias_id, status);

-- ══════════════════════════════════════════════════════════════════════════
-- 3. alias_replay_job：别名纠正后的确定性重放 outbox（Task 12 的 dispatcher 消费）
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE alias_replay_job (
  id                    TEXT PRIMARY KEY,
  project_id            TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_id            TEXT NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
  snapshot_id           TEXT NOT NULL REFERENCES chapter_snapshot(id),
  source_generation     INTEGER NOT NULL CHECK (source_generation >= 1),
  analysis_run_id       TEXT REFERENCES extraction_run(id),
  application_id        TEXT REFERENCES extraction_application(id),
  status                TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','SUPERSEDED')),
  lease_owner           TEXT,
  lease_expires_at      TEXT,
  fencing_token         INTEGER NOT NULL DEFAULT 0,
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_alias_replay_pending
  ON alias_replay_job(project_id, status, created_at);

PRAGMA legacy_alter_table = OFF;

PRAGMA user_version = 23;
