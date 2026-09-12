-- 036：事实只记起点 —— `edge.valid_to_chapter` 清空并停用（ADR 0043）。
--
-- 「谁盖住谁」从**写**的时候（把旧边闭合成 `[old_from, new_from)`）搬到**读**的时候
-- （`queries.CURRENT_EDGE_CTE`：同一语义槽里取 valid_from 不晚于本章的最后一条）。
--
-- 为什么必须清空而不是留着不管：读端已经不看这一列了，留着旧值不会有人读；
-- 但**一旦哪天有人把那半行 WHERE 加回去**，库里这些历史值会让第 10 章那条边在
-- 第 143 章之后凭空消失。清成 NULL = 这一列从此只有一种含义（没有含义）。
--
-- ⚠️ **这一版只清空，不删列。** 删列要重建 edge 表（它上面挂着 CHECK 和四个索引），
-- 而这个改动最好的性质是**可逆**：`valid_to_chapter` 是纯派生值，任何时候都能按章序
-- 重算一遍写回去。留一版观察期，确认没问题再开 037 删列 —— 在那之前回退不丢数据。
UPDATE edge SET valid_to_chapter = NULL WHERE valid_to_chapter IS NOT NULL;

PRAGMA user_version = 36;
