import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, proj } from "./client";
import { runTurnStream } from "./turnStream";
import type {
  ActivityDetail,
  ActivityPage,
  AiSettings,
  AiSettingsInput,
  ModelWindowsRefresh,
  AutopilotAck,
  AutopilotStatus,
  BootstrapRequest,
  BootstrapResult,
  ChapterRow,
  ChapterDrafts,
  ChapterSnapshot,
  ChapterSummaryStatus,
  ChapterText,
  ChatDeleted,
  ChatDetail,
  ChatRuleRevoked,
  ChatRules,
  ChatSessionView,
  ChatStopped,
  ChatTurnEvent,
  CheckResult,
  Declaration,
  DeclareAlias,
  DeclareBelieves,
  DeclareKnows,
  DeclareNode,
  DeclareWhere,
  DraftCandidateDetail,
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
  ProposalEditInput,
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
  SyncOutcome,
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

/** 更新那份「模型 → 上下文窗口」的公开表（`POST /api/settings/model-windows/refresh`）。
 *
 *  **有意不自动跑**：这份数据决定上文给作者 800 字还是 40,000 字，而它来自一个我们
 *  不控制的仓库。自动更新 = 别人改一行，作者明天的稿子上下文就变了，而他不知道为什么。
 *
 *  拉完要**让能力重新解析一次**：起草抽屉/写作助手拿到的窗口是后端算的，
 *  不动那些查询的话，作者点完按钮看到的还是旧上文长度。 */
export function useRefreshModelWindows() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<ModelWindowsRefresh>("/api/settings/model-windows/refresh", {}),
    onSuccess: () => qc.invalidateQueries(),
  });
}

/** AI 起草（实验状态，修正案 7）：POST /draft。 */
/** ⚠️ **2026-08-10 起零调用方**（同 `summaryPrep.ts`）：「AI 起草」抽屉当天删了。
 *  留着是因为**后端 `/draft` 一个字没动**——它现在是模式二 agent 的起草工具，
 *  接面板时直接用。同理 `fetchAutopilotStatus`（`useRunAutopilot` 仍在用：换章
 *  后台整理走它）。
 *
 *  **`useSummaryWindow` / `useGenerateSummary` 2026-08-13 接上了**（右栏「章节总结」
 *  那一格）：那两条在这儿零调用方地躺了三天，而它们背后的东西**一直在花作者的钱、
 *  一直在影响每一稿**——链路通着，断在最后一格。 */
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

/** 这一章现在的总结（`GET …/chapters/{n}/summary`，单章）。
 *
 *  **和 `useSummaryWindow` 是两件事，别拿一个去凑另一个。** 那一条回答「起草这一章时
 *  滚动总结那一层覆盖成什么样」（一整个区间），这一条回答「这一章自己有没有总结」。
 *  拿窗口端点传 `chapter + 1` 去凑出本章那一行，就是在前端算一次后端的偏移——
 *  而那种偏移改起来只会有一头跟着改。 */
export function useChapterSummary(pid: string | null, chapter: number) {
  return useQuery({
    queryKey: q(["summary", pid, chapter]),
    queryFn: () =>
      api.get<ChapterSummaryStatus>(proj(pid!, `/chapters/${chapter}/summary`)),
    enabled: !!pid,
  });
}

/** 改动这一章的总结之后，哪几处读端要重取。
 *
 *  **窗口那一份必须一起失效**：右栏那句「写这一章时带得上几段」读的是它，
 *  而作者刚撤掉的那一章正在里面算作「有」。不失效它，屏幕上会同时出现
 *  「这一章的总结已撤回」和「前面 N 章里有 M 段总结（含这一章）」两句互相打架的话。 */
function invalidateSummaries(qc: ReturnType<typeof useQueryClient>, pid: string) {
  qc.invalidateQueries({ queryKey: ["summary", pid] });
  qc.invalidateQueries({ queryKey: ["summaries", pid] });
}

/** 为某一章生成滚动总结。**会调模型、会花钱，所以只由作者显式触发**——
 *  没有「保存章节后自动生成」那条路（那是一次他没按过的付费调用）。
 *  后端幂等，所以补一批的时候不必自己记住哪些补过。
 *
 *  撤回过的章按这里会**真的重新生成**（付一次钱）——那是撤回语义里写死的退路。 */
export function useGenerateSummary(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chapter: number) =>
      api.post<ChapterSummaryStatus>(proj(pid, `/chapters/${chapter}/summary`)),
    onSuccess: () => {
      invalidateSummaries(qc, pid);
      // 这一次是花了钱的（`model_call` 多一行），日志页和底栏那份用量得跟着变。
      qc.invalidateQueries({ queryKey: ["activity", pid] });
      qc.invalidateQueries({ queryKey: ["runs", pid] });
    },
  });
}

/** 把这一章的总结换成作者自己写的那一段。**不花钱。**
 *
 *  库里是追加一行，模型写的那一行留着（迁移 013）——所以这里没有「撤销」这颗按钮，
 *  他随时可以再改回去。 */
export function useEditSummary(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { chapter: number; summary: string }) =>
      api.patch<ChapterSummaryStatus>(proj(pid, `/chapters/${v.chapter}/summary`), {
        summary: v.summary,
      }),
    onSuccess: () => invalidateSummaries(qc, pid),
  });
}

/** 撤回这一章的总结。**不花钱，库里也一行都不少。**
 *
 *  **不做乐观更新**：撤回在库里是追加一条指着它的记录，屏幕上却是「这一段没了」——
 *  两者之间的差别作者永远看不到。先把那一行抹掉的话，界面自己伪造了一次成功
 *  （同 `useRevokeRule` 那条道理）。 */
export function useRetractSummary(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chapter: number) =>
      api.del<ChapterSummaryStatus>(proj(pid, `/chapters/${chapter}/summary`)),
    onSuccess: () => invalidateSummaries(qc, pid),
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
    // 单章那一份也要（右栏「章节总结」读的是它）：作者刚离开的那一章，后台正是在
    // 给它生成总结，而他一翻回去看到的会是「还没生成」。
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["summaries", pid] });
      qc.invalidateQueries({ queryKey: ["summary", pid] });
    },
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

/** **把作者在别的软件里改过的稿子读回库。**
 *
 *  这条路由（`POST …/sync`）从 M1.5 建好起在浏览器里**零调用方**，而它是
 *  「正文看得见」和「这句话记得下」之间那半条回路：章列表和正文直接扫磁盘，所以
 *  作者在 WPS 里改完回来屏幕上立刻是新的；而 `locate` 搜的是**库里的快照**——
 *  不跑这一下，他刚写的那句话选中之后会被告知「找不到」，而他的选择没有任何问题。
 *
 *  **不花钱**（一次模型调用都没有），但仍然只由作者按一下：它往库里写快照，
 *  而「磁盘先、DB 跟」的那一下是作者的动作（ADR 0007），不是后台的。
 *  **也没有 file-watch**：一个自动跟着磁盘写库的后台线程是一条作者按不停的写路径，
 *  而它防的那件事一次点击就能补回来。
 *
 *  成功后失效的东西按「这一下真的改了什么」来挑：库里的快照变了 ⇒ 历史、面板；
 *  文件本身没被动过，但章列表可能多出新写的那一章 ⇒ 章目录。
 *  **正文（`text`）不失效**——那一份直接读磁盘，和这次同步无关，
 *  而作者手上可能有没保存的字（`CenterEditor` 的 `diskAhead` 那条）。 */
export function useSyncManuscript(pid: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SyncOutcome>(proj(pid!, "/sync"), {}),
    onSuccess: () => {
      if (!pid) return;
      qc.invalidateQueries({ queryKey: ["chapters", pid] });
      qc.invalidateQueries({ queryKey: ["history", pid] });
      invalidatePanels(qc, pid);
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

/** 审阅一条提案：accept / reject / bystander / **edit**。
 *
 *  三条路由，不是两条。**`edit` 那条在此之前没有调用方**——引擎和路由都通着，
 *  而这个 hook 是个二分支（`action === "accept" ? /accept : /reject`），于是
 *  「改一改再收下」在浏览器里到不了。缺的那一档正好是最需要的那一档：`knowers`
 *  是抽取里唯一靠推断得来的一维（谁在场是文本里写着的，谁**因此知道了**是猜的）。
 *
 *  成功后刷新提案、事件、花名册与面板（`edit` 同样把 canon 版本推高一格，所以它
 *  和 accept 走同一条失效）。 */
export function useReviewProposal(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      proposalId: string;
      action: ProposalAction;
      expected_canon_version: number;
      /** 只有 `action === "edit"` 才带；后端对别的动作带编辑字段是 422。 */
      edit?: ProposalEditInput;
    }) => {
      const path = (verb: string) => proj(pid, `/proposals/${body.proposalId}/${verb}`);
      if (body.action === "accept") {
        return api.post<ProposalResolution>(path("accept"), {
          expected_canon_version: body.expected_canon_version,
        });
      }
      if (body.action === "edit") {
        return api.post<ProposalResolution>(path("edit"), {
          ...body.edit,
          expected_canon_version: body.expected_canon_version,
        });
      }
      return api.post<ProposalResolution>(path("reject"), {
        action: body.action,
        expected_canon_version: body.expected_canon_version,
      });
    },
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

// ══════════════════════════════════════════════════════════════════════════
// 写作助手（模式二，ADR 0019）—— 开 / 列 / 看 / 删 / 跑一轮 / 停 / 规矩两条
// ══════════════════════════════════════════════════════════════════════════

const chats = (pid: string, tail = "") => proj(pid, `/chats${tail}`);
const one = (id: string) => `/${encodeURIComponent(id)}`;

/** 侧栏那一列。**不轮询**：`running` 是后端**进程内**的事实，只有另一个标签页
 *  正在跑同一本书时它才会自己变——而那时那边一停，这边下一次动作就会看到。
 *  为了那一档每隔几秒打一次库，换来的是电池和噪音。 */
export function useChats(pid: string | null) {
  return useQuery({
    queryKey: q(["chats", pid]),
    queryFn: () => api.get<ChatSessionView[]>(chats(pid!)),
    enabled: !!pid,
  });
}

/** 一段对话的全部（后端给的就是全部，没有分页端点）。
 *  **超长时的处置在前端**：`chat.ts::tailWindow` 只渲染尾巴，早先那些点一下才展开。 */
export function useChatDetail(pid: string | null, chatId: string | null) {
  return useQuery({
    queryKey: q(["chat", pid, chatId]),
    queryFn: () => api.get<ChatDetail>(chats(pid!, one(chatId!))),
    enabled: !!pid && !!chatId,
  });
}

export function useCreateChat(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    // 后端的入参模型每个字段都有默认值，但**请求体本身是必需的**：不发 body 是 422。
    mutationFn: () => api.post<ChatSessionView>(chats(pid), { title: "" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chats", pid] }),
  });
}

/** 删一段对话。**它不会动到任何一行正文**（正文在磁盘上，那两张表里没有它）。
 *  正在跑的那一段后端会先拒（409，带一句人话），这里不做静默重试。 */
export function useDeleteChat(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chatId: string) => api.del<ChatDeleted>(chats(pid, one(chatId))),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chats", pid] }),
  });
}

/** 跑一轮。`said` 留空 = 接着上次往下跑（resume，或者上一轮撞了闸之后继续）。
 *
 *  **`chapter` 是必填的，而且必须是作者此刻在写的那一章。** 后端故意没给它默认值：
 *  投影在 `chapter` 缺席时**不过滤**，而那不是安全默认值是「没接线」默认值——
 *  第 90 章的禁说清单是第 40 章那份的**子集**，留着它模型就以为只有那两条不能说
 *  （ADR 0019 边界五）。
 *
 *  ── 跑完之后哪些面板该失效重取 ────────────────────────────────────────────
 *
 *  · `chats` / `chat`：这一轮长出来的话要落到侧栏和正文旁边。
 *  · `activity` / `runs`：**每一次模型调用都记了账**（`api/chat.py::_ledger`），
 *    日志页和底栏那份用量得跟着变——不失效它们，作者刚花的钱在界面上要等下一次
 *    刷新才出现，而 `main.tsx` 写着 `refetchOnWindowFocus: false`。
 *
 *  · **正文那一侧**（`text` / `chapters` / `history` + 右栏那几格）：**助手可以把某一稿
 *    写进磁盘上那一章**（[ADR 0021](docs/adr/0021-agent-writes-drafts-without-asking.md)
 *    的精神 + [ADR 0022](docs/adr/0022-drafting-is-a-proposal-not-a-write.md) 的机制，
 *    走的是 `PUT /chapters/{n}/text` 那条**同一条**路径）。所以一轮跑完之后要失效的东西
 *    和作者自己按了保存之后是同一批——`useSaveChapter` 那张表照抄。
 *
 *    **「它到底写没写」现在回执上有了**（`receipt.drafts[].landed`，ADR 0022 把 3.5
 *    那条余债还上了），**但这一档仍然一律重取**：写没写只是判据之一，而**判错的代价
 *    不对称**——白取一次是一次本地请求，漏取一次是作者对着旧稿按保存、把助手写的
 *    整章盖掉。省这一次请求换一个「只在某个条件下才正确」的分支，不值。
 *
 *    ⚠️ **这一行的前提是 `CenterEditor` 不会拿重取到的正文盖掉作者没保存的字**
 *    （`editorDoc.ts`）。那个前提没有的时候补这一行 = 作者一边打字一边跟助手说话，
 *    刚打的半段被静默吃掉，**而没保存过的东西哪儿都找不回来**。两件事必须一起在。
 *
 *  ── 它走的是长连接，不是一次请求/响应（ADR 0024）─────────────────────────
 *
 *  `mutationFn` 里那次 `await` 从**开跑**一直挂到**跑完**，和以前一模一样——
 *  所以 `isPending` / `onSuccess` / `onError` 和上面那整段失效表一个字都没改。
 *  变的只有中间：`onEvent` 会被叫上几十到上千次（一稿正文是上千片）。
 *
 *  **`onEvent` 不进 react-query 的状态**：那些片一秒钟几十条，每条都走一次
 *  `setState` 会把这块屏幕拖垮。调用方自己攒（`ChatPanel` 用的是 reducer + ref）。 */
export function useRunTurn(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      chatId: string;
      chapter: number;
      said: string;
      runId: string;
      onEvent?: (event: ChatTurnEvent) => void;
    }) =>
      runTurnStream(
        chats(pid, `${one(v.chatId)}/turn/events`),
        {
          chapter: v.chapter,
          said: v.said,
          // 这一轮的标识。**「停」要报同一个**，否则它会停到别的轮上去
          // （`chat.ts::newRunId` 写着那个坏序列）。
          //
          // **长连接没有让它变得不必要**：这条流断掉之后那一轮还在跑，而「停」
          // 仍然是另一个请求（`api/chat.py` 模块 docstring 那张表下面写着为什么
          // 不把它搬到流上）。所以那个坏序列原样存在，这个标识原样要报。
          run_id: v.runId,
        },
        { onEvent: v.onEvent },
      ),
    onSuccess: (_receipt, v) => {
      qc.invalidateQueries({ queryKey: ["chats", pid] });
      qc.invalidateQueries({ queryKey: ["activity", pid] });
      qc.invalidateQueries({ queryKey: ["runs", pid] });
      qc.invalidateQueries({ queryKey: ["text", pid] });
      qc.invalidateQueries({ queryKey: ["chapters", pid] });
      qc.invalidateQueries({ queryKey: ["history", pid] });
      // 桌上摆着的那几稿（ADR 0022）：这一轮很可能又添了几份，而「这一章还摆着几稿」
      // 那个入口是作者关掉回执之后唯一找得回它们的地方。
      qc.invalidateQueries({ queryKey: ["drafts", pid] });
      // 这一轮很可能记下了一条新规矩（模型叫的 `remember_rule`，作者没按任何按钮）。
      // **不失效它，那条规矩要等下一次刷新才出现在清单上**——而 ADR 0023 押的退路
      // 只有一句「看得见 + 能取消」，一条看不见的新规矩正好落在它外面。
      qc.invalidateQueries({ queryKey: ["rules", pid] });
      invalidatePanels(qc, pid);
      // **把这一条 return 出去**：mutation 会等它重取完才算落地，于是「正在跑」那一段
      // 屏幕能一直挂到新消息真的到手。不等的话中间有一帧是
      // 「作者刚说的那句话不见了、助手的回话还没到」——一块空白的对话。
      return qc.invalidateQueries({ queryKey: ["chat", pid, v.chatId] });
    },
  });
}

/** 按下「停」。它**不等这一轮跑完**：信号交给正在跑的那一轮，那个请求会自己收尾。
 *
 *  **`stopped=false` 不是失败**，而且它有两种（本来就没在跑 / 在跑的是**另一轮**），
 *  所以这里没有 `onError` 分支要处理它们——两种都是 200，界面照着后端那句 `message` 说。
 *
 *  **`runId` 必须是发起那一轮时报的那个**：不报的话后端不比对，于是一次迟到的「停」
 *  会掐掉作者刚发出去的新一轮（`chat.ts::newRunId` 写着那个序列）。 */
export function useStopChat(pid: string) {
  return useMutation({
    mutationFn: (v: { chatId: string; runId: string }) =>
      api.post<ChatStopped>(chats(pid, `${one(v.chatId)}/stop`), { run_id: v.runId }),
  });
}

/**
 * 这一章现在生效的那几条规矩（[ADR 0023](docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二）。
 *
 * **`chapter` 必给。** 后端在没有章号时直接 422 而不是回一份空清单，理由是那份空清单
 * 长得和「这一章确实没有规矩」一模一样——**一句它不知道真假的话，而且看起来完全正常**。
 *
 * **不轮询**：规矩只有两种长出来的方式，一种是跑一轮（`useRunTurn` 跑完会失效它），
 * 一种是作者自己点掉一条（那条 mutation 也失效它）。别的时刻它不会自己变。
 */
export function useChatRules(pid: string | null, chatId: string | null, chapter: number | null) {
  return useQuery({
    queryKey: q(["rules", pid, chatId, chapter]),
    queryFn: () => api.get<ChatRules>(chats(pid!, `${one(chatId!)}/rules?chapter=${chapter}`)),
    enabled: !!pid && !!chatId && !!chapter,
  });
}

/**
 * 作者点掉一条规矩。
 *
 * ── **不做乐观更新**，而且这一条是这个按钮的全部价值所在 ──────────────────────
 *
 * 同一条规矩可能被记过好几遍，读端只摆出最后那一条；撤销在引擎侧按**身份**撤掉每一份。
 * 那件事出错的时候（只划掉作者点的那个下标），后端照样 200、回执照样 `revoked: true`
 * ——**唯一能看出来的地方就是重取回来它还在**。
 *
 * 所以这儿只失效、不预先把那一行从屏幕上抹掉：抹掉的话界面自己伪造了成功，
 * 而作者要到下一次打开这块面板才发现规矩还在（那时他早忘了自己点过）。
 */
export function useRevokeRule(pid: string, chatId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (seq: number) =>
      api.del<ChatRuleRevoked>(chats(pid, `${one(chatId!)}/rules/${seq}`)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules", pid] }),
  });
}

// ══════════════════════════════════════════════════════════════════════════
// 桌上摆着的那几稿（ADR 0022）—— 两条路由：列 / 摊开一版
// ══════════════════════════════════════════════════════════════════════════

/**
 * 助手写过、**还摆在桌上**的那几稿。最近的在前，**不带正文**。
 *
 * **它不是版本历史。** 版本历史（`useHistory`）里是**已经在书里**的那些；这儿是还没进书的
 * 候选——它们在磁盘上、在快照里都不存在，**没有这条路由，作者关掉那一轮的回执就
 * 再也找不到它们了**。所以「这一章还摆着几稿」那个入口读的就是它。
 */
export function useDrafts(pid: string | null, chapter: number | null) {
  return useQuery({
    queryKey: q(["drafts", pid, chapter]),
    queryFn: () =>
      api.get<ChapterDrafts>(proj(pid!, `/drafts${chapter ? `?chapter=${chapter}` : ""}`)),
    enabled: !!pid,
    // **全仓唯一一条开着「切回来重取」的查询**（`main.tsx` 那一行把它全局关了）。
    // 理由是这一条读端有一个别处没有的形态：**并排比那一页开在另一个标签页里**，
    // 而作者切走的那段时间里，工作台那边可能又写了几稿。切回来看见一份少了两稿的
    // 桌子，而屏幕上没有任何东西说它旧了——那正是这个仓库反复在修的「看起来正常的
    // 假页面」。代价是一次本地 SQLite 的列表查询（不带正文）。
    refetchOnWindowFocus: true,
  });
}

/**
 * 摊开某一版的全文。**`open` 为假时一个字节都不取**——列表那一档一次二十稿，
 * 而每一稿都是一整章正文（后端有意只在详情里给 `text`）。
 *
 * `staleTime: Infinity` 不是性能调优，是**候选按定义不可变**（ADR 0022：它既不进正文
 * 也不进对话，写完就不再变）。重取回来的必然一模一样，而在并排比那一页上
 * 三列同时重取 = 三章正文白走一趟。
 */
export function useDraftText(pid: string | null, draftId: string | null, open: boolean) {
  return useQuery({
    queryKey: q(["draft", pid, draftId]),
    queryFn: () => api.get<DraftCandidateDetail>(proj(pid!, `/drafts/${encodeURIComponent(draftId!)}`)),
    enabled: !!pid && !!draftId && open,
    staleTime: Infinity,
  });
}
