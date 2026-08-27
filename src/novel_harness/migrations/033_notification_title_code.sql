-- 033：通知标题从"后端算好的最终中文句子"改成"码 + 参数"（国际化第四批 Phase B）。
--
-- ── 为什么是加两列，不是重建表 ──────────────────────────────────────────
-- `title TEXT NOT NULL` 不动：新行往里写的是 `title_code` 本身（比如
-- "import_toc_skipped_title"），不是渲染出来的句子——纯粹为了满足 NOT NULL
-- 和给维护者在库里 debug 时留一个能认的锚，**前端不读这一列**。旧行（迁移前
-- 创建、`title` 里已经是烧好的中文）的 `title_code`/`title_params_json` 天然是
-- NULL，不做任何反推式回填——从一句已经渲染完的中文猜回"当时是哪个码、什么
-- 参数"就是在回答"这句话是什么意思"，ADR 0005 禁的那类判断；旧通知永远停在
-- 创建时的语言，同 `decision_log` 的"历史行不回填"。
--
-- **一个列两种含义**（旧行是中文句子，新行是码字符串）通常是坏味道，这次接受，
-- 判别式是 `title_code IS NULL`——显式、机械。`dedupe_key` 不依赖 `title`（早已
-- 独立算），加这两列不影响任何一条既有去重逻辑。

ALTER TABLE system_notification ADD COLUMN title_code TEXT;
ALTER TABLE system_notification ADD COLUMN title_params_json TEXT
  CHECK (title_params_json IS NULL OR json_valid(title_params_json));

ALTER TABLE system_notification_outbox ADD COLUMN title_code TEXT;
ALTER TABLE system_notification_outbox ADD COLUMN title_params_json TEXT
  CHECK (title_params_json IS NULL OR json_valid(title_params_json));

PRAGMA user_version = 33;
