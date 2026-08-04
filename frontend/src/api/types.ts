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

export interface DraftResult {
  experimental: boolean;
  note: string;
  text: string;
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
  StateDim: "状态维度",
  Chapter: "章",
};

/** 作者能自己建的 6 类，附一句「什么时候用它」。
 *
 * **故意不含 `StateDim` 和 `Chapter`**：前者是引擎内部的状态维度，后者由 import 生成——
 * 把它们放进「新建」菜单等于邀请作者手工造出引擎的内部结构。上面的 `LABEL_ZH` 仍要认它们
 * （花名册会显示），两张表的差集就是这条区分本身，别合并。
 */
export const AUTHORED_LABELS: { label: NodeLabel; hint: string }[] = [
  { label: "Character", hint: "会出现在认知矩阵行上的人" },
  { label: "Location", hint: "「他在哪」的那个哪" },
  { label: "Secret", hint: "认知矩阵的列。声明谁知道它之前，得先有它" },
  { label: "Faction", hint: "门派 / 家族 / 组织" },
  { label: "Object", hint: "玄铁令这类会易主的东西" },
  { label: "Foreshadow", hint: "你打算在后面回收的那把枪" },
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

export interface KnowledgeMatrix {
  project_id: string;
  chapter: number;
  scope: string;
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
  project: Project | null;
  initial_chapter: number | null;
  import_report: ImportReport | null;
}

export interface CheckResult {
  chapter: number;
  scene_count: number;
  rules_run: string[];
  issues: Issue[];
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
