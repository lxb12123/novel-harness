-- 一轮没跑成，那句话要留在**对话里**，不是留在界面状态里。
--
-- ── 2026-08-13 真实现场 ───────────────────────────────────────────────────
-- 作者说了两句话，助手一个字都没有：他配的端点 TLS 全断，那一轮在发出去之前就死了
-- （`chat_message` 只有他那两条，`model_call` 一次都没增加）。屏幕上确实弹过一句提醒，
-- 但它活在浏览器的组件状态里 —— 组件一卸载、他再发一句，那句话就没了。
-- 作者的原话：「有提醒文字，但是过一会文字消失了，**没有必要消失**。」
--
-- 于是这一档要落盘：切走切回都在，翻历史也看得见。
--
-- ── 为什么是 `section` 的第三档，而不是往消息正文里塞一个机器码 ─────────────
--
-- 「消息里带一个编码、投影和 `is_rule` 认它就跳过」是另一条路，而这个仓库**反复栽在
-- 「从字符串反推」上**（`activity.py` 那条「别从 label 的措辞去分辨」、前端那条「拿屏幕上
-- 的人名自己去凑就是从标题反推」）。更要命的是它把两条判据交给了两处代码去记得写：
-- 漏掉哪一处都不报错 —— 漏了投影那处，这句话被喂回给模型（它会以为自己说过）；
-- 漏了 `is_rule` 那处，它被当成一条「规矩」，在作者切到下一章时静默消失
-- （`agent/rules.py` 模块 docstring 末尾那段警告写的就是这个形态）。
--
-- 第三档是**结构判据**：`agent/store.py::load` 按 `section` 分流，这一档天然进不了
-- `Conversation`，于是「不进模型上下文」和「不被当成规矩」**在实现里够不着**，
-- 不是纪律上不许。
--
-- ── 为什么值得为它重建整张表 ─────────────────────────────────────────────
--
-- SQLite 改不了 CHECK，加一档取值只能重建。011 拒绝过一次同样的代价（复合外键），
-- 理由是「为它重建整张表的代价远大于收益」—— **这一次收益是另一回事**：
-- `section = 'history'` 这个条件今天散在四句 SQL 里（读整段 / 数历史条数 / 数 pending /
-- 追加时那道乐观并发闸）。新档走 `section`，那四句**一个字都不用改就仍然是对的**；
-- 换成「加一列布尔」的话，四处都要补上 `AND notice = 0`，而漏掉任何一处都不报错：
-- 漏了那道并发闸，作者每收到一句「这一轮没跑成」，下一轮就会被判成「别的窗口刚往前
-- 走了一步」——一条永远发不出去的对话。
--
-- 附带的第二条：`tests/test_chat_boundary.py` 那张「逐列搜毒」的网扫的就是
-- `chat_message` 的每一列，新档天然在网里；另开一张表的话要记得把网也扩一遍，
-- 而忘了同样不报错。
--
-- ── 这条迁移对已有数据做了什么 ───────────────────────────────────────────
--
-- 逐列平移，一行不增一行不减（`db.py::migrate` 已经先 `VACUUM INTO` 备份过一份）。
-- 新表的列定义是 006 + 011 的合并结果，顺序、默认值、CHECK、UNIQUE 全部照抄；
-- 索引跟着 DROP 一起没了，所以在最后重建。
-- **谁都不引用 `chat_message`**（`PRAGMA foreign_key_list` 全库只有它引用别人），
-- 所以 DROP + RENAME 不会留下悬空的外键。

CREATE TABLE chat_message_rebuilt (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  -- 三档：`Conversation` 的两个字段，加上**不属于 `Conversation`** 的那一档。
  -- `notice` = 说给作者听的一行（这一轮为什么没跑成），措辞的唯一出处是
  -- `agent/loop.py::stop_wording()`。它不进 prompt、不是一条规矩、也不占历史下标。
  section         TEXT NOT NULL CHECK (section IN ('prefix', 'history', 'notice')),
  role            TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
  content         TEXT NOT NULL DEFAULT '',
  tool_calls_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tool_calls_json)),
  tool_call_id    TEXT NOT NULL DEFAULT '',
  chapter         INTEGER CHECK (chapter IS NULL OR chapter >= 1),
  pruned          INTEGER NOT NULL DEFAULT 0 CHECK (pruned IN (0, 1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  revokes_seq     INTEGER CHECK (revokes_seq IS NULL OR revokes_seq >= 0),
  -- 追加那道乐观并发闸的着力点（006 原文）。`notice` 也占一个 `seq`：
  -- **顺序只有这一份真相**，屏幕上「它排在哪两句话中间」是按它数出来的。
  UNIQUE (session_id, seq)
);

INSERT INTO chat_message_rebuilt
  (id, session_id, seq, section, role, content, tool_calls_json, tool_call_id,
   chapter, pruned, created_at, revokes_seq)
SELECT
   id, session_id, seq, section, role, content, tool_calls_json, tool_call_id,
   chapter, pruned, created_at, revokes_seq
FROM chat_message;

DROP TABLE chat_message;
ALTER TABLE chat_message_rebuilt RENAME TO chat_message;
CREATE INDEX idx_chat_message ON chat_message(session_id, seq);

PRAGMA user_version = 12;
