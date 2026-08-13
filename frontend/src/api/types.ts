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
  /** 其中**供应商报了 token 用量的**有几条。`calls - metered_calls` = 一个数都没给的那几次。
   *  和 `priced_calls` 是同一对形状（计数 + 可空的合计），界面上照同一种方式说。 */
  metered_calls: number;
  /** **只是报了的那几次的合计**；一次都没报是 null（不是 0）。
   *  供应商不报 usage 时后端就是 null —— 而可中断起草开了之后这一档是常态，不是边角。 */
  tokens_in: number | null;
  tokens_out: number | null;
  ms: number | null;
  /** 其中填了金额的有几条。今天恒为 0，所以 `cost` 恒为 null。 */
  priced_calls: number;
  cost: number | null;
}

export interface RunsPanel {
  entries: ActivityEntry[];
  run_count: number;
  totals: CostTotals;
}

// ══════════════════════════════════════════════════════════════════════════
// 写作助手（模式二，ADR 0019）：会话 / 一轮的回执
// ══════════════════════════════════════════════════════════════════════════
//
// **这几个形状是「对话的投影」，不是对话的原文**（`api/chat.py` 的模块 docstring 第三条）。
// 工具返回和「只叫工具没说话」的那几条后端根本没发出来——它们是内部模型，里头躺着
// `NodeRef` 的裸 id。**前端不许自己去别处把它们捞回来补上**：收窄的最强形态是根本没到手，
// 而把它捞回来渲染当场就是屏幕上的研发术语（`src/test/screenGuard.ts` 第三张网）。

/** 屏幕上只有两种说话人。少掉的那一部分有一个数（`TurnReceipt.lookups`），
 *  查了几次说得出来，查到了什么不上屏。 */
export type ChatSpeaker = "author" | "assistant";

export interface ChatMessageView {
  /** 在这段对话历史里的位置。**当 key 用，不是业务标识。** */
  seq: number;
  speaker: ChatSpeaker;
  text: string;
}

export interface ChatSessionView {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  /** 历史有多少条，**含不上屏的那些**。所以它和屏幕上的气泡数对不上，
   *  界面上不许把它当「你们说了几句」显示出来。 */
  message_count: number;
  /** 这一刻正在跑一轮。**进程内事实**，后端一重启就恒为 false。 */
  running: boolean;
  /** 上一轮**断在半路**、还缺结果的那几步。不为零 = 进程死在了模型调用和派发之间；
   *  下一轮会先把它们补跑掉（ADR 0019 的 resume），所以这儿不需要一颗「恢复」按钮，
   *  但侧栏上必须看得出来——断掉的和跑完的长得一样，作者就无从知道该不该接着说。 */
  pending_lookups: number;
}

export interface ChatDetail {
  session: ChatSessionView;
  messages: ChatMessageView[];
}

/** 这一轮发给模型的那份上下文**裁掉了什么**。零不写，非零必须说得出理由（§10 约束 8）。 */
export interface ChatContextReceipt {
  off_chapter: number;
  stale_lookups: number;
  trimmed_results: number;
  dropped_lookups: number;
  dropped_reasoning: number;
  lost_lookups: number;
  /** 剪到只剩作者说过的话仍然装不下。**这一档不砍作者的话**，这一轮直接停。 */
  full: boolean;
}

/**
 * 摆在桌上的一稿（[ADR 0022](docs/adr/0022-drafting-is-a-proposal-not-a-write.md)）。
 * **正文不在这儿**——要正文单取一次（`DraftCandidateDetail`）。
 *
 * **「推荐哪一版」不是引擎算的，也不是这一层算的**（ADR 0005：引擎不给散文打分）：
 * `note` 是写那一稿的那个模型自己交的一句话，`landed` 是助手真的做过的一个动作
 * ——「它把哪一版写进了书」就是它的推荐。一批都没落盘时后端不替作者挑，
 * **前端也不许从字里行间反推一个「最好的那版」**（同「跳转坐标由后端给」那条禁令）。
 */
export interface DraftCandidateView {
  /** 取全文用它。**一个字符都不许上屏**——它是 `draft:01J…` 这种形状，
   *  `src/test/screenGuard.ts` 第三张网认的就是它。作者认得的是「第 2 稿」。 */
  id: string;
  chapter: number;
  /** 这一章的第几稿。**屏幕上说的是这个数。** */
  ordinal: number;
  /** 字数。**今天恒是中文口径**（起草台只用中文档，见 `drafts.ts::unitsLabel`）。 */
  units: number;
  /** 写它的那个模型自己那句话。**可能是空串**（它这次没说）——空的时候界面上别硬编一句，
   *  后端没有替它编（ADR 0005）。 */
  note: string;
  /** 开头一段，定长（后端 `agent.candidates.PREVIEW_UNITS` + `……`）。 */
  preview: string;
  created_at: string;
  /** 它进过书没有。**不是「被选中」**：作者可以在版本历史里把它退回去。 */
  landed: boolean;
  /** **空 = 这一稿写完了**；非空 = 它被砍断了（作者按了「停」），这句话说明为什么。
   *
   *  **非空时必须画出来。** 一稿断在半句上而屏幕不说，作者会以为写作模型就写成了这样
   *  ——预览和全文长得和一份写完的稿子一模一样，没有第二个地方能告诉他。
   *  **措辞照抄，别按这一位自己造一句**（后端 `AUTHOR_STOPPED_NOTE` 是唯一出处）。 */
  stopped_reason: string;
}

/** 一稿的全文。**摊开那一版读的就是它。** */
export interface DraftCandidateDetail extends DraftCandidateView {
  /** 一稿正文，**不含章标题**（那一行是切章的锚，属于作者）。 */
  text: string;
}

export interface ChapterDrafts {
  drafts: DraftCandidateView[];
}

/** 停法的机器码。**一个都不许上屏**——它是 snake_case，形状判据会当场咬住它。
 *  给作者看的那句话是 `TurnReceipt.message`（后端 `stop_wording()` 写好的）。
 *
 *  **十一种，而且这份清单不是手抄的**：`tests/test_wording_guard.py` 拿 Python 那边的
 *  `StopReason` 逐个来比，少一个就红。2026-08-12 之前这儿少了 `asked_author`
 *  （第十一种停法），而全仓没有任何东西钉着它——于是那一档在类型上根本不存在，
 *  接口给了、界面收不到。 */
export type ChatStopReason =
  | "done"
  | "asked_author"
  | "step_limit"
  | "cost_limit"
  | "batch_too_wide"
  | "author_stopped"
  | "repeated_call"
  | "no_output"
  | "tool_stuck"
  | "context_full"
  | "model_unreachable";

/**
 * 它停下来问作者的那一句 + 几个可点的选项（[ADR 0024](docs/adr/0024-a-turn-is-a-conversation-not-a-black-box.md)）。
 *
 * **两个字段全是模型自己的字**：后端那条 handler 里连一个数据来源都没有，
 * 引擎往问句里加不了一个字。所以这一层**照样一个字都不许加**——
 * 别在选项后面补一句「（推荐）」，那是引擎在给散文打分（ADR 0005）。
 *
 * `options` 可以是空的（模型只问了一句没给选项）：那时界面上就只有输入框，
 * **不许自己编两个选项出来**。
 */
export interface ChatAuthorQuestion {
  question: string;
  options: string[];
}

/** 一轮跑到哪儿了（后端 `agent.loop.TurnEventKind`）。**机器码，一个字都不上屏。**
 *
 *  同 `ChatStopReason`：这份清单由 `tests/test_wording_guard.py` 拿 Python 那个枚举钉着。 */
export type ChatTurnEventKind =
  | "tool_started"
  | "tool_finished"
  | "reply_text"
  | "reply_delta"
  | "draft_started"
  | "draft_delta"
  | "draft_kept"
  | "draft_failed"
  | "asked_author"
  | "turn_stopped";

/**
 * 一轮跑到一半时后端喊的那一声（ADR 0024 决策一）。长连接上的中间帧。
 *
 * ── 这里**没有**「工具查到了什么」，而且是类型层没有 ──────────────────────
 *
 * 后端那个模型上根本没有一个字段装得下工具返回（`ToolOutcome.content`）：
 * 查完了那一声只有「成没成」和「第几章」。**这一层也不许从别处把它捞回来补上**
 * ——那里面是 `NodeRef` 的裸 id 加作者写在秘密节点上的 `twist`，
 * 而一条事件一旦被推上屏，那段话就在作者的持久化对话里了，改代码删不掉。
 *
 * ── 措辞在后端，这一层不翻第二遍 ────────────────────────────────────────
 *
 * | 字段 | 谁写的 | 上屏吗 |
 * |---|---|---|
 * | `said_to_author` | **引擎**（`TurnEvent` 那几个构造口 + `stop_wording()`） | 上 |
 * | `text` | **模型**（回话的字 / 稿子的字） | 上 |
 * | `kind` / `tool` / `reason` | 机器码 | **一个字都不许上** |
 */
export interface ChatTurnEvent {
  kind: ChatTurnEventKind;
  /** 说给作者听的那一句。**措辞的唯一出处在后端。** */
  said_to_author: string;
  /** 模型自己写的字，原样。回话的一段 / 一稿的一片。 */
  text: string;
  /** 动的是工具表里哪一条（机器码）。认不出的是空的。**只用来分派，不上屏。** */
  tool: string;
  ok: boolean | null;
  chapter: number | null;
  index: number;
  total: number;
  /** **同一条字流的片归到一起。** 一批三稿是同时在写的，三条流的片会交错着到达，
   *  而同一章的三稿连 `chapter` 都一样——没有这个数就没法把它们分开摆。
   *  `0` = 这一轮只有一条流。**它不上屏**，它是分组用的钥匙。 */
  stream: number;
  ordinal: number;
  units: number;
  reason: ChatStopReason | null;
  asked: ChatAuthorQuestion | null;
}

export interface TurnReceipt {
  session: ChatSessionView;
  chapter: number;
  reason: ChatStopReason;
  /** 说给作者的那一句。**措辞的唯一出处在后端**，前端不许再翻一遍。 */
  message: string;
  reply: string;
  /** 这一轮新长出来、上得了屏的那几条。 */
  messages: ChatMessageView[];
  steps: number;
  /** 这一轮查了几次资料（工具调用次数）。**查到了什么不上屏。** */
  lookups: number;
  tokens_reported: number;
  /** 有几次调用没量准。**不为零时上面那个数是低估**，界面上不许把它当全部。 */
  calls_without_usage: number;
  context: ChatContextReceipt;
  /** 它停下来问了作者一句（ADR 0024）。**非空 ⇔ `reason === "asked_author"`。**
   *
   *  **它必须从回执上读，不能只从事件流上读**：事件是「跑的过程」，而作者可能在这一轮
   *  结束之后才打开那段对话（刷新页面、换台机器、三个月后回来）——那时唯一还说得出
   *  「它当时问了你什么」的就是这个字段。 */
  asked: ChatAuthorQuestion | null;
  /** 这一轮写出来的那几稿，后端已按「第几章 + 第几稿」排好序（ADR 0022）。
   *
   *  **界面照这个顺序摆，不许自己再排一遍**：并发跑的稿子谁先回来是随机的，
   *  按别的口径重排会让作者每次刷新看到的次序都不一样。
   *
   *  `landed=true` 的那一稿已经在书里了 ⇒ **那一章的正文变了**，该重取一次。 */
  drafts: DraftCandidateView[];
}

export interface ChatStopped {
  chat_id: string;
  /** `false` = 这一刻它本来就没在跑。**不是失败。** */
  stopped: boolean;
  message: string;
}

export interface ChatDeleted {
  chat_id: string;
  deleted: boolean;
}
