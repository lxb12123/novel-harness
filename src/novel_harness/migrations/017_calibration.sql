-- ═══════════════════════════════════════════════════════════════════════════
-- 017 · calibration —— 模式二写前校准的非 Canon 派生产物（ADR 0033）
--
-- 编号 017 是**本任务**（calibration）的迁移，紧跟 016 连续落地。
-- 并行任务（保存 → 快照 → 验证 → 总结版本 → Canon 纠错 → 系统通知 →
-- 别名生命周期 → 规则集）原拟用 017–023，与本任务撞号：**合并时必须把并行
-- 迁移整体后移一档（018–024）**。编号约定见 ADR 0033「迁移编号约定」。
--
-- 这两张表**不是 Canon，也不是正文**：可重建、可过期、可撤销，永不进图。
-- 正文的真相源仍是磁盘（ADR 0007），图的真相源仍是 edge/node（ADR 0004）。
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE calibration_artifact (
  -- ULID（EntityType.CALIBRATION）。kind 不同各自计数。
  id                  TEXT PRIMARY KEY,
  -- inspection = CalibrationReport（Agent 可读、不可起草）；
  -- sealed      = SealedCalibration（READY_FOR_DRAFT 才能起草）。
  kind                TEXT NOT NULL CHECK (kind IN ('inspection', 'sealed')),
  project_id          TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter             INTEGER NOT NULL CHECK (chapter >= 1),
  -- 作者 turn 绑定：模型不能自报（ADR 0033：AUTHOR_INTENT 只能来自服务端确认记录）。
  author_turn_id      TEXT NOT NULL,
  author_request_sha256 TEXT NOT NULL,
  -- inspection: OPEN / NEEDS_AUTHOR / STALE
  -- sealed:     READY_FOR_DRAFT / STALE / REVOKED
  status              TEXT NOT NULL CHECK (status IN (
                        'OPEN', 'NEEDS_AUTHOR', 'READY_FOR_DRAFT', 'STALE', 'REVOKED')),
  payload_json        TEXT NOT NULL,
  -- 内容寻址幂等：同一项目同一种类同内容只留一行，Agent resume 后按 ID 取回。
  content_sha256      TEXT NOT NULL,
  source_watermark_json TEXT NOT NULL,
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE UNIQUE INDEX uq_calibration_artifact_content
  ON calibration_artifact (project_id, kind, content_sha256);
CREATE INDEX idx_calibration_artifact_chapter
  ON calibration_artifact (project_id, chapter, created_at);
CREATE INDEX idx_calibration_artifact_turn
  ON calibration_artifact (author_turn_id);

-- ContinuityConflictHandoff 的 producer 侧 outbox。并行通知任务消费它
-- （写 system_notification 与右栏），不在这里再判一次「是否冲突」（ADR 0033）。
CREATE TABLE calibration_handoff_outbox (
  id             TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter        INTEGER NOT NULL CHECK (chapter >= 1),
  calibration_id TEXT NOT NULL,
  author_turn_id TEXT NOT NULL,
  payload_json   TEXT NOT NULL,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  consumed       INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1))
);

CREATE INDEX idx_calibration_outbox_pending
  ON calibration_handoff_outbox (project_id, consumed, created_at);
