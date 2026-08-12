-- 候选稿：**起草的产物先落在这儿，不落在书里**（ADR 0022）。
--
-- ADR 0021 那一版里落盘是起草的副作用：写完一稿就直接进第 N 章。一批三稿的真实行为
-- 因此是「第一稿落盘 ⇒ 磁盘变了 ⇒ 后两稿手里的底稿全过期 ⇒ 被 sha 闸拒掉」——作者要三版、
-- 拿到一版、付了三份钱。那道闸没写错，**是「落盘是起草的副作用」这个机制错了**。
--
-- 所以生成和落盘拆成两个动作，中间需要一个落点。这张表就是那个落点。
--
-- ── 它为什么既不是正文、也不是对话 ───────────────────────────────────────
--
--   * **不是正文**（边界三 / ADR 0007）：一稿还没被任何人选中，它不是书的一部分。
--     `chapter_snapshot` 里一行都不许为它多出来——那会让「哪一份正文是真的」在
--     版本抽屉里当场有第二个答案，而且是作者看得见的那一个。
--   * **不是对话**：跟模型说话的接口是**无状态**的，每一轮把整个消息数组从头重发。
--     三稿 ≈ 9,000 字进了对话，就会**每一轮都被重发**直到会话结束，而默认没有任何东西
--     会去拿掉它。对话里只放 id + 定长预览 + 那一稿的自述（`agent/candidates.py`）。
--
-- ── 每一列都是「模型自己产出的东西」或纯量，一列都不许装上下文 ─────────────
--
-- **这张表是第五个落盘面**，而边界一那条不对称在这儿同样成立：候选表要是装了秘密原文，
-- 它和对话历史一样不可回收（ADR 0022 结尾）。所以这里没有 prompt、没有约束清单、
-- 没有 `must_not_reveal`、没有 params_json——那几样都是「上下文」，一旦进了表，
-- 一次 `SELECT *` 就把它们端出来了。
-- `tests/test_draft_candidates.py` 拿一本带 `props.twist` / `plot_note` /
-- `SecretDetail.description` 的书跑完整链路，然后**逐列**搜那几种毒；
-- 在出参上搜干净和在表里搜干净不是一回事。

CREATE TABLE draft_candidate (
  id             TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_number INTEGER NOT NULL CHECK (chapter_number >= 1),
  -- 这一章的第几稿。**作者认得的那个说法**（「第 2 稿」），模型也拿它跟作者讲话——
  -- 裸 id（`draft:01J…`）是机器码，摆到小说作者脸上就是屏幕守卫要咬的那一种。
  -- 取 MAX+1 而不是 COUNT+1：清理掉几行旧稿之后 COUNT 会把号重发一遍。
  ordinal        INTEGER NOT NULL CHECK (ordinal >= 1),
  -- 一稿正文，**不含章标题**：那一行是切章的锚，属于作者（ADR 0021 的范围限制）。
  body           TEXT NOT NULL,
  -- 写这一稿的那个模型自己的一句话（「这一版更冷，删掉了那段回忆」）。
  -- **引擎全程没给散文打过分**（ADR 0005 一个字没破）：是写它的模型在说自己写了什么，
  -- 而它是在同一次调用里白送的三十个字。空 = 它这次没说，**不许替它编一句**。
  note           TEXT NOT NULL DEFAULT '',
  units          INTEGER NOT NULL DEFAULT 0 CHECK (units >= 0),
  -- 起草那一刻磁盘上那一章的 sha256。**落盘时的乐观闸比对的就是它**（ADR 0021）：
  -- 拆成两个动作之后，「起草时依据的是哪一份」必须跟着候选一起活到落盘那一刻，
  -- 否则闸就只能拿「现在」跟「现在」比，也就是永远放行。
  -- NULL = 起草那会儿这一章还不存在（那一档落盘照旧拒，由作者去建章）。
  base_sha256    TEXT,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  -- 写进书里的那一刻。NULL = 还没进书。**它不是「被选中」的标志**——
  -- 作者可以在版本历史里把它退回去，那时这一行仍然记着「它确实被写进去过」。
  landed_at      TEXT
);

-- 列表按「最近的在前」，和会话列表同一个形状。
CREATE INDEX idx_draft_candidate ON draft_candidate(project_id, created_at DESC, id DESC);
-- 「这一章有哪几稿」+ 下一个 ordinal 取 MAX 都走它。
CREATE INDEX idx_draft_candidate_chapter ON draft_candidate(project_id, chapter_number, ordinal);

PRAGMA user_version = 7;
