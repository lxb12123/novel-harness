-- 「这次调用是为哪一章花的」—— 让账自己答得出来，不再靠反查。
--
-- `activity._call_chapter` 今天是从 `extraction_run` / `chapter_summary` **反查**的
-- （`model_call` 是通用的调用审计，自己没有章号）。那条路对抽取和章节总结成立，
-- 对**起草**不成立——起草没有这样一张业务表，于是日志页上那一行是
-- 「一次调用，1234 token」：作者看得见花了钱，**看不见花在哪儿**。
--
-- ── 为什么加列而不是给起草也建一张业务表 ──────────────────────────────
-- 那张表除了被反查之外没有第二个用途，是为了迁就一句设计立场而造的。
-- 加列之后抽取和总结那两条反查也可以退休，少一层间接。
--
-- ⚠️ **旧行是 NULL，不是 0。** 作者库里已有 100 行 `model_call`，它们的章号只能继续
-- 靠反查拿到——所以 `_call_chapter` **必须保留反查作为兜底**，直接改成只读这一列
-- 会让那 100 行的章号当场消失（而且不报错）。
ALTER TABLE model_call ADD COLUMN chapter_number INTEGER
  CHECK (chapter_number IS NULL OR chapter_number >= 1);

PRAGMA user_version = 9;
