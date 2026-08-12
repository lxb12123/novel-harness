-- 模式二（agent harness）的会话持久化 —— ADR 0019「为什么不是图编排」的落点。
--
-- 线性 tool loop 的执行态**只有两样**：一串 message，加上「哪几个 tool_call 还缺
-- tool_result」。后者不是一列，是前者的一个查询（`Conversation.pending_calls`）——
-- 所以这里没有 status、没有 step、没有 checkpoint blob。加任何一列「跑到哪一步了」
-- 都会造出第二份执行态，而两份迟早对不上，且对不上的时候没有任何东西会报错。
--
-- ── 这两张表的唯一正确性判据 ─────────────────────────────────────────────
--
-- **读回来重建出的 `Conversation` 必须和存进去之前那个逐字节相同。**
-- 不相同 = resume 之后模型看到的是另一段历史，**而没有任何东西会报错**：产出的是
-- 一段读起来完全正常、只是基于一段被悄悄改写过的历史的回答。所以每一列都是
-- `AgentMessage` 上某个字段的原样落盘，一个都不许由代码在读回时「重新生成」：
--
--   * `section` —— `Conversation` 的两个字段（`prefix` / `messages`）哪一个。
--     **稳定前缀也逐字存**，不在读回时由 `AGENT_SYSTEM_PROMPT` 重新拼：那个常量会改，
--     而改了之后旧会话的历史就被静默换掉了（边界六说的「钉死在 context 里」的反面，
--     一样坏）。
--   * `chapter` —— 这条工具返回绑第几章（投影的过滤判据，ADR 0019 边界五）。
--     `NULL` = 不绑章号，**和「第 0 章」是两件事**，所以是可空 INTEGER 不是 0 默认值。
--   * `pruned` —— 这条工具返回已经被剪成占位。读回来当成真返回 = 模型以为工具
--     真回了那么一句。
--
-- ── 边界三：正文留磁盘，这里存的是对话 ───────────────────────────────────
--
-- 这两张表里**没有一列是正文的落点**，也没有任何读路径把它们当成「第 N 章是什么」的
-- 答案：正文的真相源在磁盘上（ADR 0007），章节快照的锚在 `chapter_snapshot`。
-- 一稿还没被作者接受的草稿留在对话里（它就是助手说过的一段话），**接受 = 写进磁盘**，
-- 走既有的 `PUT …/chapters/{n}/text`；`chapter_snapshot` 的
-- `UNIQUE (chapter_id, text_sha256)` 让那一步天然幂等。
-- **绝不能反过来**：把未接受的草稿写进 `chapter_snapshot`，它就会出现在版本抽屉里，
-- 「哪一份正文是真的」当场有了第二个答案，而且是作者在界面上看得见的那一个。

CREATE TABLE chat_session (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  -- 作者给这段对话起的名字。空 = 还没起（界面自己说「新的对话」），
  -- **不在这里编一个默认标题**：编出来的标题和作者起的长得一样。
  title      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  -- 列表按它排序（最近说过话的在前）。每追加一批消息更新一次。
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_chat_session ON chat_session(project_id, updated_at DESC, id DESC);

CREATE TABLE chat_message (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  -- **顺序的唯一真相。** 不能靠 created_at：同一轮里几条消息落在同一毫秒是常态，
  -- 而顺序错了的对话在 OpenAI 兼容的 wire 上是 400（一条带 tool_calls 的 assistant
  -- 必须紧跟着被同样多条 tool 消息接住）。也不能靠 rowid：那是实现细节。
  seq             INTEGER NOT NULL,
  -- `Conversation` 的哪一个字段。见文件头。
  section         TEXT NOT NULL CHECK (section IN ('prefix', 'history')),
  role            TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
  content         TEXT NOT NULL DEFAULT '',
  -- `[{"id","name","arguments"}]`。`arguments` 是模型生成的原始字符串，**可以不是
  -- 合法 JSON**（流式下它是一串 delta 拼起来的，截断真的会发生）——所以它是这个数组里
  -- 的一个**字符串值**，不是一个嵌套对象。`json_valid` 校的是外面这一层。
  tool_calls_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tool_calls_json)),
  tool_call_id    TEXT NOT NULL DEFAULT '',
  chapter         INTEGER CHECK (chapter IS NULL OR chapter >= 1),
  pruned          INTEGER NOT NULL DEFAULT 0 CHECK (pruned IN (0, 1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  -- 追加是**乐观并发**的着力点：作者在两个标签页里各跑一轮时，后到的那一次
  -- 会撞上这条 UNIQUE 而不是把两轮交织成一段谁也读不懂的历史。
  UNIQUE (session_id, seq)
);

CREATE INDEX idx_chat_message ON chat_message(session_id, seq);

PRAGMA user_version = 6;
