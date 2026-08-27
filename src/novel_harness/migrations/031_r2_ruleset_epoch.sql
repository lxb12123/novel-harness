-- 031：删 R2 FUTURE_LEAK 之后，给所有既有项目递增 ruleset epoch（ADR 0040）。
--
-- ── 为什么必须开新迁移，不能只改 checks/catalog.py ──────────────────────────
--
-- `catalog.py` 模块头写死的规矩：「SYSTEM_RULES 的语义字段以后要改（加规则 / 改
-- 阻断属性 / 换 schema），必须用新迁移为所有项目递增 ruleset_epoch 并重算总
-- hash；不许偷偷改 017 里冻结的那个字面量」。R2 从 SYSTEM_RULES 里删掉，语义
-- 变了：`ruleset_hash()` 会算出一个新数。已经落库的 `validation_ruleset_state`
-- 那一行如果不跟着动，旧项目会拿着「R2+R3 时代」的 epoch/hash 继续跑，
-- Task 5 的 attempt 冻结逻辑会以为规则集没变过——静默地继续按已经不存在的
-- R2 语义记账。
--
-- ── 两个数从哪儿来 ──────────────────────────────────────────────────────
--
-- `checks/catalog.py` 新增了两个常量：`CURRENT_RULESET_EPOCH = 2`（手动维护，
-- 这是本仓库第一次真的递增它——018 落地以来 SYSTEM_RULES 从没变过语义，
-- 一直停在 epoch=1）、`CURRENT_RULESET_HASH`（`ruleset_hash()` 活算出来的，
-- 对着当前只剩 R3 的 SYSTEM_RULES）。下面 UPDATE 里的字面量必须与这两个常量
-- 一致——`tests/test_migrate.py` 断言两边相等，同 018 对 `SYSTEM_RULESET_
-- V1_HASH` 的那条纪律：**字面量是唯一真相的冻结快照，改 catalog.py 不改这里
-- 是没用的，迁移已经跑过的库不会重新执行**。
--
-- `SYSTEM_RULESET_V1_HASH`（018 冻的那个历史值）原地不动——它记的是「epoch=1
-- 那一刻」，不是「现在」，031 不碰它，也不碰 018 一个字节。
--
-- ── 为什么是 UPDATE 全表，不分项目 ─────────────────────────────────────────
--
-- `SYSTEM_RULES` 是**全局**目录，不是项目级配置——018 的注释已经说清楚
-- 「以后要改，必须为所有项目递增」。今天还没有「每个项目自定义系统规则」这回事，
-- 所以这条 UPDATE 对 `validation_ruleset_state` 里的每一行都生效，没有 WHERE。

UPDATE validation_ruleset_state
SET epoch = epoch + 1,
    ruleset_hash = 'b2c0becfe9ef1fee01a8a95897ce7d4110894bf2990f3952f18456fb69db6892',
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');

PRAGMA user_version = 31;
