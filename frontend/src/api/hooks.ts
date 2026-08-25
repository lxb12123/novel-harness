import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, proj } from "./client";
import { runTurnStream } from "./turnStream";
import type {
  ActivityDetail,
  ActivityPage,
  AiSettings,
  AiSettingsInput,
  ModelWindowsRefresh,
  BootstrapRequest,
  BootstrapResult,
  BookSummaryStatus,
  CanonEdgeEditRequest,
  CanonEdgeEditResult,
  CanonEdgeView,
  ChapterRow,
  ChapterDrafts,
  ChapterSnapshot,
  ChapterSummaryMentions,
  ChapterSummaryStatus,
  ChapterText,
  ChatDeleted,
  ChatDetail,
  ChatSessionView,
  ChatStopped,
  ChatTurnEvent,
  CheckResult,
  DeclareAlias,
  DeclareNode,
  DraftCandidateDetail,
  EventCastCorrection,
  EventCastInput,
  EventView,
  ExtractionRun,
  Mentioned,
  DeleteNodeInput,
  NodeDeleted,
  NodeRef,
  RenameNodeInput,
  RosterEntry,
  NodeSummaryMentions,
  ProposalAction,
  ProposalEditInput,
  ProposalRecord,
  ProposalResolution,
  ProvisionalConfirmation,
  Project,
  RecordedRules,
  RunsPanel,
  SceneConstraints,
  StateSnapshot,
  StoredAlias,
  Subgraph,
  SummaryWindow,
  SystemNotification,
  ValidationRuleView,
  CharacterBasicInfo,
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

// ⚠️ **`useDraft` 2026-08-14 删了。** 它从 2026-08-10「AI 起草」抽屉被删起就零调用方，
// 当时留着的理由写的是「模式二接面板时直接用」——**那个理由已经过期**：模式二的起草
// 是后端一次工具调用（`agent/drafting.py` → `importer.save_chapter`），浏览器不发这条
// 请求。后端 `POST …/draft` 一个字没动，要用重写一个 hook 是十行的事。
//
// 同一批删掉的还有 `useCreateProject` / `useImportBook`（建书和导书都走
// `POST /api/projects/bootstrap` 那一条了）和整个 `summaryPrep.ts`（它自己写着
// 「如果模式二最终没用它，删掉整个文件」，而模式二起草读现有总结、不补缺的）。
//
// **零调用方的东西放着不管，就是下一个「文档说有其实没有」**——那句话是
// `summaryPrep.ts` 自己写的，这次照它执行。

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

/** 全书总结状态视图（`GET …/summary-status`，2026-08-18 文档 §6 / Step 4）。
 *
 *  这份只跟书走、不跟章走（没有 chapter 参数）：它回答的是「全书哪些章有/缺/
 *  不对齐/异常」，以及这一轮自治调度会按什么权重补。**不只读、还是自治轮的可见
 *  反馈**——作者看完点某个芯片就跳去那一章。 */
export function useBookSummaryStatus(pid: string | null) {
  return useQuery({
    queryKey: q(["bookSummaryStatus", pid]),
    queryFn: () => api.get<BookSummaryStatus>(proj(pid!, `/summary-status`)),
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
 *  「这一章的总结已撤回」和「前面 N 章里有 M 段总结（含这一章）」两句互相打架的话。
 *
 *  **倒排那两条也一起**（T6）：总结里的字一变，「这一段提到了谁」和「还有哪几章提到他」
 *  两个答案都变了。漏掉它们的话，作者刚把「萧决」改成「魔尊」，下面那排芯片还挂着萧决——
 *  一块看起来完全正常、内容已经过期的屏幕。**反查那条按 pid 整片失效**：改一章总结会
 *  同时影响别的章的反查结果（那一章从某个人的名单里进来或出去），按 node_id 精确失效
 *  等于要在前端算一遍后端刚算完的差集。 */
function invalidateSummaries(qc: ReturnType<typeof useQueryClient>, pid: string) {
  qc.invalidateQueries({ queryKey: ["summary", pid] });
  qc.invalidateQueries({ queryKey: ["summaries", pid] });
  qc.invalidateQueries({ queryKey: ["summaryMentions", pid] });
  qc.invalidateQueries({ queryKey: ["nodeSummaryMentions", pid] });
  // 全书视图随着任何一章的总结生/改/撤而变：一起失效，别留一份过期全貌。
  qc.invalidateQueries({ queryKey: ["bookSummaryStatus", pid] });
}

/** 这一章的总结提到了花名册里的哪些东西（T6）。
 *
 *  **和 `useChapterSummary` 分成两条**，不是并进那一份出参：那个形状是四条动作路由
 *  共用的，而它同时也是 `…/summaries` 窗口里的一行——每一章都挂一串芯片会让一次
 *  覆盖率查询变成一次全书反查。 */
export function useSummaryMentions(pid: string | null, chapter: number) {
  return useQuery({
    queryKey: q(["summaryMentions", pid, chapter]),
    queryFn: () =>
      api.get<ChapterSummaryMentions>(proj(pid!, `/chapters/${chapter}/summary/mentions`)),
    enabled: !!pid,
  });
}

/** 还有哪几章的总结提到它（T6）。**不调模型、不花钱**，所以点着玩没有代价。
 *
 *  `nodeId` 为空 = 作者还没点任何一个芯片，这条不发。 */
export function useNodeSummaryMentions(pid: string | null, nodeId: string | null) {
  return useQuery({
    queryKey: q(["nodeSummaryMentions", pid, nodeId]),
    queryFn: () =>
      api.get<NodeSummaryMentions>(proj(pid!, `/nodes/${encodeURIComponent(nodeId!)}/summary-mentions`)),
    enabled: !!pid && !!nodeId,
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

export function useProjects() {
  return useQuery({ queryKey: q(["projects"]), queryFn: () => api.get<Project[]>("/api/projects") });
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

// ⚠️ **`useSyncManuscript` 2026-08-15 删了**，跟着「读回改动」那颗按钮一起。
// 后端 `POST …/sync` 照旧在（`nh sync` 用的是同一套 `importer.sync`），只是浏览器里
// 没有调用方了——把库和磁盘对齐现在走 `POST …/reconcile`（`frontend/src/reconcile.ts`）：
// 开书深对一次、之后每次回到标签页只 stat，722 章实测 ~4ms 且零读盘。

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
    queryFn: () => api.get<RosterEntry[]>(proj(pid!, "/roster")),
    enabled: !!pid,
  });
}

/** 改花名册里那一条的显示名。canonical 别名在后端同一个事务里跟着改。
 *
 *  `expected_canon_version` 由调用方从**它正在渲染的那份花名册**上取（项目出参里的
 *  `canon_version`）：作者拿着一份旧列表点改名时收到的是 409，不是「改掉了一个他
 *  没看见的、刚被抽取动过的东西」。同别名那几条的做法。 */
export function useRenameNode(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name, expected_canon_version }: RenameNodeInput) =>
      api.patch<NodeRef>(proj(pid, `/nodes/${encodeURIComponent(id)}`), {
        name,
        expected_canon_version,
      }),
    onSuccess: () => {
      invalidatePanels(qc, pid);
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

/** 删掉花名册里的一条。**有关系或情节引着它 → 409 + 挡路的条数。**
 *
 *  它是「抽取自动建人物」的配套（ADR 0020 补记）：模型认错一个，作者得有办法清掉，
 *  否则那个错永远往上下文里塞噪声。**这一步不可逆**，调用方要先问一句。
 *
 *  版本走**查询参数**不走请求体：带 body 的 DELETE 在各家客户端/代理上支持得参差不齐。 */
export function useDeleteNode(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, expected_canon_version }: DeleteNodeInput) =>
      api.del<NodeDeleted>(
        proj(pid, `/nodes/${encodeURIComponent(id)}`) +
          `?expected_canon_version=${expected_canon_version}`,
      ),
    onSuccess: () => {
      invalidatePanels(qc, pid);
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
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
    // **切回来重取**（`main.tsx` 把它全局关了，这里和 `useDrafts` 是仅有的两个例外）。
    //
    // 作者的日常回路是「工作台标签页一直开着 → 切到 WPS 改 → 切回来」，而这中间
    // **页面一次都没有重新加载过**。不重取，编辑器里就一直是他切走之前那份，
    // 且屏幕上没有任何东西说它旧了——他会对着旧正文接着写，然后按保存**盖掉
    // 自己刚在 WPS 里写的东西**。这是「看起来正常的假页面」里最贵的一种。
    //
    // 它安全，是因为落点在 `editorDoc.diskChange`：没有未存的改动就静默采纳，
    // 有才拦一句「这一章在别处变过了」。**这两档都是对的，不能只留一半。**
    refetchOnWindowFocus: true,
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

/** 在书末尾新起一章（空的）。**不带任何入参**——章号和章标题都由后端按磁盘定。
 *
 *  没有「在第 N 章后面插一章」这一档：那会把它后面每一章的号都推一位，而章号是
 *  全书的顺序键（证据、事件、快照全挂在它上面）。要插叙就在末尾新起一章再改标题。 */
export function useCreateChapter(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ChapterRow>(proj(pid, "/chapters")),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chapters", pid] }),
  });
}

/** 删掉一整章。**后端会拒绝「引擎已经在它上面记过东西」的那种**（409，带明细）。
 *
 *  正文不是删掉是挪走（书文件夹底下的 `deleted/`），所以这条路按错了还找得回来——
 *  但那件事**不在屏幕上说**：作者不看路径，而一句「已经挪到某某文件夹」会把一颗
 *  「删掉」按钮讲成一次搬家。
 *
 *  连章目录带正文一起失效：**删的那一章可能正开着**，只失效目录的话，
 *  中栏会继续摆着一份磁盘上已经不存在的正文，而作者还能往里打字。 */
export function useDeleteChapter(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chapter: number) => api.del(proj(pid, `/chapters/${chapter}`)),
    onSuccess: (_data, chapter) => {
      qc.invalidateQueries({ queryKey: ["chapters", pid] });
      qc.invalidateQueries({ queryKey: ["text", pid, chapter] });
    },
  });
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
 *  **`cast` 也不带**——不知道谁在场就走全禁（D4）；这是不给作者设门槛，不是偷懒。
 *
 *  光标**前后**两截都送（`following_text` 是后面那截已经写好的正文）。**送不等于用**：
 *  后端只在「这一章不是全书最后一章」时才把它渲染成【下文】——那个判断要知道全书
 *  写到第几章，前端不知道。 */
export function useContinuation(pid: string, chapter: number) {
  return useMutation({
    mutationFn: (around: { before: string; after: string }) =>
      api.post<{ text: string }>(proj(pid, `/chapters/${chapter}/draft`), {
        mode: "continuation",
        previous_tail: around.before,
        following_text: around.after,
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

// ⚠️ **`useLocate` / `useDeclare` 2026-08-14 删了**（连同它们唯一的调用方
// `DeclareDrawer` 和中栏那条选区工具条）。**后端 `POST …/locate`、`POST …/declare/*`
// 一个字都没动**：`nh locate` 和 `nh declare` 的七条子命令还在用同一套 `Ledger`，
// 而它们是这套图谱唯一的手工写入口。哪天要把「手工记一条」重新接进界面，
// 端点、拒绝措辞、`QuoteNotFound` 那段话都还在原地等着。

/** 建一个节点（人物 / 地点 / 势力 …）。幂等：键是 name，重复提交同一个名字不会建出两个。
 *
 * 这条路径是**整个工作台的起点**：import 只切章、不抽实体（ADR 0004 有意的），
 * 所以在花名册里有第一个人之前，约束 / declare 两样全都无从算起
 * （declare 的「填称呼」会必然 UnknownName 404）。成功后走 invalidatePanels——
 * 花名册从空变非空的那一刻，那两样才第一次有得算。
 */
export function useCreateNode(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    // 出参过 `_narrow`：未来节点收窄成 {id,label,name}，其余回完整 Node（带 props）。
    // 所以按 NodeRef + 可选 props 收（同 SubgraphNode 的处理）——UI 只读 label/name。
    mutationFn: (body: DeclareNode) =>
      api.post<NodeRef & { props?: unknown }>(proj(pid, "/nodes"), body),
    onSuccess: () => invalidatePanels(qc, pid),
  });
}

/** 一个人物的本名 + 全部 ACTIVE 别名（canonical 不算 chip，Task 11 API）。 */
export function useCharacterProfile(pid: string | null, characterId: string | null) {
  return useQuery({
    queryKey: q(["character-profile", pid, characterId]),
    queryFn: () =>
      api.get<CharacterBasicInfo>(
        proj(pid!, `/characters/${encodeURIComponent(characterId!)}/profile`),
      ),
    enabled: !!pid && !!characterId,
  });
}

/** 给一个人物加别名（canonical 拒；撞 expected_canon_version 409）。 */
export function useAddCharacterAlias(pid: string, characterId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { surface: string; expected_canon_version: number }) =>
      api.post<StoredAlias>(
        proj(pid, `/characters/${encodeURIComponent(characterId)}/aliases`),
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["character-profile", pid, characterId] });
    },
  });
}

/** 改一条别名（机器 alias → author 派生行）。 */
export function useEditAlias(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      aliasId,
      ...body
    }: { aliasId: string; surface?: string; expected_canon_version: number }) =>
      api.patch<StoredAlias>(proj(pid, `/aliases/${encodeURIComponent(aliasId)}`), body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["character-profile", pid] });
      qc.invalidateQueries({ queryKey: ["roster", pid] });
    },
  });
}

/** 撤回一条别名。 */
export function useRetractAlias(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (aliasId: string) =>
      api.del<{ id: string; status: string }>(proj(pid, `/aliases/${encodeURIComponent(aliasId)}`)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["character-profile", pid] });
      qc.invalidateQueries({ queryKey: ["roster", pid] });
    },
  });
}

/** 改归属：撤回 + 在目标 Character 上新建（只能移到 Character）。 */
export function useReassignAlias(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      aliasId,
      ...body
    }: { aliasId: string; to_character_id: string; expected_canon_version: number }) =>
      api.post<StoredAlias>(
        proj(pid, `/aliases/${encodeURIComponent(aliasId)}/reassign`),
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["character-profile", pid] });
      qc.invalidateQueries({ queryKey: ["roster", pid] });
    },
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

// ⚠️ **`useScenes` / `useWriteScene` 2026-08-14 删了**，连同它们背后的两条路由、
// 场景条、底栏那一列和 R4（[ADR 0027](docs/adr/0027-scene-blocks-cut.md)）。
// 场景块是一套要作者在正文里手写的标记语法（`## 场景 N` + `<!-- nh: cast=… -->`），
// 而「这一场谁在」ADR 0018 起就是从正文数出来的（`useMentioned`）。

// ⚠️ **`useResolve` 2026-08-14 删了**（同上：唯一调用方是中栏那条选区工具条的
// 「查看相关内容」）。后端 `GET …/resolve` 照旧在，左栏花名册那条路也照旧走它的
// 后端实现（`store.resolve`）——删的只是「选一句话去查图谱」这一个入口。

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

/** 显式后台抽取：POST 后立刻拿回 PENDING run，再用 useExtractionRun 轮询。
 *
 *  **`force` 那一档是「把没跑成的那一次再跑一遍」**：后端见到已经失败的同一条 run
 *  会把它**原地重置**回排队（不删行、不新建行），所以日志上那一行不会变成两行。
 *  不带 `force` 的话它原样还回那条失败的 run —— 接口 202、屏幕上什么都不会发生。
 *
 *  时间线和底栏那份用量跟着变：重跑会改写同一条 `extraction_run`，跑完还会多一次
 *  `model_call`。不失效它们，日志页上那一行会一直红着，而作者刚刚才按过按钮。 */
export function useStartExtraction(pid: string, chapter: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (opts?: { force?: boolean }) =>
      api.post<ExtractionRun>(
        proj(pid, `/chapters/${chapter}/extract${opts?.force ? "?force=true" : ""}`),
      ),
    onSuccess: (run) => {
      qc.setQueryData(["extraction", pid, run.id], run);
      qc.invalidateQueries({ queryKey: ["activity", pid] });
      qc.invalidateQueries({ queryKey: ["activity-detail", pid] });
      qc.invalidateQueries({ queryKey: ["runs", pid] });
    },
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
// Canon 边纠错（Task 8 / ADR 0032）：自动升上去的地点/状态/关系边
// ══════════════════════════════════════════════════════════════════════════
//
// 同「改一条已经生效的事实」那两条：**不做静默重试**。409 是「这本书在别处刚被
// 改过 / 这条边已经变了」，重试等于把作者的改动画到一份他没看过的状态上；
// 界面该做的是刷新当前事实、保留表单让作者看过后再提交。

const edgePath = (pid: string, edgeId: string) =>
  proj(pid, `/canon/edges/${encodeURIComponent(edgeId)}`);

/** 一条可纠错 Canon 边（`GET …/canon/edges/{id}`）。null = 还没有要打开的边。 */
export function useCanonEdge(pid: string | null, edgeId: string | null) {
  return useQuery({
    queryKey: q(["canon-edge", pid, edgeId]),
    queryFn: () => api.get<CanonEdgeView>(edgePath(pid!, edgeId!)),
    enabled: !!pid && !!edgeId,
    retry: false,
  });
}

/** 修改 / 改归属（PATCH）。**`expected_canon_version` 从正在渲染的那份 view 上取。** */
export function useEditCanonEdge(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ edgeId, ...body }: CanonEdgeEditRequest & { edgeId: string }) =>
      api.patch<CanonEdgeEditResult>(edgePath(pid, edgeId), body),
    onSuccess: (result) => {
      // 旧 edge ID 在 identity 改变后会返回 replacement——把旧 ID 那条缓存清掉，
      // 下次按回执里的新 ID 重新读。
      qc.invalidateQueries({ queryKey: ["canon-edge", pid] });
      invalidateReview(qc, pid);
      qc.invalidateQueries({ queryKey: ["projects"] }); // canon 版本推高了一格
      qc.invalidateQueries({ queryKey: ["activity", pid] });
      qc.invalidateQueries({ queryKey: ["canon-version", pid] });
      return result;
    },
  });
}

/** 软撤回（DELETE）。 */
export function useRetractCanonEdge(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ edgeId }: { edgeId: string }) =>
      api.del<CanonEdgeEditResult>(edgePath(pid, edgeId)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["canon-edge", pid] });
      invalidateReview(qc, pid);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["activity", pid] });
      qc.invalidateQueries({ queryKey: ["canon-version", pid] });
    },
  });
}

// ══════════════════════════════════════════════════════════════════════════
// 写作助手（模式二，ADR 0019）—— 开 / 列 / 看 / 删 / 跑一轮 / 停 / 规矩两条
// ══════════════════════════════════════════════════════════════════════════

const chats = (pid: string, tail = "") => proj(pid, `/chats${tail}`);
const one = (id: string) => `/${encodeURIComponent(id)}`;

/** 右栏「系统通知 N」那一格（Task 10）。只有 OPEN 会显示在默认列表里。 */
export function useNotifications(pid: string | null) {
  return useQuery({
    queryKey: q(["notifications", pid]),
    queryFn: () => api.get<SystemNotification[]>(proj(pid!, "/notifications")),
    enabled: !!pid,
  });
}

/** 数字 badge：只数 OPEN。**null 是「还没读」，不是 0。** */
export function useNotificationsCount(pid: string | null) {
  return useQuery({
    queryKey: q(["notifications-count", pid]),
    queryFn: () => api.get<{ open: number }>(proj(pid!, "/notifications/count")),
    enabled: !!pid,
  });
}

/** 忽略当前 hash 对（正文或总结任一变化都允许再次提醒，后端去重键钉着）。 */
export function useIgnoreNotification(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (notificationId: string) =>
      api.post<{ id: string; status: string }>(
        proj(pid, `/notifications/${encodeURIComponent(notificationId)}/ignore`),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications", pid] });
      qc.invalidateQueries({ queryKey: ["notifications-count", pid] });
    },
  });
}


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

// ⚠️ **`useChatRules` / `useRevokeRule` 2026-08-14 删了**，连同「这一章的规矩」那颗按钮
// 和那块面板（理由在 `ChatPanel.tsx` 顶上那段：有效期该由模型按情境判，而不是按章号算，
// 更不该摆出来让作者管）。
//
// **后端那两条路由（`GET`/`DELETE …/chats/{cid}/rules[/{seq}]`）同日一起撤了**，
// 所以这儿没有留下一对零调用方的端点——这个仓库为「有端点、没人调」栽过一次
// （ARCHITECTURE「工作台的已知洞」第 4 条：滚动总结一直在花作者的钱，而他看不见）。
//
// 作者今天怎么让一条规矩失效？**跟助手说一句。** 规矩带着「作者写第几章时说的；
// 情境不在了就不必守」进 prompt，作不作数由模型判（[ADR 0028]）。

/** 作者交代过的每一条规矩（`GET /projects/{pid}/rules`）。**记录，不是控件。**
 *
 *  **不轮询**：它只在跑完一轮之后可能变，而日志页本来就不常驻在屏幕上。 */
export function useRecordedRules(pid: string | null) {
  return useQuery({
    queryKey: q(["recordedRules", pid]),
    queryFn: () => api.get<RecordedRules>(proj(pid!, "/rules")),
    enabled: !!pid,
  });
}

/** 规则目录元数据（023 / Task 13）：R2/R3 常驻 + 作者自定义规则。 */
export function useValidationRules(pid: string | null) {
  return useQuery({
    queryKey: q(["validation-rules", pid]),
    queryFn: () => api.get<ValidationRuleView[]>(proj(pid!, "/validation-rules")),
    enabled: !!pid,
  });
}

/** 添加一条确定性命中规则（`forbidden_literal`，不进代码 / 正则 / 语义）。 */
export function useAddValidationRule(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title?: string; literal: string; blocks_downstream?: boolean }) =>
      api.post<ValidationRuleView>(proj(pid, "/validation-rules"), body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["validation-rules", pid] });
    },
  });
}

/** 启停 / 改字 / 改阻断。语义变化 = 后端同事务 epoch+1 + 重算 hash。 */
export function useUpdateValidationRule(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      ruleId,
      ...body
    }: { ruleId: string; enabled?: boolean; blocks_downstream?: boolean; literal?: string }) =>
      api.patch<{ rule_id: string; updated: boolean }>(
        proj(pid, `/validation-rules/${encodeURIComponent(ruleId)}`),
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["validation-rules", pid] });
    },
  });
}

/** 删除一条作者规则。 */
export function useDeleteValidationRule(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: string) =>
      api.del<{ rule_id: string; deleted: string }>(
        proj(pid, `/validation-rules/${encodeURIComponent(ruleId)}`),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["validation-rules", pid] });
    },
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
    // **开着「切回来重取」的两条查询之一**（另一条是 `useChapterText`；`main.tsx`
    // 那一行把它全局关了）。
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
