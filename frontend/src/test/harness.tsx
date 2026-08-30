import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import fixtures from "../__fixtures__/api.json";

// 组件测试的装配 —— **喂给组件的每一个字节都来自真后端**。
//
// `api.json` 不是手写的：它是 `tests/test_frontend_contract.py` 从真 app（TestClient +
// 真 SQLite）dump 出来、规范化掉 ULID/时间戳之后冻住的真响应，且那个 pytest 每次都会
// 重新 dump 一遍对比——后端出参一改它先红。
//
// 这条纪律是本目录存在的理由：**用手写 fixture 做前端测试等于两份手写的东西互相验证**，
// 而这条缝原本的病正是「前端按自己以为的形状写，后端悄悄改了，没人发现」。

export { fixtures };

// `body` 可以是一个函数：测试要观察「补总结的时候界面在说什么」时，得能把那次响应
// 卡在原地（`body: async () => { await gate; return …; }`），否则中间那一帧快到测不出来。
//
// `stream` 是长连接那条路（ADR 0024）：给一串**原始帧**，stub 把它们拼成一条
// `text/event-stream` 的 body。不给 `stream` 而只给 `body` 的时候，
// stub 会把那份 `body` 当成**最后那一帧回执**——绝大多数测试关心的正是它，
// 而中间那些帧的真实字节由 `fixtures.chatTurnEvents` 提供（真 dump，见下）。
type Handler = {
  method?: string;
  match: RegExp;
  status?: number;
  body?: unknown | (() => unknown | Promise<unknown>);
  /** 原始 SSE 帧（含 `event:` / `data:` / 结尾那个空行）。
   *
   *  **单个帧可以是一个 Promise**：要观察「跑到一半屏幕上是什么」就得能把流卡在
   *  某一帧之前（同上面 `body` 那条理由）——不卡住的话，中间那些帧快到测不出来，
   *  而这一刀的全部主张就在那几帧上。 */
  stream?: Frames | (() => Frames | Promise<Frames>);
};

type Frames = (string | Promise<string>)[];

/** 长连接那条路由。**这个形状写在一处**：stub 靠它决定要不要发一条流。 */
const EVENT_STREAM = /\/turn\/events$/;

/**
 * 把几条 `{帧名, 载荷}` 拼成原始帧。**这是这份文件里唯一手写 SSE 格式的地方**，
 * 而它对不对由 `fixtures.chatTurnEvents`（从真 app dump 的原始字节）钉着：
 * 那份夹具被同一个解码器吃过一遍，格式漂了它先红。
 */
export const sseFrames = (
  frames: { event: string; data: unknown }[],
): string[] => frames.map((f) => `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`);

/** 一轮跑完：中间那些帧是**真 dump 的字节**，最后一帧换成这个测试要的那份回执。 */
export const turnStream = (receipt: unknown, events = fixtures.chatTurnEvents): string[] => [
  ...events.filter((frame) => !frame.startsWith("event: receipt")),
  ...sseFrames([{ event: "receipt", data: receipt }]),
];

// 反查那一份**多加一章**：真 dump 只有一章（第 2 章那段刚被撤回，它冻的正是
// 「撤回过的章不在名单里」——这一层最贵的断言）。而「点开看还有哪几章」那条分支
// 一章验不出来，所以在真 dump 之上**派生**出第二章，不手写一个形状。
// `author_written` 反过来：「模型压的」那句免责只该贴在模型写的那一行上。
const SUMMARY_TRAIL = {
  ...fixtures.summaryMentionTrail,
  chapters: [
    fixtures.summaryMentionTrail.chapters[0],
    { ...fixtures.summaryMentionTrail.chapters[0], chapter_number: 7, author_written: true },
  ],
};

/** 全书总结状态（Step 4）。纯手写 stub（见路由表那条注释）——四章把四态一眼摆全：
 *  `paired`（对得上）/ `stale`（不对齐，该覆写）/ `missing`（缺，该补）/ `empty`
 *  （没正文），第 4 章还是 `anomaly`（生成异常，§6）。第 5 章权重 0 = 这一轮不看。 */
const SUMMARY_STATUS = {
  draft_chapter: 4,
  focused_chapter: 4,
  chapters: [
    { chapter_number: 1, has_text: true, state: "paired", weight: 1.0, anomaly: false },
    { chapter_number: 2, has_text: true, state: "stale", weight: 1.0, anomaly: false },
    { chapter_number: 3, has_text: true, state: "missing", weight: 1.0, anomaly: false },
    { chapter_number: 4, has_text: true, state: "missing", weight: 0.0, anomaly: true },
    { chapter_number: 5, has_text: false, state: "empty", weight: 0.0, anomaly: false },
  ],
};

/** 章标之前躺着一整章那一档：**全书章号可能集体错一位**，而界面上看不出任何异常。
 *  真 dump 出来的（`preamble_chars: 1104` 越过后端 1000 的门槛，警告那段字是后端写的）。 */
export const IMPORT_SUMMARY_ALARM = fixtures.bootstrapPreamble.summary;

/** 导入回执正常那一档（`preamble_chars: 0`，无警告）。 */
export const BOOTSTRAP_IMPORT = fixtures.bootstrapImport;

/** 默认路由表：URL → fixture。测试可以前置自己的 handler 覆盖其中任意一条。 */
const DEFAULT: Handler[] = [
  // AI 设置。**中栏编辑器也在拿它**（续写能带多少上文是后端算的，跟着这条回来），
  // 所以它得是默认路由的一条，否则每块带编辑器的屏幕都会撞上「没准备 handler」。
  // 给的是「一个字都没配过」那一份——要验配好之后长什么样的测试自己前置 `settingsSaved`。
  { match: /\/api\/settings$/, body: fixtures.settings },
  { method: "POST", match: /\/api\/projects\/bootstrap$/, body: BOOTSTRAP_IMPORT },
  { method: "POST", match: /\/sync$/, body: fixtures.sync },
  { match: /\/api\/projects$/, body: fixtures.projects },
  // ⚠️ 默认给的是 `rosterWithCounts` 那一份，**不是 `roster`**：两份都是真 dump，
  // 差别只有出场章数——`roster` 抓在总结落地之前（全 0），`rosterWithCounts` 抓在之后
  // （萧决 / 青云城主府各 1 章）。角色册那一格要按次数降序 + 一颗倒序切换，
  // 全 0 的样本两个方向渲染出来一模一样，测试永远绿。
  { match: /\/roster$/, body: fixtures.rosterWithCounts },
  // 这个人的事件时间线（2026-08-25）。**排在 `/characters/…/profile` 之前**：
  // 两条都以 `/characters/{id}/…` 开头，先匹配到的先赢。
  { match: /\/characters\/[^/]+\/events$/, body: fixtures.characterEvents },
  { match: /\/chapters$/, body: fixtures.chapters },
  { match: /\/chapters\/\d+\/constraints/, body: fixtures.constraints },
  { match: /\/chapters\/\d+\/state/, body: fixtures.states },
  { match: /\/chapters\/\d+\/mentioned/, body: fixtures.mentioned },
  { match: /\/chapters\/\d+\/proposals/, body: fixtures.proposals },
  { match: /\/chapters\/\d+\/events\?scope=PROVISIONAL/, body: fixtures.eventsProvisional },
  { match: /\/chapters\/\d+\/events\?scope=CANON/, body: fixtures.eventsCanon },
  { match: /\/chapters\/\d+\/summaries$/, body: fixtures.summaries },
  // 章节总结那一格的四条（读 / 生成 / 改 / 撤回）。**四条出参是同一个形状**，
  // 所以前三条吃的都是同一份真 dump（`summaryGenerated`）——不是三份手写的东西。
  //
  // ⚠️ 撤回那一档是**从真 dump 派生**的（把 `summary` / `created_at` 置空 +
  // `retracted`），因为一份「刚被撤回」的回执在契约夹具里还没有。
  // `tests/test_frontend_contract.py` 已经在抓它了（`summaryRetracted`），
  // 下一次重生成夹具之后把这一行换成那个键。
  // 倒排那两条排在 `…/summary$` 前面：那条正则要求 `summary` 结尾，
  // `…/summary/mentions` 不会被它咬到，但顺序摆对了看得更清楚。
  { match: /\/chapters\/\d+\/summary\/mentions$/, body: fixtures.summaryMentions },
  { match: /\/nodes\/[^/]+\/summary-mentions$/, body: SUMMARY_TRAIL },
  { match: /\/chapters\/\d+\/summary$/, body: fixtures.summaryGenerated },
  { method: "PATCH", match: /\/chapters\/\d+\/summary$/, body: fixtures.summaryGenerated },
  {
    method: "DELETE",
    match: /\/chapters\/\d+\/summary$/,
    body: { ...fixtures.summaryGenerated, summary: null, created_at: null, retracted: true },
  },
  { match: /\/chapters\/\d+\/text/, body: fixtures.chapterText },
  // 默认给**两版**那一份：一版的历史里没有还原/删除可点，照它写的界面等于没验过。
  { match: /\/chapters\/\d+\/history$/, body: fixtures.chapterHistoryTwo },
  { method: "PUT", match: /\/chapters\/\d+\/text$/, body: fixtures.chapterSaved },
  { method: "DELETE", match: /\/snapshots\//, body: { deleted: true } },
  { method: "POST", match: /\/nodes$/, body: fixtures.createNode },
  { method: "POST", match: /\/aliases$/, body: fixtures.createAlias },
  // 当前章焦点（2026-08-18 §3）：免费心跳，只记位置。
  { method: "POST", match: /\/focus$/, body: { chapter: 1, project_id: "project:ID1" } },
  // 全书总结状态视图（2026-08-18 文档 §6 / Step 4）。
  //
  // ⚠️ 纯手写 stub（同上两条）：`…/summary-status` 是这条线新增的端点，
  // `api.json` 里还没有它（那份 fixture 由 `tests/test_frontend_contract.py` 从真 app
  // dump）。形状是确定性查库的结果（不调模型），等它进真 dump 后换成 fixture 键。
  // 形状：`paired` 一章 + `missing` 一章 + `anomaly` 一章 + `empty` 一章 + 权重反馈。
  { match: /\/summary-status$/, body: SUMMARY_STATUS },
  // 人物基础信息（Task 11/15）：profile 读 + 别名增/撤回。
  { match: /\/characters\/[^/]+\/profile$/, body: fixtures.characterProfile },
  { method: "POST", match: /\/characters\/[^/]+\/aliases$/, body: fixtures.aliasCreated },
  { method: "DELETE", match: /\/aliases\/[^/]+$/, body: fixtures.aliasDeleted },
  { method: "PATCH", match: /\/aliases\/[^/]+$/, body: fixtures.aliasEdited },
  { method: "POST", match: /\/accept$/, body: fixtures.proposalAccept },
  { method: "POST", match: /\/reject$/, body: fixtures.proposalReject },
  // 「改一改再收下」。回执和 accept 同型（`ProposalResolution`），差别在 `status`——
  // 后端真 dump 的那两份分别是 `ACCEPTED` / `REJECTED`，这条路由要的是 `EDITED`。
  // **不手写一份**：拿真回执改一个字段，比编一个 27 字段的对象离真形状近得多。
  {
    method: "POST",
    match: /\/edit$/,
    body: { ...fixtures.proposalAccept, status: "EDITED" },
  },
  // 一次整理跑完了长什么样（真 dump）。**没跑成那一份不做默认**：要验它的测试自己前置，
  // 免得每一块屏幕都莫名其妙挂着一条失败。
  { match: /\/extractions\//, body: fixtures.extractionRun },
  { method: "POST", match: /\/provisional\/confirm$/, body: fixtures.provisionalConfirm },
  // 改一条**已经生效**的事实（ADR 0020 的「可改」）。三条都是真 dump 的回执。
  //
  // **「补一条」那条必须排在前面**：两条路由都以 `/canon/knowledge` 结尾，只差前面
  // 那段 `/chapters/{n}/`。顺序反了的话，补一条会静默拿到「改」的回执——而两份回执
  // 长得像（都有 character/secret/since_chapter），测试不会当场看出来。
  { method: "POST", match: /\/canon\/events\/.*\/cast$/, body: fixtures.canonEventCast },
  // Canon 边纠错（Task 8 / ADR 0032）：读 / 改 / 撤回自动升上去的地点边。
  // 三条都是真 dump（`canonEdge` / `canonEdgeEdited` / `canonEdgeRetracted`）。
  // PATCH 之后旧 ID 失效——回执里的 replacement id 才是下一次要读的那条。
  { match: /\/canon\/edges\/[^/]+$/, body: fixtures.canonEdge },
  { method: "PATCH", match: /\/canon\/edges\/[^/]+$/, body: fixtures.canonEdgeEdited },
  { method: "DELETE", match: /\/canon\/edges\/[^/]+$/, body: fixtures.canonEdgeRetracted },
  // 系统通知（Task 10 / 前端 Task 14）：列表 + 忽略（真 dump）。
  { match: /\/notifications\/count$/, body: fixtures.notificationsCount },
  { match: /\/notifications$/, body: fixtures.notifications },
  {
    method: "POST",
    match: /\/notifications\/[^/]+\/ignore$/,
    body: fixtures.notificationsIgnored,
  },
  // 活动记录。**按 id 前缀分派详情**（`extraction_run:` / `decision:`）——和后端
  // `read_entry` 的分派判据是同一个，所以这几条路由表不会和真接口漂开。
  // id 在 URL 里是编码过的（`decision%3AID36`），所以只匹配前缀不匹配那个冒号。
  { match: /\/activity\/extraction_run/, body: fixtures.activityRunDetail },
  { match: /\/activity\/call/, body: fixtures.activityCallDetail },
  { match: /\/activity\/decision/, body: fixtures.activityDecisionDetail },
  // 过滤那一份是**另一次真 dump**（`activityAuthorOnly`），不是把上面那份筛一遍：
  // 「过滤后 actors 计数不变」这条断言只有拿真出参才验得出来。
  { match: /\/activity\?.*actor=author/, body: fixtures.activityAuthorOnly },
  { match: /\/activity(\?|$)/, body: fixtures.activity },
  { match: /\/runs(\?|$)/, body: fixtures.runs },
  { match: /\/rules$/, body: fixtures.recordedRules },
  // 写作助手（模式二）的六条。**顺序有意义**：`/chats` 那两条要排在 `/chats/…`
  // 前面，否则列表和详情会互相顶掉。
  { method: "POST", match: /\/chats$/, body: fixtures.chatCreated },
  { match: /\/chats$/, body: fixtures.chats },
  // 跑一轮走的是长连接（ADR 0024）。**默认给真 dump 的那一整串帧**——
  // 组件测试因此吃的是后端真的吐出来的字节，而不是一份手写的「我以为它长这样」。
  { method: "POST", match: EVENT_STREAM, stream: fixtures.chatTurnEvents },
  // 不流式那条仍然在（它是「换回请求/响应」那条退路），今天浏览器不打它。
  { method: "POST", match: /\/chats\/[^/]+\/turn$/, body: fixtures.chatTurn },
  { method: "POST", match: /\/chats\/[^/]+\/stop$/, body: fixtures.chatStopped },
  // 作者的规矩（ADR 0023 决策二）。**默认给「有两条」那一份**：三种长相里另外两种
  // （一条都没定过 / 定过都不作数了）在真 dump 里也各有一份，由要验它们的测试自己前置。
  { method: "DELETE", match: /\/chats\/[^/]+$/, body: fixtures.chatDeleted },
  { match: /\/chats\/[^/]+$/, body: fixtures.chatDetail },
  // 桌上摆着的那几稿（ADR 0022）。两条**形状上不重叠**：详情那条要求 `/drafts/` 后面
  // 还有一段，列表那条要求 `/drafts` 之后直接是查询串或结尾。
  { match: /\/drafts\/[^/]+$/, body: fixtures.draftDetail },
  { match: /\/drafts(\?|$)/, body: fixtures.drafts },
];

/** 一串帧变成一个真的 `ReadableStream`。**一帧劈成两个 chunk 发**：
 *  网络本来就想在哪儿切就在哪儿切，而「按 chunk 解析」在本机上几乎永远是对的、
 *  然后在作者的机器上偶尔丢半帧。所以这儿故意切在帧中间。 */
function streamOf(frames: Frames): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  let tail: Uint8Array | null = null;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (tail) {
        controller.enqueue(tail);
        tail = null;
        return;
      }
      if (i >= frames.length) return controller.close();
      const frame = await frames[i++];
      const half = Math.max(1, Math.floor(frame.length / 2));
      tail = encoder.encode(frame.slice(half));
      controller.enqueue(encoder.encode(frame.slice(0, half)));
    },
  });
}

/** 把 fetch 换成查表。**没有匹配上就抛**——静默返回空数组会让组件渲染出一个
 *  「看起来正常的空面板」，而那正是这套测试要拦的失败形态（§10 约束 8）。 */
export function stubFetch(extra: Handler[] = []): void {
  const routes = [...extra, ...DEFAULT];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const hit = routes.find(
      (r) => (r.method ?? "GET") === method && r.match.test(String(url)),
    );
    if (!hit) throw new Error(`测试里没有为 ${method} ${url} 准备 handler`);
    const status = hit.status ?? 200;
    const ok = status < 400;
    const body = typeof hit.body === "function" ? await hit.body() : hit.body;
    // 拒绝那一档**永远是 JSON**，就算路由是长连接那条：后端把 4xx 全抛在第一个
    // 字节之前（`api/chat.py::_TurnRun`），前端也照 `client.ts` 那条老路拆它。
    if (ok && EVENT_STREAM.test(String(url))) {
      const frames =
        typeof hit.stream === "function"
          ? await hit.stream()
          : (hit.stream ?? turnStream(body));
      return { ok, status, body: streamOf(frames) } as unknown as Response;
    }
    return {
      ok,
      status,
      json: async () => body,
    } as Response;
  });
}

export function renderWithApi(ui: ReactElement, extra: Handler[] = []) {
  stubFetch(extra);
  // retry: false —— 默认 3 次重试会让「断言失败」变成「测试超时」，报错难读。
  //
  // `refetchOnWindowFocus: false` **是在抄 `main.tsx`**，不是测试偏好：react-query 的
  // 默认值是 `true`，不写这一行，测试里每一条查询都开着「切回来重取」，而生产里
  // 只有点名开的那两条开着。那种装配下「切回来会不会重取」这件事**测不出来**——
  // 断言在两种实现下都绿。两边的默认值必须是同一个。
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
