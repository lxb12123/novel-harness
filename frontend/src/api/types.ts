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

export type NodeLabel =
  | "Character"
  | "Location"
  | "Faction"
  | "Secret"
  | "Foreshadow"
  | "Object"
  | "StateDim"
  | "Chapter";

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
