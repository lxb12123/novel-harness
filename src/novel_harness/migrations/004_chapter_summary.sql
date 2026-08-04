-- 滚动总结：每章一条机器摘要，作为产品起草「更早章节」背景层。
-- 摘要不是作者确认的事实（不写 decision_log），进 prompt 时明确标注「机器摘要，仅背景」。

CREATE TABLE chapter_summary (
  id             TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_number INTEGER NOT NULL CHECK (chapter_number >= 1),
  summary        TEXT NOT NULL CHECK (length(summary) > 0),
  schema_version TEXT NOT NULL,
  prompt_hash    TEXT NOT NULL,
  model_call_id  TEXT REFERENCES model_call(id),
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  -- 同一章同一 prompt 只付一次调用；换 prompt 版本会得到新行（内容寻址）。
  UNIQUE (project_id, chapter_number, schema_version, prompt_hash)
);

CREATE INDEX idx_chapter_summary_latest
  ON chapter_summary(project_id, chapter_number, created_at DESC, id DESC);

PRAGMA user_version = 4;
