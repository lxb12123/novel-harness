-- 每章总结 → 它提到了花名册里的哪些东西（倒排表）。
--
-- 这是 PLAN 技术裁决表「全文检索：**不做。mention 索引即检索**」在**总结**上的兑现。
-- 那条裁决在正文上早就跑着（`mentioned.py` / `agent/index.py` 的人物轴），只是从没落到
-- 总结上——而作者要的「迅速找到相关章节的总结，然后引用、对比、调研」正是这一层：
-- 每一段总结是一个记忆点，**记忆点之间的连线就是「同一个人 / 同一个地方 / 同一个秘密」**。
--
-- **不是找相似，是找相关。** 找相似要向量、embedding、语义 —— 那是被砍掉的 Qdrant，
-- 而且撞 ADR 0005 的铁律。这里一行都不往那边走：判据只有「这个称呼在这段字里出现了没有」，
-- 用的是 `text/mentions.py` 那条正则 alternation，和 R2 FUTURE_LEAK 同一份实现。
--
-- ══════════════════════════════════════════════════════════════════════════
-- 为什么是两张表：`summary_mention` 答不了「这一段扫过没有」
-- ══════════════════════════════════════════════════════════════════════════
--
-- 一段总结**扫过了但一个人都没提到**，和**根本没扫过**，在倒排表里长得一模一样：
-- 两种都是零行。合成一张表的话，凡是零命中的总结每次读都会被重扫一遍——不是错，
-- 是白烧 CPU；更糟的是没有任何地方记得住「它是拿哪一版花名册扫的」。
--
-- 所以 `summary_index` 是**覆盖标记 + 内容地址**：一行 = 「这一段，我拿 roster_hash
-- 那一版花名册扫过了」。零命中因此是一条 index 行 + 零条 mention 行，和没扫过分得开。
--
-- ══════════════════════════════════════════════════════════════════════════
-- 索引什么时候重建：**两个内容地址的相等判断，没有一个要记得调的钩子**
-- ══════════════════════════════════════════════════════════════════════════
--
-- 索引是 `(总结的那一行, 花名册那一版)` 的纯函数，而两个自变量都是内容寻址的：
--
--   总结那一行   `chapter_summary` 是 append-only（迁移 013）。作者改一段 / 撤回 /
--                重新生成，产出的都是**一个新 id 的新行**，旧行原样留着。所以
--                「这一行的字变了」在这个库里不可能发生——变的只会是「哪一行现在算数」。
--                ⇒ 新行没有 index 行 ⇒ 下一次读时补扫；旧行不再是「现在算数的那一条」
--                ⇒ 同一次里把它的 index / mention 行删掉。**写路径一个字都不用改。**
--
--   花名册那一版  `roster_hash` = 那一刻全部可匹配 surface + 它们指向谁的哈希。
--                作者建一个人物 / 加一个别名 / 把某个称呼标成不可用 / 删掉一个人，
--                这个哈希就变 ⇒ 全书的 index 行一个都对不上 ⇒ 整本重扫一次。
--
-- **为什么不做写穿（插总结时顺手建索引）**：花名册那一半根本写穿不了——`POST /nodes`
-- 和 `POST /aliases` 各要记得去重扫一遍全书，而忘了不报错，表现只是「那个新人物在
-- 总结里永远搜不到」。一条静默的假空。这个仓库对「必须记得调的守卫」的评价写在
-- ARCHITECTURE §10.5：「它是本层最弱的一环」。这里干脆不造那一环。
--
-- 代价是一次全量重扫，而它便宜得可以忽略：总结整本加起来是**几十万个字符**
-- （722 章 × 模型档 120 字上限 / 作者档 1000 字上限），一次正则 alternation 扫完
-- 是毫秒级——正文那一侧（722 × 3000 字）才是需要心疼的量，而这张表不碰正文。
--
-- ══════════════════════════════════════════════════════════════════════════
-- 谁负责「现在算数的那一条」：**不在这儿写第二份判据**
-- ══════════════════════════════════════════════════════════════════════════
--
-- 「最新那一行且没被撤回」的 SQL 只有一处（`SummaryStore._latest_per_chapter`，
-- 013 那轮刚把两份合成一份）。这两张表因此**不存章号**：反查时先问 `SummaryStore`
-- 现在算数的是哪几行，再拿 summary_id 去交集。存一份章号进来就是第二个会漂的判据，
-- 而漂开的表现是「起草带进 prompt 的那几章」和「界面上说提到过的那几章」不是同一批。

CREATE TABLE summary_index (
  -- 一段总结扫过一次 = 一行。**主键是 summary_id**：一段字只可能有一个当前的扫描结果。
  summary_id  TEXT PRIMARY KEY REFERENCES chapter_summary(id) ON DELETE CASCADE,
  project_id  TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  -- 扫它时那一版花名册的内容地址。**它和它对不上 = 这一行作废**（见上面那段）。
  roster_hash TEXT NOT NULL,
  indexed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE summary_mention (
  summary_id TEXT NOT NULL REFERENCES summary_index(summary_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  -- 外键连 `node`：作者删掉一个人物，指着他的倒排行跟着消失（而不是留下一堆
  -- 点下去查无此人的芯片）。同一次改动也会让 roster_hash 变，两条路是一致的。
  node_id    TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  -- **命中的是哪个称呼**，不只是哪个人。作者在总结里写的是「魔尊」还是「萧决」
  -- 是他自己的信息（ADR 0004：别名差异编码着关系阶段和认知边界，是 canon 不是噪声）。
  surface    TEXT NOT NULL,
  PRIMARY KEY (summary_id, node_id, surface)
) WITHOUT ROWID;

-- 反查那一条唯一的 SQL：`WHERE project_id = ? AND node_id = ?`。
CREATE INDEX idx_summary_mention_node ON summary_mention(project_id, node_id);

-- 「这个项目里哪些总结是拿当前这版花名册扫的」——补扫时每次都要问一遍。
CREATE INDEX idx_summary_index_roster ON summary_index(project_id, roster_hash);

PRAGMA user_version = 14;
