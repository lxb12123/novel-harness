-- Novel Harness — 001_init：PLAN.md §8「Day 2」的全部表，一次建全。
--
-- ── 本文件的三条读法 ──────────────────────────────────────────────────────
--
-- 1. CHECK 的取舍线：**写错了会得到错误答案**的枚举一律 CHECK 死
--    （information_scope / status / evidence_status / exclusivity / label / kind /
--     decision / proposal_set.status）；写错了只是日志难看的字段一律留开放字符串
--    （source / edge_type 之外的 kind / capability / issue_type）。
--    理由不是懒：SQLite 改 CHECK 要重建整张表，为一个不参与过滤的字段付这个代价
--    不划算。ADR 0005 对 validation_report.issue_type 用的正是同一条线。
--
-- 2. 时态过滤（valid_from / valid_to / information_scope / status / evidence_status
--    五个条件缺一不可）只允许在 graph/queries.py 里出现一次（PLAN §5.5）。
--    本文件的 CHECK 是那份实现的安全网，不是它的替代品。
--
-- 3. `node` 是全部 8 类节点的唯一身份表。`chapter` 和 `secret` 是**扩展表**：
--    主键就是 node.id（1:1）。这样 edge.src / edge.dst 才能永远 REFERENCES node(id)。
--    PLAN §8 把 secret / chapter 列成独立表、§5.8 又把 Secret / Chapter 列进 8 类节点，
--    而 §8 Day 5 的矩阵 SQL 写的是 `k.dst = s.id`——三处只有在「扩展表主键 = node.id」
--    这一种读法下才自洽。
--
-- 4. 上面那条读法要成立，外键就得**连 label 和 project_id 一起钉住**，不能只钉 id。
--    `REFERENCES node(id)` 只保证 secret.id 是**某个** node，不保证它是一个 **Secret** node，
--    也不保证它是**本项目**的 node——两者都实测能写进来，且后果都是静默的（一个人被
--    当成秘密列进面板列头 / 一条边对 state_at 永久隐形）。所以 node 上有两条看起来
--    冗余的 UNIQUE（见那张表），扩展表和 edge / alias 走**复合外键**。
--    这跟 edge.type→edge_type 那条外键是同一个手法、同一个理由：**事前不可能，
--    而不是事后在日志里一眼可见**。
--
-- ── db.py 契约（三条，缺一条本文件就不成立）────────────────────────────────
--
-- A. connect() 必须 `PRAGMA foreign_keys=ON`。SQLite 默认**关闭**外键，
--    不设它则本文件所有 REFERENCES 都只是注释。
-- B. migrate() 必须用 `PRAGMA user_version` 做闸门（本文件末尾置 1）。
--    本文件**不是自幂等**的：没有 IF NOT EXISTS，重跑必报错。这是故意的——
--    迁移文件里的 IF NOT EXISTS 会把「跑错了迁移」变成静默通过。
--    「连跑两次 migrate 不报错」这条验收由闸门满足，不由 DDL 满足。
--    注意 `PRAGMA user_version = ?` **不能**用占位符，只能拼字符串。
-- C. 本文件必须用 `importlib.resources` 读，不能用 `__file__` 拼路径——wheel 里没有源码树。
--    用这一种写法：
--        files("novel_harness") / "migrations" / "001_init.sql"
--    `files("novel_harness.migrations")` 也能跑通（migrations/ 没有 __init__.py，
--    Python 把它当 PEP 420 命名空间包），但它依赖命名空间包解析，且目录一旦为空就崩。
--    从父包导航过去没有这个前提。两种写法都已在装好的 wheel 里实测过。
--    `(files(...) / "migrations").iterdir()` 可用，migrate() 的「按文件名扫 *.sql」成立。
--
-- 打包：uv_build 默认就把 module root 底下的**全部**文件收进 wheel，.sql 不需要在
-- pyproject 里配 package data（已在干净 venv 里装 wheel 实测：本文件在包内、可读、可跑）。
-- 这跟 setuptools/hatchling 要显式声明 package-data 的习惯不同，别照那个习惯去改 pyproject。
--
-- ── 时间戳格式 ────────────────────────────────────────────────────────────
-- 全库统一 ISO 8601 / UTC / 毫秒 / 'Z' 后缀，即 strftime('%Y-%m-%dT%H:%M:%fZ','now')。
-- Python 侧若自己写时间戳必须产出同一形状：`datetime.now(UTC).isoformat()` 得到的是
-- `+00:00` 后缀，与 'Z' 混存会在同秒记录上把字典序排错。


-- ═══════════════════════════════════════════════════════════════════════════
-- 项目
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE project (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  -- 正文在磁盘上（ADR 0007）。这是 chapters/ 的所在目录，不是库里的正文。
  root_path     TEXT NOT NULL,
  -- 每次 Canon 提交 +1。单库单事务下它就是 STALE_BASE_VERSION 检查的全部（§2.2a）。
  canon_version INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);


-- ═══════════════════════════════════════════════════════════════════════════
-- 图：节点 / 别名 / 边
-- ═══════════════════════════════════════════════════════════════════════════

-- 8 类节点（PLAN §5.8）。增长规则（ADR 0005）：只有当某条面板分区或某条规则
-- 真的要查它时，才允许加一类。Volume/Scene/Skill/Event/Fact/State/EvidenceRef
-- 在 v1 没有任何消费者——它们不在这里，不是遗漏。
CREATE TABLE node (
  id         TEXT PRIMARY KEY,          -- ULID，形如 character:{proj_short}:{ulid}（ADR 0003）
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  label      TEXT NOT NULL,
  -- 显示名。这是**唯一真相**：secret / chapter 扩展表都不再存一份 name。
  -- 同名字段存两处就需要一个同步器，而 §5.4 为这个理由砍掉了存储版 CURRENT。
  name       TEXT NOT NULL,
  props_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (label IN ('Character','Location','Faction','Secret',
                   'Foreshadow','Object','StateDim','Chapter')),
  CHECK (json_valid(props_json)),
  -- 下面两条 UNIQUE 在 `id` 已是主键时是**逻辑冗余的**——它们的存在理由全在子表那边：
  -- SQLite 要求复合外键的父列有一条恰好覆盖这些列的 UNIQUE 索引。有了它们，
  --   (id, project_id)        → edge.src/dst / alias.node_id 指向别的项目的节点 = 物理不可能
  --   (id, project_id, label) → secret / chapter 扩展表挂到 label 不对的节点上 = 物理不可能
  -- 这跟 edge.type→edge_type 那条外键是同一个手法、同一个理由：§5.3 对跨项目误引用的
  -- 原处置只是「project_short 前缀让它在日志里一眼可见」——那是**事后可见**，
  -- 而这份 schema 在别处（edge.type→edge_type）已经明确选择了**事前不可能**。
  UNIQUE (id, project_id),
  UNIQUE (id, project_id, label)
);

CREATE INDEX idx_node_label ON node(project_id, label);
CREATE INDEX idx_node_name  ON node(project_id, name);

-- **一个 dim_key 一个节点。** 没有它，两个 StateDim 节点可以共享 dim_key='health'
-- （「健康」和「生死」），而 supersede 按 dst 的**节点 id** 找冲突边、不是按 dim_key，
-- 于是「ch89 死、ch100 复活」两条边谁也不闭合谁 → state_at 同时返回 dead 和 alive →
-- StateSnapshot.is_dead 的 any() 让 dead 永远压过 alive → R3 对全书每一句「萧决道：」
-- 报死人说话 → M3 的「误报 <1 条/章」当场崩。
-- 注意 dim_key 存在的理由（graph/models.py）恰恰是「作者随时会把 name 从『健康』改成
-- 『生死』」——即「同一维度有多个表述」是被**预期**的，只是预期在 name 上，不在节点上。
CREATE UNIQUE INDEX idx_state_dim_key
  ON node(project_id, json_extract(props_json, '$.dim_key'))
  WHERE label = 'StateDim' AND json_extract(props_json, '$.dim_key') IS NOT NULL;

-- 别名是**索引，不是事实**（§5.8）——所以它是表不是边。
-- 且它故意**不做实体消解**（ADR 0004）：「顾姑娘/清音/魔尊」的差异编码了关系阶段和
-- 认知边界（化名 = 别人不知道他是谁），是 canon 不是噪声。GraphRAG 的 entity
-- resolution 会正好摧毁这个项目最有价值的信号。
CREATE TABLE alias (
  id               TEXT PRIMARY KEY,
  project_id       TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  -- 复合外键而不是 `REFERENCES node(id)`：后者保证 node_id 是**某个**节点，不保证它是
  -- **本项目**的节点。一条 project_id=B 却指向 A 的节点的别名行会落库，然后对 A 的
  -- mention 匹配永久隐形（alias_rows 按 a.project_id 过滤）——静默，无报错。
  node_id          TEXT NOT NULL,
  surface          TEXT NOT NULL,
  kind             TEXT NOT NULL,
  -- 作者/UI 对这个 surface 本身的判定。**歧义不在这里**：一个 surface 映射到多个
  -- node_id（「师兄」）是跨行事实，只能在查询时算（见 Resolution.usable_for_rules）。
  usable_for_rules INTEGER NOT NULL DEFAULT 1,
  UNIQUE (project_id, node_id, surface),
  CHECK (kind IN ('canonical','alias','nickname','title')),
  CHECK (usable_for_rules IN (0,1)),
  -- ADR 0004 说「2 字以下别名 UI 层直接拒绝录入」（「音」「决」是灾难）。
  -- 这里把它下沉一层，并且**改准了**：短 surface 可以存在（有 1 字名的人物），
  -- 但绝不可以被规则拿去匹配正文。UI 层的规矩能被绕过，这条不能。
  CHECK (usable_for_rules = 0 OR length(surface) >= 2),
  FOREIGN KEY (node_id, project_id) REFERENCES node(id, project_id) ON DELETE CASCADE
);

-- mention 匹配（正则 alternation 的料）+ 歧义判定
-- （GROUP BY surface HAVING COUNT(DISTINCT node_id) > 1）都走这条。
CREATE INDEX idx_alias_surface ON alias(project_id, surface);

-- 每个节点至多一条 canonical。约定：它的 surface == node.name。
-- 这一份「重复」是正当的——node.name 是显示真相，alias 里的 canonical 行是**索引项**，
-- 没有它 mentions.py 的 alternation 就匹配不到本名。
CREATE UNIQUE INDEX idx_alias_canonical ON alias(project_id, node_id) WHERE kind = 'canonical';


-- 边的互斥性元数据（§5.5）。**必须在第一条边写进库之前存在**，否则历史数据全是脏的，
-- 而 state_at 会同时返回「在青云城」和「在北荒」两条有效边 → 规则误报 → 生死线指标崩。
-- edge.type 对它建外键，不是 CHECK：外键让「一条边的类型没有互斥性语义」在物理上
-- 不可能发生，CHECK 做不到这件事。
CREATE TABLE edge_type (
  type        TEXT PRIMARY KEY,
  exclusivity TEXT NOT NULL CHECK (exclusivity IN ('single_per_src','single_per_src_dst','multi'))
);

-- 9 类关系（§5.5 / §5.8）。DOES_NOT_KNOW（组合爆炸）和 PARTIALLY_KNOWS（欠定义）
-- 已**永久删除**，不是推迟：闭世界推导「不存在 KNOWS 边 ⇒ 不知道」替代前者，
-- 「秘密拆子事实 + 每子事实一条 KNOWS」替代后者（ADR 0005）。
INSERT INTO edge_type (type, exclusivity) VALUES
  ('LOCATED_AT',  'single_per_src'),      -- 一个人同一时刻只能在一个地方
  ('HAS_STATE',   'single_per_src_dst'),  -- (人, 状态维度) 单值
  ('RELATED_TO',  'single_per_src_dst'),  -- (A,B) 关系阶段单值
  ('KNOWS',       'single_per_src_dst'),  -- (人, 秘密) 单值
  ('BELIEVES',    'single_per_src_dst'),
  ('MEMBER_OF',   'multi'),
  ('OWNS',        'multi'),
  ('PLANTED_IN',  'multi'),
  ('RESOLVED_IN', 'multi');

-- **这 9 行是 schema，不是数据。** 上面那段注释说它是「互斥性元数据」，但没有任何东西
-- 让它真的是元数据——它是一张普通的可写可改表，而两个动词各自够狠：
--   INSERT 一行 = 复活一个 ADR 0005 判了**永久删除**的边类型（DOES_NOT_KNOW 的组合
--     爆炸：实体化后额外 67,500 条边），因为 edge.type 的外键只查这张表在不在。
--   UPDATE 一行 = supersede 静默失效。`UPDATE edge_type SET exclusivity='multi'
--     WHERE type='LOCATED_AT'` 之后 upsert_edge 谁都不挤，state_at 同时返回「在青云城」
--     和「在北荒」——**这正是 §5.5 逐字点名的那个 fatal**。
-- schema 花大力气用外键保证「一条边的类型一定有互斥性语义」，却没保证「那个语义是对的」。
-- 要改这 9 行就写一个迁移（在里面 DROP 掉本触发器、改、再建回来）——那正是它该走的路。
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


CREATE TABLE edge (
  id                 TEXT PRIMARY KEY,
  project_id         TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  -- 两端走**复合外键**（见表末），不是 `REFERENCES node(id)`：后者只保证端点是某个节点，
  -- 不保证是**本项目**的节点。一条 project_id=B、两端却都属于 A 的边会落库，并且对
  -- state_at(project_id='A') **永久隐形**（全部时态查询都带 project_id = :pid）。
  src                TEXT NOT NULL,
  dst                TEXT NOT NULL,
  type               TEXT NOT NULL REFERENCES edge_type(type),
  props_json         TEXT NOT NULL DEFAULT '{}',

  -- ── 时态（闭开区间 [valid_from, valid_to)）────────────────────────────
  -- valid_from **永远不由作者手填**（§5.9 / ADR 0006）：他不记得主角哪章突破金丹，
  -- 他会填 1，于是时间态模型退化成当前值快照图——而时间态是全部差异化的地基。
  -- CANON/PROVISIONAL：valid_from = 证据所在章。PLANNED：valid_from = 作者的意图
  -- （「计划第 200 章回收」），那不是回忆是决定，他当然填得出来。
  valid_from_chapter INTEGER NOT NULL,
  -- 只有 upsert_edge 的 supersede 写它，任何调用方都不许（所以 EdgeSpec 里没有这个字段）。
  valid_to_chapter   INTEGER,

  -- ── 三层图谱（§5.4）────────────────────────────────────────────────────
  -- CURRENT 是**推导不是存储**：CURRENT = valid_to IS NULL AND scope='CANON'。
  -- 存 CURRENT 会制造「一条边被 supersede 时谁负责把 CURRENT 摘掉」这个同步问题，
  -- 它会在 M4 以误报的形式爆炸——正好是这个产品最怕的东西。
  -- OPTIONAL 已**永久删除**（无消费者）。DRAFT ≡ PROVISIONAL，不另设值。
  information_scope  TEXT NOT NULL,

  -- 行级生命周期，与时态**正交**。故意只有两个值：
  -- 没有 SUPERSEDED——被挤掉这件事已经由 valid_to_chapter 表达了，再存一个状态位
  -- 就是把刚砍掉的 CURRENT 同步问题原样请回来。
  -- RETRACTED 的唯一来源是 supersede 的同章更正（见下方 valid_to > valid_from 的 CHECK）。
  status             TEXT NOT NULL DEFAULT 'ACTIVE',

  confidence         REAL NOT NULL DEFAULT 1.0,
  -- 开放字符串。约定取值：author（作者声明）/ extractor（M4 抽取）/ system（推导、
  -- 决策日志重放）。不 CHECK：它不参与任何过滤，写错了只是日志难看。
  source             TEXT NOT NULL DEFAULT 'author',

  evidence_id        TEXT REFERENCES evidence(id),
  -- 'NONE' 是**哨兵值不是占位符**，且这一列 NOT NULL 是硬要求：
  -- §5.5 的 state_at 写的是 `evidence_status != 'STALE'`，而 SQL 里 `NULL != 'STALE'`
  -- 求值为 NULL 即假 —— 若这列可空，那条查询会**静默丢掉每一条作者声明的无证据边**，
  -- 而作者声明正是整个产品（ADR 0004）。
  evidence_status    TEXT NOT NULL DEFAULT 'NONE',

  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  CHECK (information_scope IN ('CANON','PROVISIONAL','PLANNED','REJECTED')),
  CHECK (status IN ('ACTIVE','RETRACTED')),
  CHECK (evidence_status IN ('NONE','FRESH','STALE')),
  CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CHECK (json_valid(props_json)),
  -- 闭开区间：valid_to == valid_from 是空区间（这条事实从未成立过），那是 bug 不是事实。
  -- 同章更正的正确表达是 status='RETRACTED'，不是把区间压成空。
  CHECK (valid_to_chapter IS NULL OR valid_to_chapter > valid_from_chapter),
  -- 两列必须同生同死，否则 state_at 的 STALE 过滤会对着一条不存在的证据放行。
  CHECK ((evidence_id IS NULL) = (evidence_status = 'NONE')),
  -- v1 的 9 类关系没有一条有合法自环：LOCATED_AT(萧决,萧决) / KNOWS(萧决,萧决) /
  -- RELATED_TO(萧决,萧决) 全是导入器 bug。将来真有需要自环的类型，改这条 CHECK 时
  -- 顺便回答「它的 exclusivity 怎么算」——那个问题现在没有答案，正说明它现在不存在。
  CHECK (src <> dst),
  FOREIGN KEY (src, project_id) REFERENCES node(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (dst, project_id) REFERENCES node(id, project_id) ON DELETE CASCADE
);

-- upsert_edge 的幂等键（§2.2a「幂等变成一个唯一索引」）。
-- 含 information_scope 是**故意**的：它让抽取器（PROVISIONAL）在物理上碰不到
-- 作者的 CANON 行——原则 5「Agent 不得直接修改正式 Canon」由此变成一条数据库约束，
-- 而不是一条纪律。
CREATE UNIQUE INDEX idx_edge_identity
  ON edge(project_id, src, dst, type, valid_from_chapter, information_scope);

-- §8 点名的两条。列序 = state_at 的 WHERE 列序，valid_from_chapter 收尾让范围扫在索引里完成。
CREATE INDEX idx_edge_src ON edge(project_id, src, type, valid_from_chapter);
CREATE INDEX idx_edge_dst ON edge(project_id, dst, type, valid_from_chapter);
-- revalidate 的反查：一条 evidence 失活 → 哪些边要停火（ADR 0006）。
CREATE INDEX idx_edge_evidence ON edge(evidence_id) WHERE evidence_id IS NOT NULL;


-- ═══════════════════════════════════════════════════════════════════════════
-- 章节与快照
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE chapter (
  -- 扩展表：主键就是 label='Chapter' 的那个 node.id。
  -- PLANTED_IN / RESOLVED_IN 的 dst 是 Chapter 节点，只有这样 edge.dst 的外键才成立。
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  -- 冗余列，唯一用途是给下面那条复合外键当锚（见 secret.label 的完整论证）。
  label           TEXT NOT NULL DEFAULT 'Chapter' CHECK (label = 'Chapter'),
  -- **全书顺序位置（1-based），由导入器分配；不是正文里印的章号。**
  -- 必须这样定义，因为 state_at 的 `valid_from_chapter <= :ch` 要求它是全序键，
  -- 而正文里印的章号会因分卷重启（第二卷第一章）和番外而重复。
  number          INTEGER NOT NULL,
  title           TEXT NOT NULL DEFAULT '',
  path            TEXT NOT NULL,   -- 相对 project.root_path，见下方 idx_chapter_path
  text_sha256     TEXT NOT NULL,
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  -- 改 11：v1 无索引，staleness 恒为 0。字段免费，语义免费，**历史补不回来**。
  indexed_version INTEGER NOT NULL DEFAULT 0,
  -- 这条 UNIQUE 是**导入期的绊线，不是洁癖**：两章同号 ⇒ state_at 的全序断裂 ⇒
  -- 「两条互斥边」。让脏数据在导入时炸（§8 已为它预留半天），好过在 M1 变成
  -- 一个查不出来的面板 bug。
  UNIQUE (project_id, number),
  CHECK (number >= 1),
  -- path 是 NOT NULL 但可以是 ''，而 '' 是「导入器没填」的形状，不是一个路径。
  CHECK (length(path) > 0),
  FOREIGN KEY (id, project_id, label) REFERENCES node(id, project_id, label) ON DELETE CASCADE
);

-- 与 UNIQUE(project_id, number) 同一类绊线、同一个论证：正文在磁盘上（ADR 0007），
-- watchdog 监视文件、**按 path 反查章节**、触发 revalidate。两章共用 chapters/151.md 时
-- 「这个文件改了 → 该更新哪一章的 text_sha256 / 该重算哪一章的证据」没有确定答案。
-- M0 的验收是「切出章数与目录数完全一致」——一个把两章切到同一个 path 的导入 bug
-- 恰好不会被那条验收抓到（章数对得上），却会在 M1 变成一个查不出来源的面板 bug。
CREATE UNIQUE INDEX idx_chapter_path ON chapter(project_id, path);

-- 证据的不可变审计锚（ADR 0006 的审计指针指向它）。
CREATE TABLE chapter_snapshot (
  id          TEXT PRIMARY KEY,
  chapter_id  TEXT NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
  text        TEXT NOT NULL,
  text_sha256 TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  -- §6 S3 要求「插之前比 text_sha256 去重」。这里把那句话变成数据库强制的：
  -- debounce 每 800ms 插一行 = GB 级，靠调用方自觉去重是靠不住的。
  -- 快照是证据的锚，不是版本历史——同内容只需要存在一次。
  UNIQUE (chapter_id, text_sha256)
);

CREATE INDEX idx_chapter_snapshot ON chapter_snapshot(chapter_id, created_at);


-- ═══════════════════════════════════════════════════════════════════════════
-- 证据（双指针，ADR 0006）
-- ═══════════════════════════════════════════════════════════════════════════

-- 后端**永不对外发 offset**。对外定位一律 (para_index, quote_text, occurrence_k) 三元组。
-- offset 是三重错位的温床：坐标系（ProseMirror pos）/ 单位（JS 的 UTF-16 code unit vs
-- Python 的 code point，扩展 B 区汉字如 𤩝 差 1）/ 时间（快照 N vs doc N+7）。
-- 这些会以「偶尔位置差一两个字」的形态出现，被误当成小 bug 调两周。
--
-- para_index / occurrence_k 一律 **0-based**。
CREATE TABLE evidence (
  id                  TEXT PRIMARY KEY,
  project_id          TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,

  -- ── 审计指针：指向不可变快照，永不失效 ────────────────────────────────
  chapter_snapshot_id TEXT NOT NULL REFERENCES chapter_snapshot(id),
  para_index          INTEGER NOT NULL,
  quote_text          TEXT NOT NULL,
  -- **用定位成功后的原文子串算，不是用 LLM 返回的字符串算**（ADR 0006 配套第 3 条）。
  -- 抽取器返回的 quote 有 10–30% 对不上原文；先 difflib 模糊定位、ratio<0.9 直接丢弃，
  -- 再对命中的原文子串取哈希。反过来做的话，锚从第一天起就是坏的。
  quote_sha256        TEXT NOT NULL,

  -- ── 重定位指针：在**当前**正文里定位。作者随时在自己的编辑器里改稿，所以它会漂。
  chapter_id          TEXT NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
  -- 提示，不是真相：relocate 成功后可以就地更新。审计指针的 para_index 则永不更新。
  para_index_hint     INTEGER NOT NULL,
  occurrence_k        INTEGER NOT NULL DEFAULT 0,

  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (para_index >= 0 AND para_index_hint >= 0 AND occurrence_k >= 0),
  CHECK (length(quote_sha256) = 64)
);

-- revalidate 的入口：某章正文变了 → 扫它的全部证据。
CREATE INDEX idx_evidence_chapter ON evidence(chapter_id);
CREATE INDEX idx_evidence_quote   ON evidence(project_id, quote_sha256);


-- ═══════════════════════════════════════════════════════════════════════════
-- 秘密（头牌的料）
-- ═══════════════════════════════════════════════════════════════════════════

-- 扩展表：主键就是 label='Secret' 的那个 node.id，所以 §8 Day 5 的 `k.dst = s.id` 成立。
-- 显示名在 node.name，这里不再存一份。
-- 秘密不是「事实」，是**作者的意图**——墙上挂了一把枪是不是伏笔，取决于作者第 200 章
-- 打不打算开枪。这个信息物理上不存在于已写文本里，任何抽取器都是在猜（ADR 0004）。
CREATE TABLE secret (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  -- 冗余列，唯一用途是给下面那条复合外键当锚：`REFERENCES node(id)` 只保证 secret.id
  -- 是**某个**节点，不保证它是一个 **Secret** 节点。头注释第 3 条把「扩展表主键 =
  -- node.id」称作让 §8 / §5.8 / Day 5 三处自洽的唯一读法，但那条读法在外键上只成立了
  -- 一半——于是一个 label='Character' 的节点能登记成秘密，§8 Day 5 的矩阵 SQL
  -- （`JOIN secret s ON k.dst = s.id`）会把一个**人**列进认知面板的列头。
  -- 而 sqlite_store.knowledge_matrix 的运行时 label 校验拦得住它，代价是**整个项目**
  -- 的头牌面板一起黑掉（secrets=None 是面板的唯一路径，它先取全表再逐个校验）。
  -- 一行坏数据不该有全项目的爆炸半径，尤其当 schema 本可以让它根本进不来。
  label       TEXT NOT NULL DEFAULT 'Secret' CHECK (label = 'Secret'),
  description TEXT NOT NULL DEFAULT '',
  -- 子事实（ADR 0005）：PARTIALLY_KNOWS 被删掉之后，「部分知道」的表达法是
  -- 把秘密拆成子事实、每子事实一条 KNOWS。表达力完全相同，零新增边类型。
  sub_of      TEXT REFERENCES secret(id) ON DELETE CASCADE,
  CHECK (sub_of IS NULL OR sub_of <> id),
  FOREIGN KEY (id, project_id, label) REFERENCES node(id, project_id, label) ON DELETE CASCADE
);

CREATE INDEX idx_secret_project ON secret(project_id);


-- ═══════════════════════════════════════════════════════════════════════════
-- 决策日志（append-only，§5.7）
-- ═══════════════════════════════════════════════════════════════════════════

-- 全库唯一真正不可重建的资产：作者已经点过的每一次确认。
-- 用**文本引语**而非 ID/offset 做锚，于是换存储、改 edge schema、重跑抽取之后
-- 这些确认都能重放回新 schema。没有它，任何一次 schema 变更都要作者重新点一遍
-- 全部确认——而那一刻就是项目结束的时刻。
--
-- 注意这张表**故意没有任何外键**（连 project_id 都没有）：它必须能比它记录的
-- 一切活得更久。指向 project/node 的外键会让「删掉项目」顺手删掉唯一不可重建的东西，
-- 而 ADR 0003 立这张表就是为了防这个。§5.7 给的 DDL 里同样一个 REFERENCES 都没有。
CREATE TABLE decision_log (
  id             TEXT PRIMARY KEY,
  ts             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  project_id     TEXT NOT NULL,
  -- 开放字符串：alias_merge | secret_declare | knows_declare | proposal_review | ...
  kind           TEXT NOT NULL,
  subject_name   TEXT,             -- 人名，不是 ID
  quote_text     TEXT,             -- 文本引语，不是 offset
  quote_sha256   TEXT,
  chapter_number INTEGER,
  para_index     INTEGER,
  payload_json   TEXT NOT NULL,
  decision       TEXT NOT NULL,
  actor          TEXT NOT NULL DEFAULT 'author',
  CHECK (decision IN ('accept','reject','edit')),
  CHECK (json_valid(payload_json))
);

CREATE INDEX idx_decision_log ON decision_log(project_id, ts);

-- 「只增不改」从一句约定变成一条数据库约束。decisions.py 里「只有 append() 没有
-- update()」是靠纪律；这三个触发器是靠引擎。前者会在某个赶时间的下午被绕过。
--
-- **三个动词都要堵，不是两个。** UPDATE 和 DELETE 两条曾经是全部，而它们漏掉了
-- `INSERT OR REPLACE`：SQLite 默认 `PRAGMA recursive_triggers = 0`（本库实测就是 0），
-- 此时 REPLACE 内部的隐式 DELETE **不触发 BEFORE DELETE 触发器**——于是 REPLACE
-- 大摇大摆走过去，把一条已落库的确认就地改写，无痕、无报错、行数不变。
-- 下面这条 BEFORE INSERT 才是真正的堵法：它对 INSERT OR REPLACE / INSERT OR IGNORE /
-- 裸 INSERT 一视同仁（三者实测全被拦），且**不依赖任何连接级 PRAGMA**——
-- `recursive_triggers` 是连接级的，靠每条连接都记得设它，本身就是一条纪律。
CREATE TRIGGER decision_log_immutable_insert BEFORE INSERT ON decision_log
BEGIN
  SELECT RAISE(ABORT, 'decision_log 只增不改：这个 id 已经落库（ADR 0003 / PLAN §5.7）')
  WHERE EXISTS (SELECT 1 FROM decision_log WHERE id = NEW.id);
END;

CREATE TRIGGER decision_log_immutable_update BEFORE UPDATE ON decision_log
BEGIN
  SELECT RAISE(ABORT, 'decision_log 只增不改（ADR 0003 / PLAN §5.7）');
END;

CREATE TRIGGER decision_log_immutable_delete BEFORE DELETE ON decision_log
BEGIN
  SELECT RAISE(ABORT, 'decision_log 只增不删（ADR 0003 / PLAN §5.7）');
END;


-- ═══════════════════════════════════════════════════════════════════════════
-- 控制面：模型调用 / 校验报告 / 提案聚类
-- ═══════════════════════════════════════════════════════════════════════════

-- 13 状态 ChapterRun 状态机、Policy Engine、Idempotency Key 全部**不存在**（改 8）：
-- v1 没有自动流水线、没有自治 Agent、单机单用户单次点击，那些组件在解决尚不存在的问题。
-- 只留这张如实记录的表——**这部分不可事后补，因为历史不会重演**。
-- 渲染层是 `nh run show <id>` + 面板角标，不是 Run Inspector 页（改 8 / 第 22 节第 16 项）。
CREATE TABLE model_call (
  id          TEXT PRIMARY KEY,
  ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  project_id  TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  capability  TEXT NOT NULL,     -- 开放字符串：writer | extractor | task_parser | ...
  model       TEXT NOT NULL,
  -- 必须如实记录 thinking / output_config：Opus 4.8 省略 thinking = 不思考，
  -- Sonnet 5 省略 = adaptive。跨臂不显式统一就不可比，这是个静默陷阱（§5.2）。
  params_json TEXT NOT NULL DEFAULT '{}',
  prompt_hash TEXT NOT NULL,
  -- 内容寻址 artifact:sha256:{hex}（ADR 0003 的唯一 ULID 例外）。v1 没有 artifact 表，
  -- 所以这里是裸 TEXT、无外键。
  in_artifact  TEXT,
  out_artifact TEXT,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  ms          INTEGER,
  cost        REAL,
  attempt     INTEGER NOT NULL DEFAULT 1,
  CHECK (json_valid(params_json))
);

CREATE INDEX idx_model_call ON model_call(project_id, ts);


-- v1 一行不写它（LLM Validator 砍到 v2，改 12），但表 v1 就建好，v2 直接往里填。
CREATE TABLE validation_report (
  id             TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_id     TEXT REFERENCES chapter(id) ON DELETE CASCADE,
  chapter_number INTEGER,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  ruleset        TEXT NOT NULL DEFAULT '',
  issue_count    INTEGER NOT NULL DEFAULT 0,
  -- 每个 issue 的 issue_type 是**开放字符串枚举**，直接采用 ConStory-Bench 的
  -- 5 类 19 子类分类法（ADR 0005）——别自己发明第 23 节那 13 个指标。
  -- 每个 issue 的定位一律 (para_index, quote_text, occurrence_k)，永不 offset。
  issues_json    TEXT NOT NULL DEFAULT '[]',
  CHECK (json_valid(issues_json)),
  CHECK (issue_count >= 0)
);

CREATE INDEX idx_validation_report ON validation_report(project_id, chapter_id, created_at);


-- **扁平 Proposal 队列永不做**——它是错误心智模型的 UI 化身（ADR 0004）。
-- 确认必须是对**聚类**的：「李明在 47 章里被叫作明哥 —— 全部接受？」= 1 次点击换 47 条。
-- 所以这张表叫 proposal_set，主键是那个「集」，不是那 47 条里的某一条。
-- 且它是 exception-driven 的（§5.4）：高置信度 + 与 CANON 无冲突的抽取结果默认直接落
-- PROVISIONAL，**根本不产生行**。只有「与 CANON 直接冲突」和「confidence<0.7 且涉及
-- 主要人物」两类进这里，每章 0–2 条。
CREATE TABLE proposal_set (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,     -- 开放字符串：alias_cluster | edge_conflict | ...
  summary         TEXT NOT NULL DEFAULT '',
  item_count      INTEGER NOT NULL DEFAULT 1,
  items_json      TEXT NOT NULL DEFAULT '[]',
  confidence      REAL,
  status          TEXT NOT NULL DEFAULT 'PENDING',
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  resolved_at     TEXT,
  decision_log_id TEXT REFERENCES decision_log(id),
  -- CHECK 死是因为 UI 只查 status='PENDING'：写错一个值 = 这一聚类**永远不再露面**，
  -- 那是错误答案不是难看的日志。
  CHECK (status IN ('PENDING','ACCEPTED','REJECTED','EDITED')),
  CHECK (item_count >= 1),
  CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
  CHECK (json_valid(items_json))
);

CREATE INDEX idx_proposal_set_pending ON proposal_set(project_id, status);


PRAGMA user_version = 1;
