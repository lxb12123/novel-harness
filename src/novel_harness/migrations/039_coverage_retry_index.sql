-- 039：同一批缺口允许再下一张单（有限次自动重试，ADR 0052 补记二 / `chapter_refresh.MAX_AUTO_RETRIES`）。
--
-- 018 给 coverage 那一类单钉了一条唯一索引：(run, workflow, epoch, kind, mask) 只许一行——
-- 「同一份正文、同一套规则、缺同一批分支」永远只有一张单，FAILED 了也不再下。
-- 那条纪律守的是「别替一个坏掉的模型每半小时再付一次钱」；真书上它的另一面露出来了：
-- 模型不是坏的，是那条路由上思考把预算吃空了，修好之后 31 章红着的单一张都不会再下。
--
-- 现在重试是**新的一行**（trigger_key 带着第几次：`coverage:6`、`coverage:6#1`…），旧的原样
-- 留着，所以这条索引改成非唯一（表上 (run, workflow, epoch, kind, trigger_key) 那条 UNIQUE
-- 照旧管幂等）。次数上限不在库里，在 `ensure_refresh_coverage` 数——库只管留得住每一张。

DROP INDEX IF EXISTS idx_refresh_attempt_coverage;
CREATE INDEX idx_refresh_attempt_coverage
  ON chapter_refresh_attempt(run_id, workflow_version, ruleset_epoch, trigger_kind, missing_branch_mask)
  WHERE trigger_kind = 'coverage';

PRAGMA user_version = 39;
