import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, proj } from "./client";
import type {
  ActivityDetail,
  ActivityPage,
  AiSettings,
  AiSettingsInput,
  AutopilotAck,
  AutopilotStatus,
  BootstrapRequest,
  BootstrapResult,
  ChapterRow,
  ChapterSnapshot,
  ChapterSummaryStatus,
  ChapterText,
  CheckResult,
  Declaration,
  DeclareAlias,
  DeclareBelieves,
  DeclareKnows,
  DeclareNode,
  DeclareWhere,
  DraftRequest,
  DraftResult,
  EventCastCorrection,
  EventCastInput,
  EventView,
  ExtractionRun,
  ImportReport,
  KnowledgeCorrection,
  KnowledgeEditInput,
  KnowledgeMatrix,
  Mentioned,
  NodeRef,
  ProposalAction,
  ProposalRecord,
  ProposalResolution,
  ProvisionalConfirmation,
  Project,
  QuoteCandidate,
  ResolveResult,
  RunsPanel,
  Scene,
  SceneConstraints,
  StateSnapshot,
  StoredAlias,
  Subgraph,
  SummaryWindow,
} from "./types";

// 服务端状态全进 TanStack Query（§2.3）：queryKey = [端点, pid, chapter, cast]，
// 坐标一变自动重取。写路径（save / declare）成功后**精确** invalidate 受影响的 key。

const q = (parts: unknown[]) => parts;

export function useAiSettings() {
  return useQuery({
    queryKey: q(["settings"]),
    queryFn: () => api.get<AiSettings>("/api/settings"),
  });
}

export function useSaveAiSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: AiSettingsInput) =>
      api.put<AiSettings>("/api/settings", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
}

/** AI 起草（实验状态，修正案 7）：POST /draft。 */
/** ⚠️ **2026-08-10 起零调用方**（同 `summaryPrep.ts`）：「AI 起草」抽屉当天删了。
 *  留着是因为**后端 `/draft` 一个字没动**——它现在是模式二 agent 的起草工具，
 *  接面板时直接用。同理下面的 `useSummaryWindow` / `useGenerateSummary` /
 *  `fetchAutopilotStatus`（`useRunAutopilot` 仍在用：换章后台整理走它）。 */
export function useDraft(pid: string, chapter: number) {
  return useMutation({
    mutationFn: (input: DraftRequest) =>
      api.post<DraftResult>(proj(pid, `/chapters/${chapter}/draft`), input),
  });
}

/** 起草第 `chapter` 章时，滚动总结那一层覆盖了哪些章、缺哪些。
 *
 *  **这条读端存在的理由是「不许静默」**：`chapter_summary` 表空着的时候，写作 prompt 里
 *  【更早章节滚动总结】渲染成「- 暂无」，而作者在界面上看不到任何东西告诉他那是
 *  「还没生成」而不是「本来就没有」。 */
export function useSummaryWindow(pid: string | null, chapter: number) {
  return useQuery({
    queryKey: q(["summaries", pid, chapter]),
    queryFn: () => api.get<SummaryWindow>(proj(pid!, `/chapters/${chapter}/summaries`)),
    enabled: !!pid,
  });
}

/** 为某一章生成滚动总结。**会调模型、会花钱，所以只由作者显式触发**——
 *  没有「保存章节后自动生成」那条路（那是一次他没按过的付费调用）。
 *  后端幂等，所以补一批的时候不必自己记住哪些补过。 */
export function useGenerateSummary(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chapter: number) =>
      api.post<ChapterSummaryStatus>(proj(pid, `/chapters/${chapter}/summary`)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["summaries", pid] }),
  });
}

// ── 后台整理（作者一离开某一章就交给后端做的那些活）─────────────────────────
//
// ⚠️ **这两条端点由后端另一条线落地中，今天线上可能还是 404。**
// 所以调用方必须把失败当无事发生：POST 那条永远不许打扰作者，GET 那条问不到就当
// 「不知道」，退回当场自己跑一遍（`summaryPrep.ts`）。等后端落地后，
// `frontend/src/test/harness.tsx` 里那两条手写 stub 要换成真 fixture。

/** 把「刚写完的那一章」交给后台整理（生成总结 / 抽取事件）。**不阻塞、不提示。** */
export function useRunAutopilot(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chapter: number) =>
      api.post<AutopilotAck>(proj(pid, `/chapters/${chapter}/autopilot`)),
    // 后台补完一章总结，起草前那次「缺不缺」的检查就该看到新结果。
    onSuccess: () => qc.invalidateQueries({ queryKey: ["summaries", pid] }),
  });
}

/** 问一句：这一章的后台整理跑完了吗、正在跑吗。
 *
 *  **命令式，不是 useQuery**：它只在作者按下「起草」的那一刻被问到，而且要在一个
 *  按章循环里逐章问——那种形状套不进 hook 的渲染期缓存。 */
export function fetchAutopilotStatus(pid: string, chapter: number): Promise<AutopilotStatus> {
  return api.get<AutopilotStatus>(proj(pid, `/chapters/${chapter}/autopilot`));
}

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

export function useBootstrapProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BootstrapRequest) =>
      api.post<BootstrapResult>("/api/projects/bootstrap", body),
    onSuccess: ({ project }) => {
      const pid = project?.id;
      qc.invalidateQueries({ queryKey: ["projects"] });
      if (pid) {
        qc.invalidateQueries({ queryKey: ["chapters", pid] });
        qc.invalidateQueries({ queryKey: ["roster", pid] });
      }
    },
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

/** 这一章正文里提到了花名册中的谁。**作者不填，引擎数。**
 *
 *  `cast` 为空时下面那三个读端在后端走的就是这一份，所以这里只是把同一个结果显示出来，
 *  不是第二条推导——两份推导会漂移，那正是这个仓库反复修的病。 */
export function useMentioned(pid: string | null, chapter: number) {
  return useQuery({
    queryKey: q(["mentioned", pid, chapter]),
    queryFn: () => api.get<Mentioned>(proj(pid!, `/chapters/${chapter}/mentioned`)),
    enabled: !!pid,
  });
}

/** 这三条读端的「谁在场」有两个参数，**方向相反，不许混用**：
 *
 *  · `cast` = 过滤（「只看这几个人」）。唯一来源是作者亲手标的场景块。
 *  · `include` = 只加不减（「这几个人也要算进来」）。日志页的跳转坐标走这条。
 *
 *  混用的后果只有一个方向：`must_not_reveal` 的判据是「在场的人里至少有一个还不知道」，
 *  系统给的坐标按定义只知道一个人，塞进 `cast` 就是替作者收窄，禁令跟着少一批
 *  （ADR 0018 §3 / `api/app.py::_effective_cast`）。 */
function castQuery(cast: string, include: string): string {
  return `cast=${encodeURIComponent(cast)}&include=${encodeURIComponent(include)}`;
}

export function useMatrix(pid: string | null, chapter: number, cast: string, include = "") {
  return useQuery({
    queryKey: q(["matrix", pid, chapter, cast, include]),
    queryFn: () =>
      api.get<KnowledgeMatrix>(
        proj(pid!, `/chapters/${chapter}/matrix?${castQuery(cast, include)}`),
      ),
    enabled: !!pid,
  });
}

export function useConstraints(pid: string | null, chapter: number, cast: string, include = "") {
  return useQuery({
    queryKey: q(["constraints", pid, chapter, cast, include]),
    queryFn: () =>
      api.get<SceneConstraints>(
        proj(pid!, `/chapters/${chapter}/constraints?${castQuery(cast, include)}`),
      ),
    enabled: !!pid,
  });
}

export function useStates(pid: string | null, chapter: number, cast: string, include = "") {
  return useQuery({
    queryKey: q(["state", pid, chapter, cast, include]),
    queryFn: () =>
      api.get<StateSnapshot[]>(
        proj(pid!, `/chapters/${chapter}/state?${castQuery(cast, include)}`),
      ),
    enabled: !!pid,
  });
}

/** 换章号会失效所有跟章号/cast 挂钩的读端。声明成功后调它。 */
function invalidatePanels(qc: ReturnType<typeof useQueryClient>, pid: string) {
  for (const k of ["matrix", "constraints", "state", "roster", "subgraph"]) {
    qc.invalidateQueries({ queryKey: [k, pid] });
  }
}

/** 撞上「别处刚改过」之后，把作者眼前这几张面板重读一遍。
 *
 *  **矩阵那一格的版本住在表自己身上**（`matrix.version.canon_version`，由 `/matrix`
 *  那条路由填）——不重取它，作者手上那个数一个字节都不会变：他再保存一次还是同一个
 *  409，点几次都出不去。而 `main.tsx` 写着 `refetchOnWindowFocus: false`，
 *  **没有任何东西会替他补这一次重读**。
 *
 *  **重取归编辑器自己管，不归挂载它的那一页管。** `MatrixView` 今天挂在两个地方
 *  （右栏第二格、章节核对页），此前重取靠调用方传 `onRefresh?.()`：右栏传了、核对页
 *  没传，于是同一颗「看看最新的」在两块屏幕上一颗管用、一颗只把编辑器收起来。
 *  一个可选 prop 决定退路死不死，那是迟早还会再漏一次的形状。
 */
export function useRefreshPanels(pid: string | null) {
  const qc = useQueryClient();
  return () => {
    if (!pid) return;
    invalidatePanels(qc, pid);
    // 顶栏和审阅面板读的是这一份版本；别处推高了它，那儿也该跟着更新。
    qc.invalidateQueries({ queryKey: ["projects"] });
  };
}

export function useSaveChapter(pid: string, chapter: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (markdown: string) => api.put(proj(pid, `/chapters/${chapter}/text`), { markdown }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chapters", pid] });
      qc.invalidateQueries({ queryKey: ["text", pid, chapter] });
      // 存一次就多一版（同内容除外），而「哪一版是当前」也跟着变——不失效它，
      // 版本列表会停在打开抽屉那一刻的样子。
      qc.invalidateQueries({ queryKey: ["history", pid, chapter] });
      invalidatePanels(qc, pid); // 正文变可能改动证据可定位性 → 面板重取
    },
  });
}

/** 还原到某一版：**就是把那一版的正文按普通保存写回去**，不是另一条写路径。
 *  快照按内容去重，写回去 sha 命中已有那条 → 当前指回它、版本列表不长第三条。
 *  所以这里没有 `/restore` 端点，只有一次 PUT。 */
export function useRestoreSnapshot(pid: string, chapter: number) {
  return useSaveChapter(pid, chapter);
}

/** 删掉一版历史。后端会拒绝两种：当前那一版、被证据/抽取/提案引着的那一版。 */
export function useDeleteSnapshot(pid: string, chapter: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (snapshotId: string) =>
      api.del(proj(pid, `/chapters/${chapter}/snapshots/${encodeURIComponent(snapshotId)}`)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["history", pid, chapter] }),
  });
}

/** 行内续写（ADR 0015）：同一条 `/draft`，只是 `mode` 不同。
 *
 *  **请求里没有 `goal`**——续写的提示语是后端常量（D3），前端能传的东西作者就能改，
 *  而 `goal` 是 ADR 0010 点名的泄漏入口之一。
 *  **`cast` 也不带**——不知道谁在场就走全禁（D4）；这是不给作者设门槛，不是偷懒。 */
export function useContinuation(pid: string, chapter: number) {
  return useMutation({
    mutationFn: (previousTail: string) =>
      api.post<{ text: string }>(proj(pid, `/chapters/${chapter}/draft`), {
        mode: "continuation",
        previous_tail: previousTail,
        length: CONTINUATION_LENGTH,
      }),
  });
}

/** 一两段的长度档。`LengthSpec` 的下限是 1，所以这是合法取值（ADR 0015 D1 / 0013）。
 *  M2 的 kill-gate 走冻结的 `M2_LENGTH_SPEC`，动不到考卷。 */
export const CONTINUATION_LENGTH = {
  language: "zh",
  min_units: 60,
  target_units: 140,
  max_units: 260,
} as const;

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

// ══════════════════════════════════════════════════════════════════════════
// M4：后台抽取 / 提案审阅 / 被动确认
// ══════════════════════════════════════════════════════════════════════════

export function useProposals(pid: string | null, chapter: number) {
  return useQuery({
    queryKey: q(["proposals", pid, chapter]),
    queryFn: () => api.get<ProposalRecord[]>(proj(pid!, `/chapters/${chapter}/proposals`)),
    enabled: !!pid,
  });
}

export function useEvents(pid: string | null, chapter: number, scope: "PROVISIONAL" | "CANON") {
  return useQuery({
    queryKey: q(["events", pid, chapter, scope]),
    queryFn: () => api.get<EventView[]>(proj(pid!, `/chapters/${chapter}/events?scope=${scope}`)),
    enabled: !!pid,
  });
}

/** 显式后台抽取：POST 后立刻拿回 PENDING run，再用 useExtractionRun 轮询。 */
export function useStartExtraction(pid: string, chapter: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (opts?: { force?: boolean }) =>
      api.post<ExtractionRun>(
        proj(pid, `/chapters/${chapter}/extract${opts?.force ? "?force=true" : ""}`),
      ),
    onSuccess: (run) => qc.setQueryData(["extraction", pid, run.id], run),
  });
}

export function useExtractionRun(pid: string | null, runId: string | null) {
  return useQuery({
    queryKey: q(["extraction", pid, runId]),
    queryFn: () => api.get<ExtractionRun>(proj(pid!, `/extractions/${runId}`)),
    enabled: !!pid && !!runId,
    refetchInterval: (query) =>
      query.state.data?.status === "PENDING" || query.state.data?.status === "RUNNING"
        ? 2000
        : false,
  });
}

/** 审阅一条提案：accept / reject / bystander。成功后刷新提案、事件、花名册与面板。 */
export function useReviewProposal(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      proposalId: string;
      action: ProposalAction;
      expected_canon_version: number;
    }) =>
      body.action === "accept"
        ? api.post<ProposalResolution>(proj(pid, `/proposals/${body.proposalId}/accept`), {
            expected_canon_version: body.expected_canon_version,
          })
        : api.post<ProposalResolution>(proj(pid, `/proposals/${body.proposalId}/reject`), {
            action: body.action,
            expected_canon_version: body.expected_canon_version,
          }),
    onSuccess: () => invalidateReview(qc, pid),
  });
}

/** 被动确认：把选中的 PROVISIONAL 事件/关系一次性落成 CANON（作者显式动作）。 */
export function useConfirmProvisional(pid: string, chapter: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      fact_kind: "event" | "edge";
      fact_ids: string[];
      expected_canon_version: number;
    }) =>
      api.post<ProvisionalConfirmation>(
        proj(pid, `/chapters/${chapter}/provisional/confirm`),
        body,
      ),
    onSuccess: () => invalidateReview(qc, pid),
  });
}

// ══════════════════════════════════════════════════════════════════════════
// 活动记录（ADR 0020 的「可查」）
// ══════════════════════════════════════════════════════════════════════════

/** 一页折叠行，新的在前。`actor` 传 null = 不过滤。
 *
 *  **游标是不透明串**（后端内部是 `ts|id`）：这里只把上一页的 `next_cursor` 原样
 *  回传，一个字节都不解析、不拼装。自己拼一份 = 前端多一份对后端排序键的假设。 */
export function useActivity(pid: string | null, actor: string | null) {
  return useInfiniteQuery({
    queryKey: q(["activity", pid, actor]),
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams();
      if (actor) params.set("actor", actor);
      if (pageParam) params.set("cursor", pageParam);
      const qs = params.toString();
      return api.get<ActivityPage>(proj(pid!, `/activity${qs ? `?${qs}` : ""}`));
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last: ActivityPage) => last.next_cursor,
    enabled: !!pid,
  });
}

/** 展开某一条：跑了什么、结果是什么、花了多少。
 *
 *  **按需取，不预取**：详情里带着 `decision_log` 的审计信封，跟着列表一起拉
 *  等于打开日志页就把全库审计内容搬进浏览器（后端把 payload 排除在折叠层之外
 *  就是这个理由，前端别在这一侧把它抵消掉）。 */
export function useActivityDetail(pid: string | null, entryId: string | null) {
  return useQuery({
    queryKey: q(["activity-detail", pid, entryId]),
    queryFn: () =>
      api.get<ActivityDetail>(proj(pid!, `/activity/${encodeURIComponent(entryId!)}`)),
    enabled: !!pid && !!entryId,
  });
}

/** 这本书到今天为止的整理次数与模型用量。 */
export function useRuns(pid: string | null) {
  return useQuery({
    queryKey: q(["runs", pid]),
    queryFn: () => api.get<RunsPanel>(proj(pid!, "/runs")),
    enabled: !!pid,
  });
}

function invalidateReview(qc: ReturnType<typeof useQueryClient>, pid: string) {
  qc.invalidateQueries({ queryKey: ["proposals", pid] });
  qc.invalidateQueries({ queryKey: ["events", pid] });
  qc.invalidateQueries({ queryKey: ["roster", pid] });
  qc.invalidateQueries({ queryKey: ["state", pid] });
  qc.invalidateQueries({ queryKey: ["matrix", pid] });
  // ★ 每一次成功的确认/改正都会把这本书的 canon 版本推高一格，而下一次动作要拿它当
  //   `expected_canon_version`。不失效它，界面上第二次点就必然撞 409——而那个 409 的
  //   意思是「别处刚改过，你看的是旧的」，这里却是我们自己缓存了一个旧数字。
  qc.invalidateQueries({ queryKey: ["projects"] });
}

// ══════════════════════════════════════════════════════════════════════════
// 改一条**已经生效**的事实（ADR 0020 的「可改」）
// ══════════════════════════════════════════════════════════════════════════
//
// 这两条是 ADR 0020 押的那条退路：抽取直接进 CANON，作者第一次看见那条事实时它已经
// 生效了，所以退路必须落在 CANON 上。**不许在这两条上做静默重试**：409 的意思是
// 「这本书在别处刚被改过」，重试等于把作者的改动盖到一份他没看过的状态上。

/** 「他知道 X」改成「他以为 X」，或者反过来。
 *
 *  `expected_canon_version` 由调用方从**它正在渲染的那张矩阵**上取
 *  （`matrix.version.canon_version`），不在这里另拉一次：版本必须跟着作者看到的数据走。
 *  **这不是新增认知的入口**——空格子（不知道）走声明抽屉，那条路要一句引语来定章号。 */
export function useCorrectKnowledge(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: KnowledgeEditInput) =>
      api.post<KnowledgeCorrection>(proj(pid, "/canon/knowledge"), body),
    onSuccess: () => {
      invalidatePanels(qc, pid);
      qc.invalidateQueries({ queryKey: ["projects"] }); // canon 版本推高了一格
      qc.invalidateQueries({ queryKey: ["activity", pid] }); // 这一步会进日志
    },
  });
}

/** 改一条已生效事件的知情 / 在场名单。
 *
 *  入参是**绝对集合**：传谁就是谁，`null` = 这一维不动（所以重复提交是幂等的）。
 *  调用方只发作者真动过的那一维——发一份没动过的名单进去，日志里就多一条
 *  「改了在场」而其实一个人都没变。 */
export function useCorrectEventCast(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    // `eventId` 进路径、不进 body：`EventCastEditRequest` 是 `extra="forbid"` 的。
    mutationFn: ({ eventId, ...body }: EventCastInput & { eventId: string }) =>
      api.post<EventCastCorrection>(
        proj(pid, `/canon/events/${encodeURIComponent(eventId)}/cast`),
        body,
      ),
    // `invalidateReview` 里的 `["events", pid]` 是前缀匹配，本章那一份跟着失效。
    onSuccess: () => {
      invalidateReview(qc, pid);
      qc.invalidateQueries({ queryKey: ["activity", pid] });
    },
  });
}
