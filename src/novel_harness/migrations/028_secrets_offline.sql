-- 028：秘密整套下线（ADR 0039）。
--
-- 顺序是有讲究的，**每一步都依赖上一步**：
--
--   ① 先清 event_reveal（它引 secret_id，是 `revealed_facts` 那条限肢的存储）
--   ② 再清 KNOWS / BELIEVES 的边（它们的 dst 是 Secret 节点）
--   ③ 再清 Secret 节点的别名、`secret` 扩展行、`node` 行本身
--   ④ 最后才动 `edge_type` 那两行 —— 它被 `edge.type` 外键引着，
--      ②没做完就删它，要么外键报错，要么留下一批指向不存在类型的边
--   ⑤ 表本身（`secret` / `event_reveal`）
--
-- ⚠️ **`node.label` 的 CHECK 里仍然写着 'Secret'，没有动它。**
--    SQLite 改 CHECK 要重建整张 `node`，而 `node` 被 alias / edge / chapter / secret /
--    event_participant / event_knower … 一大批复合外键引着。重建 = 把全库的引用关系
--    在一个迁移里拆掉再接回去，收益是「库层面也写不进 Secret」——而写入方全部走
--    `NodeLabel` 枚举，那一侧已经关死了。**收益远小于风险，不做。**
--
-- ⚠️ **`decision_log` 一行都不动。** 它有三个触发器封死 INSERT/UPDATE/DELETE，
--    而那不是障碍，是本意：`secret_declare` / `knows_declare` / `knowledge_edit` /
--    `knowledge_add` 这些历史行是作者当年真的做过的事。`decisions.DecisionKind` 因此
--    保留这几个废弃值、`activity.py` 保留对应的措辞行和收窄网（见那两个文件的注释）。

-- ── ① event_reveal ────────────────────────────────────────────────────────
DELETE FROM event_reveal;

-- ── ② KNOWS / BELIEVES 边 ────────────────────────────────────────────────
-- 真删不是标 RETRACTED：RETRACTED 的语义是「这条事实从未成立过，但记录留着可查」，
-- 而这里发生的事是「承载这类事实的机制没有了」。留着一批读不出来、类型枚举里也不存在
-- 的行，只会让下一个人以为它们还有意义。
DELETE FROM edge WHERE type IN ('KNOWS', 'BELIEVES');

-- ── ③ Secret 节点 ────────────────────────────────────────────────────────
DELETE FROM alias WHERE node_id IN (SELECT id FROM node WHERE label = 'Secret');
DELETE FROM secret;
DELETE FROM node WHERE label = 'Secret';

-- ── ④ edge_type 的两行 ───────────────────────────────────────────────────
-- 那三个触发器（001_init.sql）明说了这条路：「要改这 9 行就写一个迁移
-- （在里面 DROP 掉本触发器、改、再建回来）——那正是它该走的路。」
-- **建回来的语句和 001 里逐字相同**，改一个字就等于顺手放宽了那道闸。
DROP TRIGGER edge_type_is_schema_not_data_insert;
DROP TRIGGER edge_type_is_schema_not_data_update;
DROP TRIGGER edge_type_is_schema_not_data_delete;

DELETE FROM edge_type WHERE type IN ('KNOWS', 'BELIEVES');

CREATE TRIGGER edge_type_is_schema_not_data_insert BEFORE INSERT ON edge_type
BEGIN
  SELECT RAISE(ABORT, 'edge_type 是 schema 的一部分（PLAN §5.5 / ADR 0005），改它请写迁移');
END;

CREATE TRIGGER edge_type_is_schema_not_data_update BEFORE UPDATE ON edge_type
BEGIN
  SELECT RAISE(ABORT, 'edge_type 是 schema 的一部分（PLAN §5.5 / ADR 0005），改它请写迁移');
END;

CREATE TRIGGER edge_type_is_schema_not_data_delete BEFORE DELETE ON edge_type
BEGIN
  SELECT RAISE(ABORT, 'edge_type 是 schema 的一部分（PLAN §5.5 / ADR 0005），改它请写迁移');
END;

-- ── ⑤ 表 ────────────────────────────────────────────────────────────────
DROP INDEX IF EXISTS idx_secret_project;
DROP TABLE secret;
DROP INDEX IF EXISTS idx_event_reveal_secret;
DROP TABLE event_reveal;
