import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, proj } from "./client";
import type {
  ChapterRow,
  ChapterSnapshot,
  ChapterText,
  CheckResult,
  Declaration,
  DeclareAlias,
  DeclareBelieves,
  DeclareKnows,
  DeclareNode,
  DeclareWhere,
  ImportReport,
  KnowledgeMatrix,
  NodeRef,
  Project,
  QuoteCandidate,
  ResolveResult,
  Scene,
  SceneConstraints,
  StateSnapshot,
  StoredAlias,
  Subgraph,
} from "./types";

// 服务端状态全进 TanStack Query（§2.3）：queryKey = [端点, pid, chapter, cast]，
// 坐标一变自动重取。写路径（save / declare）成功后**精确** invalidate 受影响的 key。

const q = (parts: unknown[]) => parts;

export function useProjects() {
  return useQuery({ queryKey: q(["projects"]), queryFn: () => api.get<Project[]>("/api/projects") });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<Project>("/api/projects", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

/** 导入整本 TXT（浏览器已把文件解码成文本）。成功后刷章目录 + 花名册 + 面板。 */
export function useImportBook(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api.post<ImportReport>(proj(pid, "/import"), { text }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chapters", pid] });
      qc.invalidateQueries({ queryKey: ["roster", pid] });
    },
  });
}

export function useChapters(pid: string | null) {
  return useQuery({
    queryKey: q(["chapters", pid]),
    queryFn: () => api.get<ChapterRow[]>(proj(pid!, "/chapters")),
    enabled: !!pid,
  });
}

export function useRoster(pid: string | null) {
  return useQuery({
    queryKey: q(["roster", pid]),
    queryFn: () => api.get<{ id: string; label: string; name: string }[]>(proj(pid!, "/roster")),
    enabled: !!pid,
  });
}

export function useHistory(pid: string | null, chapter: number, enabled: boolean) {
  return useQuery({
    queryKey: q(["history", pid, chapter]),
    queryFn: () => api.get<ChapterSnapshot[]>(proj(pid!, `/chapters/${chapter}/history`)),
    enabled: !!pid && enabled,
  });
}

export function useChapterText(pid: string | null, chapter: number, open: boolean) {
  return useQuery({
    queryKey: q(["text", pid, chapter]),
    queryFn: () => api.get<ChapterText>(proj(pid!, `/chapters/${chapter}/text`)),
    enabled: !!pid && open,
  });
}

export function useMatrix(pid: string | null, chapter: number, cast: string) {
  return useQuery({
    queryKey: q(["matrix", pid, chapter, cast]),
    queryFn: () =>
      api.get<KnowledgeMatrix>(proj(pid!, `/chapters/${chapter}/matrix?cast=${encodeURIComponent(cast)}`)),
    enabled: !!pid,
  });
}

export function useConstraints(pid: string | null, chapter: number, cast: string) {
  return useQuery({
    queryKey: q(["constraints", pid, chapter, cast]),
    queryFn: () =>
      api.get<SceneConstraints>(
        proj(pid!, `/chapters/${chapter}/constraints?cast=${encodeURIComponent(cast)}`),
      ),
    enabled: !!pid,
  });
}

export function useStates(pid: string | null, chapter: number, cast: string) {
  return useQuery({
    queryKey: q(["state", pid, chapter, cast]),
    queryFn: () =>
      api.get<StateSnapshot[]>(proj(pid!, `/chapters/${chapter}/state?cast=${encodeURIComponent(cast)}`)),
    enabled: !!pid,
  });
}

/** 换章号会失效所有跟章号/cast 挂钩的读端。声明成功后调它。 */
function invalidatePanels(qc: ReturnType<typeof useQueryClient>, pid: string) {
  for (const k of ["matrix", "constraints", "state", "roster", "subgraph"]) {
    qc.invalidateQueries({ queryKey: [k, pid] });
  }
}

export function useSaveChapter(pid: string, chapter: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (markdown: string) => api.put(proj(pid, `/chapters/${chapter}/text`), { markdown }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chapters", pid] });
      qc.invalidateQueries({ queryKey: ["text", pid, chapter] });
      invalidatePanels(qc, pid); // 正文变可能改动证据可定位性 → 面板重取
    },
  });
}

export function useLocate(pid: string) {
  return useMutation({
    mutationFn: (quote: string) => api.post<QuoteCandidate[]>(proj(pid, "/locate"), { quote }),
  });
}

type DeclareBody =
  | { kind: "knows"; body: DeclareKnows }
  | { kind: "believes"; body: DeclareBelieves }
  | { kind: "where"; body: DeclareWhere };

export function useDeclare(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, body }: DeclareBody) =>
      api.post<Declaration>(proj(pid, `/declare/${kind}`), body),
    onSuccess: () => invalidatePanels(qc, pid),
  });
}

/** 建一个节点（人物 / 地点 / 秘密 …）。幂等：键是 name，重复提交同一个名字不会建出两个。
 *
 * 这条路径是**整个工作台的起点**：import 只切章、不抽实体（ADR 0004 有意的），
 * 所以在花名册里有第一个人之前，认知矩阵 / 约束 / declare 三样头牌全都无从算起
 * （declare 的「填称呼」会必然 UnknownName 404）。成功后走 invalidatePanels——
 * 花名册从空变非空的那一刻，那三样才第一次有得算。
 */
export function useCreateNode(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    // 出参过 `_narrow`：Secret 收窄成 {id,label,name}，其余回完整 Node（带 props）。
    // 所以按 NodeRef + 可选 props 收（同 SubgraphNode 的处理）——UI 只读 label/name。
    mutationFn: (body: DeclareNode) =>
      api.post<NodeRef & { props?: unknown }>(proj(pid, "/nodes"), body),
    onSuccess: () => invalidatePanels(qc, pid),
  });
}

/** 给已有节点加一个称呼。**别名永不合并实体**（ADR 0004）。 */
export function useCreateAlias(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DeclareAlias) => api.post<StoredAlias>(proj(pid, "/aliases"), body),
    onSuccess: () => invalidatePanels(qc, pid),
  });
}

export function useCheck(pid: string) {
  return useMutation({
    mutationFn: (chapter: number) => api.post<CheckResult>(proj(pid, `/chapters/${chapter}/check`)),
  });
}

export function useCharacterState(pid: string | null, nodeId: string | null, chapter: number) {
  return useQuery({
    queryKey: q(["charstate", pid, nodeId, chapter]),
    queryFn: () => api.get<StateSnapshot>(proj(pid!, `/characters/${nodeId}/state?chapter=${chapter}`)),
    enabled: !!pid && !!nodeId,
  });
}

export interface EvidenceView {
  id: string;
  chapter_number: number;
  quote_text: string;
  anchor: { para_index: number; quote_text: string; occurrence_k: number };
}

export function useEvidence(pid: string | null, evidenceId: string | null) {
  return useQuery({
    queryKey: q(["evidence", pid, evidenceId]),
    queryFn: () => api.get<EvidenceView>(proj(pid!, `/evidence/${evidenceId}`)),
    enabled: !!pid && !!evidenceId,
    staleTime: Infinity, // 证据是不可变的（锚在冻结快照），拉一次就够
  });
}

export function useSubgraph(
  pid: string | null,
  center: string | null,
  chapter: number,
  hops: number,
) {
  return useQuery({
    queryKey: q(["subgraph", pid, center, chapter, hops]),
    queryFn: () =>
      api.get<Subgraph>(proj(pid!, `/subgraph?center=${encodeURIComponent(center!)}&chapter=${chapter}&hops=${hops}`)),
    enabled: !!pid && !!center,
  });
}

export function useScenes(pid: string | null, chapter: number) {
  return useQuery({
    queryKey: q(["scenes", pid, chapter]),
    queryFn: () => api.get<Scene[]>(proj(pid!, `/chapters/${chapter}/scenes`)),
    enabled: !!pid,
  });
}

export function useWriteScene(pid: string, chapter: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { number: number; cast: string[]; loc: string | null; goal: string | null }) =>
      api.put<Scene[]>(proj(pid, `/chapters/${chapter}/scenes`), body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scenes", pid, chapter] });
      invalidatePanels(qc, pid); // cast/loc 变 → 矩阵/约束/state 跟着变
    },
  });
}

/** 称呼 → 候选节点。选区查图谱用：唯一则直接 focus，歧义弹候选让作者挑。 */
export function useResolve(pid: string) {
  return useMutation({
    mutationFn: (surface: string) =>
      api.get<ResolveResult>(proj(pid, `/resolve?surface=${encodeURIComponent(surface)}`)),
  });
}
