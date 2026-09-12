-- 037：一稿的「要求」和「助手补的资料」跟着稿子存（ADR 0047 守着的第二条线）。
--
-- 模式二起草改成助手自己写「这一稿要做什么」（brief）、自己挑「写手够不着的资料」
-- （materials）之后，作者必须看得见它喂了什么——挑错了才看得出来。这两样进的是
-- 写手那一次调用的提示词，写完就散了；不存下来，桌上那张卡只剩一稿正文，
-- 作者无从判断「它为什么这么写」。
--
-- 两列都有默认值：迁移之前的旧行（真书上 0 行；合成库里有）读出来是空的，
-- 读端按「空 = 那时还没有这两格」处理，不编。
ALTER TABLE draft_candidate ADD COLUMN brief TEXT NOT NULL DEFAULT '';
ALTER TABLE draft_candidate ADD COLUMN materials_json TEXT NOT NULL DEFAULT '[]'
  CHECK (json_valid(materials_json));

PRAGMA user_version = 37;
