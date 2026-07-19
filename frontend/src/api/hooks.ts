import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, proj } from "./client";
import type {
  ChapterRow,
  ChapterText,
  CheckResult,
  Declaration,
  DeclareBelieves,
  DeclareKnows,
  DeclareWhere,
  KnowledgeMatrix,
  Project,
  QuoteCandidate,
  SceneConstraints,
  StateSnapshot,
} from "./types";

// 服务端状态全进 TanStack Query（§2.3）：queryKey = [端点, pid, chapter, cast]，
// 坐标一变自动重取。写路径（save / declare）成功后**精确** invalidate 受影响的 key。

const q = (parts: unknown[]) => parts;

export function useProjects() {
  return useQuery({ queryKey: q(["projects"]), queryFn: () => api.get<Project[]>("/api/projects") });
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

export function useCheck(pid: string) {
  return useMutation({
    mutationFn: (chapter: number) => api.post<CheckResult>(proj(pid, `/chapters/${chapter}/check`)),
  });
}
