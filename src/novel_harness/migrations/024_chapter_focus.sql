-- 024：当前章焦点（`chapter_focus`）——总结自治「当前章防抖」的记录（2026-08-18 文档 §3）。
--
-- 每个项目一行：作者**此刻正盯着的章号** + 心跳时间戳。这是唯一的「当前章」信息
-- 来源（保存传的 chapter 是「保存哪一章」，不等于「作者现在眼睛停在哪一章」）。
--
-- 写入方：前端换章 / 开书时打的**免费心跳端点**（`POST /api/projects/{id}/focus`），
-- 不触发任何总结/抽取/付费——它只是位置。读取方：保存触发与 30 分钟调度在决定
-- 「给不给某章补总结/覆写」时，跳过「焦点中的那一章」（心跳未过期的才算数；
-- 超过 TTL 没心跳 = 人不在，解除保护）。
--
-- 「离开才够格、回来不停队」：心跳从本章移到别章（或过期）→ 本章失去保护、
-- 已入队的任务照跑不打断。本表只记位置，不 sel 队列状态。

CREATE TABLE chapter_focus (
  project_id     TEXT PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
  chapter_number INTEGER NOT NULL CHECK (chapter_number >= 1),
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

PRAGMA user_version = 24;
