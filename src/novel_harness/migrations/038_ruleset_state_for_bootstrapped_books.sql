-- 038：给「出生时没领到 ruleset 基线」的书补上那一行。
--
-- ── 哪些书没有 ─────────────────────────────────────────────────────────────
--
-- 017/018 定的规矩是每个项目从出生那一刻起就有一行 `validation_ruleset_state`，
-- 但那一行只在 `project.create()` 里写，而工作台的「新建 / 导入」（`onboarding.
-- bootstrap_project()`）走的是 `project.insert()`——2026-08-04 起从浏览器建的每一本书
-- 都没有它。症状（桌面版第一次上手时作者撞上的，2026-09-13）：「分析本章」500、
-- 保存后的整理和 30 分钟扫描静默跳过，屏幕上只是按钮闪一下。
-- `project.insert()` 现在自己写这一行；这条迁移补的是已经建出来的那些。
--
-- ── 补成当前值，不是历史值 ─────────────────────────────────────────────────
--
-- 这些书从没在任何 epoch 上跑过 attempt，没有「旧 epoch 的账」要认，站到当前 epoch
-- 上就是对的。下面两个字面量必须与 `catalog.CURRENT_RULESET_EPOCH` / `CURRENT_RULESET_HASH`
-- 一致——`tests/test_migrate.py` 断言两边相等，同 018 / 031 / 035 那条纪律：
-- **字面量是唯一真相的冻结快照，改 catalog.py 不改这里是没用的，迁移已经跑过的库
-- 不会重新执行**。

INSERT INTO validation_ruleset_state (project_id, epoch, ruleset_hash)
SELECT id, 3, '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
FROM project
WHERE id NOT IN (SELECT project_id FROM validation_ruleset_state);

PRAGMA user_version = 38;
