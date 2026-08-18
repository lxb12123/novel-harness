-- 023：项目级确定性自定义验证规则（ADR 0028 补充 / 计划 Task 13）。
--
-- 第一期只支持一条**确定性命中模板** `forbidden_literal`：作者写一段必须出现的
-- 字（literal），规则逐段精确匹配它——**不执行作者代码**，不把自然语言交给 LLM。
-- 语义 Validator 仍不在本任务范围（ADR 0005：需要理解「这句话什么意思」的规则
-- 一律不进 v1）。
--
-- `required_literal`（「这段字**没有**出现」）**明确不做**：一段 text 里「没有 X」
-- 没有合法的 `(para_index, quote_text, occurrence_k)` 锚——强行实现会破坏
-- ADR 0006 的定位契约。
--
-- 复用 017 已存在的 `validation_ruleset_state`：本迁移**不创建**第二张状态表；
-- 任何规则语义变化都在写规则行的同一事务 `epoch += 1` 并重算 system + custom
-- 总 hash。R2/R3 仍在代码 catalog 里，不复制进数据库。

CREATE TABLE validation_rule (
  id                    TEXT PRIMARY KEY,
  project_id            TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  title                 TEXT NOT NULL,
  template              TEXT NOT NULL CHECK (template IN ('forbidden_literal')),
  enabled               INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  blocks_downstream     INTEGER NOT NULL DEFAULT 1 CHECK (blocks_downstream IN (0,1)),
  -- 模板参数（本期只有 forbidden_literal 的 `literal`）。_json 校验 + 非空。
  config_json           TEXT NOT NULL CHECK (
                          json_valid(config_json)
                          AND json_type(config_json) = 'object'
                          AND length(config_json) > 2
                        ),
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (project_id, id)
);
CREATE INDEX idx_validation_rule ON validation_rule(project_id, enabled);

PRAGMA user_version = 23;
