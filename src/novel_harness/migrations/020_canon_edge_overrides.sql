-- 020：自动 Canon 边的可逆纠错（ADR 0032 / 计划 Task 8）。
--
-- `LOCATED_AT / HAS_STATE / RELATED_TO` 可以自动进 Canon（ADR 0020），所以必须先有
-- 稳定 ID 的读取 / 修改 / 软撤回 / 改归属 / 审计入口——「先可逆、后自动」。
--
-- ── override 是什么 ─────────────────────────────────────────────────────
-- append-only 审计表：每个语义槽至多一条 ACTIVE override。
--   · ACTIVE override 的 replacement_edge_id 非空 = 当前解释由作者接管；
--   · NULL = 撤回 tombstone。
-- `edge.source/evidence` 永远表示**最初来源**（extractor/AUTHOR）；
-- 当前归属由 ACTIVE override 投影（A→B→A 恢复旧 identity 时 origin 保留）。

CREATE TABLE canon_edge_override (
  id                     TEXT PRIMARY KEY,
  project_id             TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  slot_key               TEXT NOT NULL,
  edge_type              TEXT NOT NULL REFERENCES edge_type(type),
  source_edge_id         TEXT NOT NULL REFERENCES edge(id),
  replacement_edge_id    TEXT REFERENCES edge(id),
  before_props_json      TEXT NOT NULL CHECK (json_valid(before_props_json)),
  after_props_json       TEXT CHECK (json_valid(after_props_json)),
  action                 TEXT NOT NULL CHECK (action IN ('EDIT','REASSIGN','RETRACT')),
  decision_log_id        TEXT,
  status                 TEXT NOT NULL DEFAULT 'ACTIVE'
                          CHECK (status IN ('ACTIVE','SUPERSEDED')),
  supersedes_override_id TEXT REFERENCES canon_edge_override(id),
  created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- 每个语义槽至多一条 ACTIVE override（作者接管当前解释）。
CREATE UNIQUE INDEX idx_canon_edge_override_active
  ON canon_edge_override(project_id, slot_key) WHERE status = 'ACTIVE';

PRAGMA user_version = 20;
