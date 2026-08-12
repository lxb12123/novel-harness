// 前端消费的引擎出参形状（手写，当前的真相源）。
//
// 为什么手写而不是全用 openapi-typescript 生成：收窄端点（resolve / subgraph / state /
// nodes）返回的是 _narrow 之后的 dict、签名是 `-> Any`，所以 FastAPI 的 openapi 里这些
// 端点没有 response schema——生成出来的类型是 `unknown`。`npm run gen:types` 仍然配好了
// （schema.ts 覆盖 project 等有模型的端点），但在后端给收窄端点补 response_model 之前，
// 这个文件是这些形状的权威。补齐那步是「让 openapi-typescript 真正兑现」的后续。
//
// 铁律的前端影子：这里**没有一个类型带章号输入字段**。`valid_from` 只在出参里出现
// （Edge / KnowledgeCell），是系统算出来的产物；declare 的入参类型（DeclareKnows…）
// 一个 chapter 字段都没有（约束 10）。

export type DraftLanguage = "zh" | "en";

/** 用户选择的人类长度单位；zh 计非空白字符，en 计 words。 */
export interface DraftLengthSpec {
  language: DraftLanguage;
  min_units: number;
  target_units: number;
  max_units: number;
}

export interface AiSettings {
  base_url: string;
  model: string;
  api_key_set: boolean;
  api_key_preview: string;
}

export interface AiSettingsInput {
  base_url: string;
  model: string;
  /** 空 = 保持原钥匙（改地址/模型不用重粘）。 */
  api_key?: string;
}

export interface DraftRequest {
  goal: string;
  cast: string[];
  length: DraftLengthSpec;
  form?: string;
  previous_tail?: string;
  /** 自定义文风，留空 = 默认。三臂共用，禁词由后端校验。 */
  house_style?: string;
}

/** 这一稿的记忆层到底装了什么。**零永远带着一句理由**（§10 约束 8）。
 *
 *  `unsummarized_chapters` 是「有正文、但还没生成滚动总结」的章号——它和
 *  `rolling_summaries === 0` 不是一回事：后者可能只是书还没写到那么长。 */
export interface DraftMemory {
  assembled: boolean;
  note: string;
  profiles: number;
  recent_events: number;
  background_events: number;
  rolling_summaries: number;
  unsummarized_chapters: number[];
}

/** 一章在滚动总结上的状态。三种「没有」分得开：没写 / 写了没生成 / 有。 */
export interface ChapterSummaryStatus {
  chapter_number: number;
  has_text: boolean;
  summary: string | null;
  created_at: string | null;
}

/** 起草第 `chapter` 章时，滚动总结覆盖的那个区间。
 *
 *  窗口边界由后端算（`rolling_summary_window`）——前端**不许**自己按
 *  「近八章」推一份，那是第二个会漂的常量。 */
export interface SummaryWindow {
  chapter: number;
  window_first: number;
  window_last: number;
  chapters: ChapterSummaryStatus[];
  summarized: number;
  /** 有正文、还没生成总结的章号。**这才是要提示作者的那一种零。** */
  missing: number[];
}

/** 后台整理某一章时，单件活的去向：排上了 / 不用做（已经有了）/ 那一章还没正文。 */
export type AutopilotTask = "queued" | "skipped" | "no_text";

/** 「把这一章交给后台整理」的回执。**作者看不到它**——这条路径存在的全部意义
 *  就是他不必知道后台在干活（他只会在下次按「起草」时发现资料已经齐了）。 */
export interface AutopilotAck {
  chapter: number;
  summary: AutopilotTask;
  extraction: AutopilotTask;
}

/** 某一章的后台整理进行到哪了。起草前用它决定「等它」还是「自己当场跑一遍」——
 *  两条都跑就是同一次模型调用付两遍钱。 */
export interface AutopilotStatus {
  chapter: number;
  summary_ready: boolean;
  extraction_ready: boolean;
  running: boolean;
}

export interface DraftResult {
  experimental: boolean;
  note: string;
  text: string;
  memory: DraftMemory;
  length: {
    language: string;
    unit: string;
    actual_units: number;
    status: string;
  };
  attempts: number;
  model: string;
  finish_reason: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
}

export type NodeLabel =
  | "Character"
  | "Location"
  | "Faction"
  | "Secret"
  | "Foreshadow"
  | "Object"
  | "StateDim"
  | "Chapter";

/** label → 中文。**8 类全列**：花名册里也会出现引擎自己建的节点（Chapter 由 import 生成）。 */
export const LABEL_ZH: Record<NodeLabel, string> = {
  Character: "人物",
  Location: "地点",
  Faction: "势力",
  Object: "物品",
  Secret: "秘密",
  Foreshadow: "伏笔",
  StateDim: "状态",
  Chapter: "章",
};

/** 作者能自己建的 6 类，附一句「什么时候用它」。
 *
 * **故意不含 `StateDim` 和 `Chapter`**：前者是引擎内部的状态维度，后者由 import 生成——
 * 把它们放进「新建」菜单等于邀请作者手工造出引擎的内部结构。上面的 `LABEL_ZH` 仍要认它们
 * （花名册会显示），两张表的差集就是这条区分本身，别合并。
 */
export const AUTHORED_LABELS: { label: NodeLabel; hint: string }[] = [
  { label: "Character", hint: "故事中的人物" },
  { label: "Location", hint: "故事发生的地点" },
  { label: "Secret", hint: "尚未公开的信息或真相" },
  { label: "Faction", hint: "门派、家族或组织" },
  { label: "Object", hint: "对情节有影响的物品" },
  { label: "Foreshadow", hint: "准备在后文回应的线索" },
];

export interface Project {
  id: string;
  name: string;
  root_path: string;
  canon_version: number;
}

export interface ChapterRow {
  number: number;
  title: string;
}

export interface ChapterText {
  number: number;
  markdown: string;
}

export interface ChapterSnapshot {
  snapshot_id: string;
  text_sha256: string;
  text: string;
  created_at: string;
  is_current: boolean;
}

/** 收窄后的窄引用：Secret / 未来节点只剩这三键（props 被 _narrow 摘掉了）。 */
export interface NodeRef {
  id: string;
  label: NodeLabel;
  name: string;
}

export type KnowledgeState = "KNOWS" | "BELIEVES" | "UNKNOWN";

export interface KnowledgeCell {
  character_id: string;
  secret_id: string;
  state: KnowledgeState;
  since_chapter: number | null;
  believed_value: string | null;
  evidence_id: string | null;
}

/** 这张表是**哪一版**的图算出来的。
 *
 *  `canon_version` 是改这一格时要带回去的那个数（`expected_canon_version`）——
 *  它随数据一起来，所以作者按下按钮时对的是**他看到的那一版**。从别处另取一次
 *  就是第二个会漂的源：中间有人升过 CANON 的话，那次 CAS 会放过一次它本该拦下的改动。
 *  `indexed_version` / `staleness` 是 v1.1 向量层的位子，v1 恒为 0。 */
export interface GraphVersion {
  canon_version: number;
  indexed_version: number;
  staleness: number;
}

export interface KnowledgeMatrix {
  project_id: string;
  chapter: number;
  scope: string;
  version: GraphVersion;
  /** 作者声明了、但解析不出唯一节点的称呼。非空 = 面板不完整，须消歧。 */
  unresolved_cast: string[];
  characters: NodeRef[];
  secrets: NodeRef[];
  cells: KnowledgeCell[];
}

export interface ForbiddenEntity {
  node: NodeRef;
  first_appears_chapter: number;
  surfaces: string[];
}

export interface SceneConstraints {
  chapter: number;
  unresolved_cast: string[];
  must_not_reveal: NodeRef[];
  forbidden_entities: ForbiddenEntity[];
}

export interface StateValue {
  dim: NodeRef | { id: string; label: NodeLabel; name: string; props?: unknown };
  dim_key: string | null;
  value: string | null;
  value_key: string | null;
  since_chapter: number;
}

// state 出参里的 node/location 可能是完整 Node（present 角色）或收窄的 NodeRef（未来节点）。
// 前端只读 id/label/name，所以按 NodeRef 用即可。
export interface StateSnapshot {
  node: NodeRef & { props?: unknown };
  chapter: number;
  scope: string;
  location: (NodeRef & { props?: unknown }) | null;
  states: StateValue[];
  edges: Edge[];
  is_dead: boolean;
}

export interface Scene {
  number: number;
  cast: string[];
  loc: string | null;
  goal: string | null;
  para_index: number;
  decl_text: string;
}

export interface QuoteCandidate {
  chapter_id: string;
  chapter_number: number;
  snapshot_id: string;
  para_index: number;
  occurrence_k: number;
  matched_text: string;
  context: string;
}

/** 引擎的 9 类关系（`graph/models.py::EdgeType`）。
 *  `tests/test_wording_guard.py` 拿 Python 那个枚举**逐个**比这份联合类型，少一个就红。 */
export type EdgeType =
  | "LOCATED_AT"
  | "MEMBER_OF"
  | "RELATED_TO"
  | "KNOWS"
  | "BELIEVES"
  | "HAS_STATE"
  | "OWNS"
  | "PLANTED_IN"
  | "RESOLVED_IN";

/** 关系类型 → 作者的说法。**9 类全列**，而且类型上必须全列：
 *  `Record<EdgeType, string>` 让「漏一行」变成一个编译错误，不是一句运行时兜底。
 *
 *  ── 这张表为什么在这儿，而不是三份散在组件里 ──────────────────────────────
 *  2026-08-11 之前它有**三份拷贝**（`BottomBar` / `EvidenceTab` / `LocalGraph`），
 *  每一份都只有 7 行（`PLANTED_IN` / `RESOLVED_IN` 一个都没有），而前两份的兜底写的是
 *  `?? e.type`——也就是说那两类边一旦被写出来，屏幕上就是 `PLANTED_IN` 四个大写字母。
 *  措辞的**唯一**出处是后端（`activity._EDGE_LABEL`），这张表是它在浏览器里的投影；
 *  投影只该有一份，且必须被守卫钉住它和后端说的是同一句话。 */
export const EDGE_ZH: Record<EdgeType, string> = {
  KNOWS: "知道",
  BELIEVES: "以为",
  LOCATED_AT: "在",
  MEMBER_OF: "属于",
  RELATED_TO: "关系",
  HAS_STATE: "状态",
  OWNS: "有",
  PLANTED_IN: "埋在",
  RESOLVED_IN: "回应于",
};

/** 认不出的类型退到一句中文，**绝不原样回吐**（同后端 `_edge_label`）。 */
export const edgeName = (type: string): string =>
  EDGE_ZH[type as EdgeType] ?? "关系";

export interface Edge {
  id: string;
  src: string;
  dst: string;
  type: string;
  valid_from_chapter: number;
  valid_to_chapter: number | null;
  evidence_id: string | null;
  props: { believed_value?: string | null; value?: string | null };
}

/** 一次成功声明的回执。closed/retracted 是产品招牌动作，前端务必展示。 */
export interface Declaration {
  edge: Edge;
  evidence: { id: string; chapter_number: number };
  decision_id: string;
  closed: Edge[];
  retracted: Edge[];
}

// subgraph 的节点：present 节点是完整 Node，Secret/未来节点被 _narrow 成 NodeRef。
// 前端只读 id/label/name，按 NodeRef 用即可。
export type SubgraphNode = NodeRef & { props?: unknown };

export interface Subgraph {
  center: SubgraphNode;
  chapter: number;
  hops: number;
  scope: string;
  nodes: SubgraphNode[];
  edges: Edge[];
  /** 节点数超上限被折叠过（hops≤2 硬上限，3 跳数学上坏）。 */
  truncated: boolean;
}

export interface ResolveHit {
  node: NodeRef;
  kind: string;
  usable_for_rules: boolean;
}

export interface ResolveResult {
  surface: string;
  ambiguous: boolean;
  unique_id: string | null;
  hits: ResolveHit[];
}

export interface ImportReport {
  chapter_count: number;
  preamble_chars: number;
  written: string[];
  unchanged: string[];
  synced: { added: unknown[]; refreshed: unknown[]; unchanged_count: number; ignored_files: string[] };
}

export type BootstrapRequest =
  | { mode: "import"; name: string; text: string }
  | { mode: "blank"; name: string };

export interface BootstrapResult {
  project: Project;
  initial_chapter: number;
  import_report: ImportReport | null;
}

export interface CheckResult {
  chapter: number;
  scene_count: number;
  rules_run: string[];
  issues: Issue[];
}

/** 本章正文里提到的花名册称呼。**不是「在场」**——引擎在数字符串，没有读懂剧情。 */
export interface Mentioned {
  chapter: number;
  /** `false` = 这一章磁盘上还没有正文。和「写了但没提到人」不是一回事，别合并显示。 */
  has_text: boolean;
  surfaces: string[];
}

export interface Issue {
  rule: string;
  issue_type: string;
  chapter: number;
  anchor: { para_index: number; quote_text: string; occurrence_k: number };
  message: string;
  suggested_action: string | null;
}

// declare 入参 —— 全是称呼原文 + 引语，**没有章号**（约束 10）。
export interface DeclareKnows {
  who: string;
  secret: string;
  quote: string;
}
export interface DeclareBelieves {
  who: string;
  secret: string;
  believed_value: string;
  quote: string;
}
export interface DeclareWhere {
  who: string;
  loc: string;
  quote: string;
}

// 建节点 / 加称呼 —— 同样没有章号，但理由不同：上面那三条是**边**，边才是时态的；
// 节点不在时间轴上（一个人不会「从第 88 章起是人物」），所以这里天然无从填起。
export interface DeclareNode {
  label: NodeLabel;
  name: string;
  aliases?: string[];
  /** 仅 Secret：秘密的内容，进 secret 扩展表（不进 node.props）。 */
  description?: string;
  /** 仅 Secret：父秘密的**称呼原文**（拆子事实用）。解析不出 / 歧义 → 服务端拒绝，不替你挑。 */
  sub_of?: string | null;
}

export type AliasKind = "canonical" | "alias" | "nickname" | "title";

export interface DeclareAlias {
  of: string;
  surface: string;
  /** `canonical` 会被服务端拒——它是 upsert_node 的独占物，改本名请改节点本身。 */
  kind?: AliasKind;
  usable_for_rules?: boolean;
}

export interface StoredAlias {
  id: string;
  project_id: string;
  node_id: string;
  surface: string;
  kind: AliasKind;
  usable_for_rules: boolean;
}

// ══════════════════════════════════════════════════════════════════════════
// M4：事件超边 / 提案审阅 / 被动确认（形状来自 test_frontend_contract 的 fixture）
// ══════════════════════════════════════════════════════════════════════════

export type ProposalKind = "low_confidence_main" | "edge_conflict" | "new_character";
export type ProposalStatus = "PENDING" | "ACCEPTED" | "REJECTED" | "EDITED";
export type ProposalAction = "accept" | "reject" | "bystander";

export interface StoryEvent {
  id: string;
  project_id: string;
  chapter_number: number;
  summary: string;
  information_scope: string;
  status: string;
  confidence: number | null;
  source: string;
  evidence_id: string;
  evidence_status: string;
  derived_from_event_id: string | null;
}

/** 事件超边的一条视图：事件 + 参与者/知情者/揭晓事实（NodeRef 收窄引用）。 */
export interface EventView {
  event: StoryEvent;
  participants: NodeRef[];
  knowers: NodeRef[];
  revealed_facts: NodeRef[];
}

export interface ProposalRecord {
  id: string;
  project_id: string;
  kind: ProposalKind;
  summary: string;
  item_count: number;
  /** 开放 JSON 项；组件只消费下面抽出来的具名字段，绝不直接渲染 items_json。 */
  items: unknown[];
  confidence: number | null;
  status: ProposalStatus;
  chapter_number: number | null;
  base_canon_version: number;
  event_ids: string[];
  edge_ids: string[];
  /** `items` 里那些裸 id 的显示名。**别拿 id 去花名册里自己查**——那是另一条独立缓存，
   *  后台整理造出的新节点会在它里面缺席一拍，而那一拍的产物是屏幕上一串截断的内部编号
   *  （`n:ID22`）。认不出的 id 不在这份名单里（后端不编假名字）。 */
  node_refs: NodeRef[];
}

export interface ProposalResolution {
  proposal_id: string;
  status: "ACCEPTED" | "REJECTED" | "EDITED";
  canon_version: number;
  decision_id: string;
  event: EventView | null;
  character: NodeRef | null;
  events: EventView[];
  edges: unknown[];
  characters: NodeRef[];
}

export interface ProvisionalConfirmation {
  confirmation_id: string;
  project_id: string;
  canon_version: number;
  decision_id: string;
  events: EventView[];
  edges: unknown[];
}

export type ExtractionRunStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED";

export interface ExtractionRun {
  id: string;
  project_id: string;
  chapter_number: number;
  snapshot_id: string;
  status: ExtractionRunStatus;
  errors: { kind: string; message: string }[];
  valid_event_count: number;
  discarded_event_count: number;
  proposal_count: number;
  model_call_id: string | null;
  schema_version: string;
  prompt_hash: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** 低置信事件项（kind=low_confidence_main 且 source_kind=event）。 */
export interface LowConfidenceEventItem {
  source_kind: "event";
  event_id: string;
  summary: string;
  confidence: number;
  quote: string;
}

/** 冲突项（kind=edge_conflict）：当前 CANON vs 抽取器提议。 */
export interface EdgeConflictItem {
  update_kind: "location" | "state" | "relationship";
  current: { edge_id: string; subject_id: string; target_id: string; value: string | null };
  proposed: {
    edge_id: string;
    subject_id: string;
    target_id: string;
    value: string | null;
    quote: string;
  };
}

export interface NewCharacterItem {
  surface: string;
  confidence: number;
  profile: {
    surface: string;
    gender: string | null;
    personality: string | null;
    background: string | null;
    character_notes: string | null;
    confidence: number;
  };
}

// ══════════════════════════════════════════════════════════════════════════
// 改一条**已经生效**的事实（ADR 0020 的「可改」）
// ══════════════════════════════════════════════════════════════════════════
//
// 这两组入参同样**一个章号字段都没有**（约束 10）：改的是「这条事实说错了」，
// 不是「它从第几章开始成立」——后者只由证据决定，新事实的生效章从被改的那条上继承。
// 出参里的 `since_chapter` 是那条继承的**产物**，面板要显示它。

export type KnowledgeEdgeType = "KNOWS" | "BELIEVES";

export interface KnowledgeEditInput {
  character_id: string;
  secret_id: string;
  to_type: KnowledgeEdgeType;
  /** `to_type="BELIEVES"` 时必填、`"KNOWS"` 时必须不传（后端两边都会拒）。 */
  believed_value?: string;
  expected_canon_version: number;
}

/** 改完一格之后的回执。**没有整条边、也没有秘密正文**：这条边的另一端按定义是个
 *  Secret，整份序列化出去就是保密清单自己泄密。 */
export interface KnowledgeCorrection {
  project_id: string;
  canon_version: number;
  decision_id: string;
  character: NodeRef;
  secret: NodeRef;
  from_type: KnowledgeEdgeType;
  to_type: KnowledgeEdgeType;
  believed_value: string | null;
  /** **继承来的，不是作者填的**（约束 10）。 */
  since_chapter: number;
  edge_id: string;
  retracted_edge_id: string;
  closed_edge_ids: string[];
}

/** 改一条已生效事件的知情 / 在场名单。
 *
 *  两个字段都是**绝对集合**（「改完之后是这些人」），`null`/不传 = 这一维不动。
 *  所以重发同一份请求是空操作，不会把同一个人加两遍。 */
export interface EventCastInput {
  knower_ids?: string[] | null;
  participant_ids?: string[] | null;
  expected_canon_version: number;
}

export interface EventCastCorrection {
  project_id: string;
  canon_version: number;
  decision_id: string;
  event: EventView;
  knowers_added: NodeRef[];
  knowers_removed: NodeRef[];
  participants_added: NodeRef[];
  participants_removed: NodeRef[];
}

// ══════════════════════════════════════════════════════════════════════════
// 活动记录（ADR 0020 的「可查」）：抽取 / 模型调用 / 确认三张表归并成一条时间线
// ══════════════════════════════════════════════════════════════════════════

export type ActivitySource = "extraction" | "model_call" | "decision";
export type ActivityStatus = "succeeded" | "failed" | "running" | "pending";
export type JumpTarget = "knowledge_cell" | "event_cast" | "proposal" | "chapter";

/** 这一条记录改的东西能从哪儿改回去。**坐标和措辞全由后端给**——
 *  前端不许从 title / source 反推跳哪儿去，那就是第二份路由表。 */
export interface ActivityJump {
  target: JumpTarget;
  /** 按钮上的那句话。后端写，前端不编。 */
  label: string;
  chapter_number: number | null;
  character_id: string | null;
  secret_id: string | null;
  event_id: string | null;
  proposal_id: string | null;
  /** 今天真能改这个目标的路由。**空数组是一个断言**（「今天没有任何入口能改它」），
   *  不是「后端忘了填」——照它画一个编辑按钮等于把 ADR 0020 的推翻条件关掉。 */
  endpoints: string[];
  /** 跳过去之后右栏那张表按哪几个称呼算行，**原样进 `?cast=`，前端一个字都不解析**。
   *
   *  认知矩阵的行由本章正文推（ADR 0018），而声明的生效章由引语定（ADR 0006）——
   *  日志里那个人可能在那一章正文里一次都没被点名，那一行就不在表上。这个坐标把它补回来。
   *
   *  空数组 = 后端给不出一个不含歧义的坐标（称呼指向不止一个人 / 没登记过称呼），
   *  **那时绝不许前端拿屏幕上的人名自己去凑一个**——那正是「从标题反推」。 */
  cast: string[];
}

/** 折叠层：一行一条，作者扫一眼就该知道的全部。
 *  **没有 payload**：那是开放 JSON 审计信封，塞进每一行等于打开日志页就把全库
 *  审计内容拉进浏览器。 */
export interface ActivityEntry {
  id: string;
  source: ActivitySource;
  ts: string;
  /** `author` / `system`（开放字符串，同 `decision_log.actor`）。 */
  actor: string;
  status: ActivityStatus;
  title: string;
  subtitle: string;
  chapter_number: number | null;
  jump: ActivityJump | null;
}

/** 展开详情里的一行「标签 → 值」。**措辞全在后端**，前端不写文案分支。 */
export interface DetailRow {
  label: string;
  value: string;
}

/** 这一步花了多少。**null 是「没记」不是 0**（§10 约束 8）：`model_call.cost`
 *  至今没有写入方（BYOK 之下引擎不知道作者签的什么单价）。 */
export interface ActivityCost {
  call_id: string;
  capability: string;
  model: string;
  tokens_in: number | null;
  tokens_out: number | null;
  ms: number | null;
  cost: number | null;
}

export interface ActivityDetail {
  /** 折叠行原样带回，**详情响应自足**——前端不必把列表那一行拼进来。 */
  entry: ActivityEntry;
  rows: DetailRow[];
  cost: ActivityCost | null;
  /** 只有抽取失败才非空。 */
  errors: string[];
  /** 只有 `source="decision"` 才有；已过后端的 `narrow_payload`。
   *  **界面不渲染它**：它是给机器看的审计信封（里面全是引擎内部字段），
   *  作者要看的那一份是 `rows`。 */
  payload: Record<string, unknown> | null;
}

/** 某个 actor 一共有多少行。**计数是全量的，不随当前过滤变**——
 *  它要回答的正是「我筛掉了多少」（ADR 0020）。 */
export interface ActorTally {
  actor: string;
  count: number;
}

export interface ActivityPage {
  entries: ActivityEntry[];
  /** 不透明串（后端内部是 `ts|id`）。**原样回传，前端不许自己拼。** */
  next_cursor: string | null;
  actors: ActorTally[];
}

export interface CostTotals {
  calls: number;
  tokens_in: number;
  tokens_out: number;
  ms: number;
  /** 其中填了金额的有几条。今天恒为 0，所以 `cost` 恒为 null。 */
  priced_calls: number;
  cost: number | null;
}

export interface RunsPanel {
  entries: ActivityEntry[];
  run_count: number;
  totals: CostTotals;
}
