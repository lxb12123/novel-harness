-- 017：chapter.snapshot_generation —— 当前正文的**单调 generation**（ABA 防护）。
--
-- ── 为什么要有这一列 ─────────────────────────────────────────────────────
--
-- 保存后所有自动任务（验证 / 总结 / 抽取）都绑定「第几版正文」。没有单调 generation，
-- S1→S2→S1 会让第一轮 S1 的晚到任务通过 snapshot/hash 等值 CAS 复活（ABA）。
-- 判据是 `text_sha256` 精确等值，不是 `snapshot_created`（同内容重存不加，
-- 从 S2 还原历史 S1 要加）。
--
-- ── 既有数据回填 ──────────────────────────────────────────────────────────
-- 每个已有 chapter 行都有一条当前快照（text_sha256 NOT NULL），回填 1。
-- 之后由 put_chapter 在 hash 切换时递增；Task 3 会继续在本迁移文件里补
-- validation_ruleset_state / chapter_refresh_run / chapter_refresh_attempt。
ALTER TABLE chapter ADD COLUMN snapshot_generation INTEGER NOT NULL DEFAULT 0;
UPDATE chapter SET snapshot_generation = 1 WHERE snapshot_generation = 0;

PRAGMA user_version = 17;
