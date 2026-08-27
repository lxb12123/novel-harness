-- 030：project.language —— 书用什么语言写（国际化第一批 ②）。
--
-- **默认从正文推，不让作者填**（约束 6「不让作者填章号」同一个方向，ADR 0018
-- 「cast 是推出来的不是声明的」）：能从正文算出来的就别做成表单。判据是集合判断
-- （CJK 表意字符占比过阈值），不是语义判断，ADR 0005 过得去——见
-- `src/novel_harness/text/language.py::detect_language()`。
--
-- `language_locked` 记的是「这一位是不是作者手动确认/改过的」：自动检测只在它是 0
-- 时才写（`project.apply_detected_language()`），作者一旦通过 `PATCH …/language` 改过，
-- 它变 1，此后自动检测永不再覆盖——同「右栏 LLM 生成、作者可见可改」那条口径：
-- 机器猜的，人改了就听人的。这一位**不进 API 出参**：它是服务端的内部记账，
-- 不是给作者看的机制词（CLAUDE.md「机制词不上屏」）。
--
-- 默认值 'zh'：新迁移不给旧库的历史项目凭空造出一个还没被判定过的语言，而这个仓库
-- 自己的既有项目全是中文（CLAUDE.md：「今天的实现仍然是中文优先」）。新建的项目
-- 一旦有正文（首次 import/sync/bootstrap），自动检测立刻会跑一次把它覆盖成真实判定
-- ——'zh' 只是「还没判定之前」的地板，不是最终答案。
--
-- 语言不进 `~/.config/novel-harness/settings.json`：那是每台机器一份的，而一个人
-- 可能同时有一本中文书和一本英文书。ADR 0012「书自己拥有它的库」——语言属于书，
-- 所以它跟 canon_version 一样挂在 project 行上。

ALTER TABLE project ADD COLUMN language TEXT NOT NULL DEFAULT 'zh'
  CHECK (language IN ('zh', 'en'));
ALTER TABLE project ADD COLUMN language_locked INTEGER NOT NULL DEFAULT 0
  CHECK (language_locked IN (0, 1));

PRAGMA user_version = 30;
