-- 029：每个人物**每一章**的信息量。分数 = 它按人求和（2026-08-25 裁定）。
--
-- ── 为什么是一张表，不是 node.props 上一个数 ──────────────────────────────
--
-- 因为**重跑抽取不许让分数翻倍**。同一章的抽取会被重跑（prompt 改了、
-- `force` 重跑、generation 前进），而「累加」写在一个标量上时，第二次跑就是
-- 第二次加。按 (人, 章) 主键存，重跑是 upsert 那一行，加多少次都还是那一行。
--
-- 这条和「分数继续累加」不冲突：累加发生在**章与章之间**（SUM），
-- 不是同一章的两次运行之间。
--
-- ── 它今天**只用来排序** ─────────────────────────────────────────────────
--
-- 完整设计是四条规则（已在册 → 并进去继续累加 / 不在册 + 够分 → 直接建 /
-- 不在册 + 不够 → 问一句 / 不答默认建进去），见 ADR 0020 的第二份补记。
-- **那道「够不够」的闸这一批一行都没写**：阈值要几本书的分布才定得下来，
-- 拿一本书凑出来的数换本书就不准。今天仍然是「全部自动建，一个都不问」。

CREATE TABLE character_information (
  project_id     TEXT    NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  character_id   TEXT    NOT NULL,
  chapter_number INTEGER NOT NULL CHECK (chapter_number >= 1),

  -- 这一章里模型给这个人写的画像有多长（非空字段的字符数之和）。
  -- **0 是合法的**：这个人在这一章被提到、但模型没给他写画像。
  -- 那是「这一章没带来新信息」，不是「没这一行」——两者在排序上是一回事，
  -- 但在「他被抽到过几章」上不是。
  units          INTEGER NOT NULL CHECK (units >= 0),

  -- 冗余列，唯一用途是给下面那条复合外键当锚（同 `chapter.label` 的做法）。
  character_label TEXT   NOT NULL DEFAULT 'Character'
                         CHECK (character_label = 'Character'),

  PRIMARY KEY (project_id, character_id, chapter_number),

  -- 复合外键连 label：只有 Character 能记分。挂到地点身上的一行会让
  -- 「按分排序的花名册」里冒出一个不是人的东西，而那一行看起来完全正常。
  FOREIGN KEY (character_id, project_id, character_label)
    REFERENCES node(id, project_id, label) ON DELETE CASCADE
);

CREATE INDEX idx_character_information_person
  ON character_information(project_id, character_id);
