-- 035：删 R3 DEAD_SPEAKS 之后，给所有既有项目递增 ruleset epoch（ADR 0042）。
--
-- ── 为什么必须开新迁移，不能只改 checks/catalog.py ──────────────────────────
--
-- 同 031（删 R2 那次）逐字同一个理由：`catalog.py` 模块头写死的规矩是
-- 「SYSTEM_RULES 的语义字段以后要改，必须用新迁移为所有项目递增 ruleset_epoch
-- 并重算总 hash」。R3 从 SYSTEM_RULES 里删掉，语义变了，`ruleset_hash()` 会算出
-- 一个新数。已经落库的 `validation_ruleset_state` 那一行不跟着动的话，旧项目会
-- 拿着「还有 R3」的 epoch/hash 继续跑，Task 5 的 attempt 冻结逻辑会以为规则集没变过
-- ——静默地继续按一条已经不存在的规则记账。
--
-- ── 这一次的 hash 是空目录的 hash ─────────────────────────────────────────
--
-- `SYSTEM_RULES` 现在是空元组，`ruleset_semantic_json()` 出来就是 `[]`，
-- 它的 sha256 是下面这个数。**空目录不是异常状态**：作者自己加的规则不进这个
-- hash（它们在 `validation_rule` 表里，各自带 epoch），系统这一侧确实什么都没有了。
--
-- 下面 UPDATE 里的字面量必须与 `catalog.CURRENT_RULESET_EPOCH` / `CURRENT_RULESET_HASH`
-- 一致——`tests/test_migrate.py` 断言两边相等，同 018 / 031 的那条纪律：
-- **字面量是唯一真相的冻结快照，改 catalog.py 不改这里是没用的，迁移已经跑过的库
-- 不会重新执行**。`SYSTEM_RULESET_V1_HASH`（018 冻的历史值）原地不动。

UPDATE validation_ruleset_state
SET epoch = epoch + 1,
    ruleset_hash = '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now');

PRAGMA user_version = 35;
