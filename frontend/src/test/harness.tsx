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

// ⚠️ **手写 stub，全仓仅此两条**：后台整理那两条端点由后端另一条线落地中，
// `api.json` 里还没有它们（那份 fixture 由 `tests/test_frontend_contract.py` 从真 app
// dump，手写它正是这条缝原本的病）。**后端落地后把这两条换成真 fixture。**
const AUTOPILOT_ACK = { chapter: 1, summary: "queued", extraction: "queued" };
const AUTOPILOT_IDLE = {
  chapter: 1,
  summary_ready: false,
  extraction_ready: false,
  running: false,
};

// ⚠️ **手写 stub，2026-08-13 新长出来的两处**（同上面 autopilot 那两条的理由）：
// `POST …/sync` 的出参和导入回执里的 `summary` 都是这一轮才有的东西，而这一轮
// 不许跑 `NH_UPDATE_FIXTURES=1`（几份改动并行，夹具由维护者统一重生成）。
// `tests/test_frontend_contract.py` **已经在抓它们了**（`sync` / `syncUnchanged` /
// `bootstrapPreamble`，且 `bootstrapImport` 会多出 `summary`）——
// **重生成之后把下面这三个常量删掉，换成 `fixtures.sync` / `fixtures.syncUnchanged` /
// `fixtures.bootstrapImport`。** 在那之前它们是两份手写的东西互相验证，正是这条缝的病。
const SYNC_CHANGED = {
  added_chapters: [3],
  updated_chapters: [2],
  unchanged_count: 1,
  chapter_count: 3,
  ignored_files: ["chapters/大纲.md"],
  headline: "读回来了：1 章有新内容、1 章是新写的（这本书现在共 3 章）。",
  notes: [
    "内容变了：第 2 章。之前那一版还在「历史」里。",
    "新读到：第 3 章。",
    "这些文件不是章节，没有动它们：chapters/大纲.md。",
  ],
};

/** 导入回执。**`preamble_chars` 正常那一档**——警告那一档由要验它的测试自己前置。 */
const IMPORT_SUMMARY = {
  chapter_count: 1,
  written_count: 1,
  unchanged_count: 0,
  landed_count: 1,
  ignored_files: [],
  preamble_chars: 0,
  headline: "《契约样书》切成了 1 章，已经建好了。",
  lines: ["切出了 1 章，其中 1 章已经可以被引用。", "新建了 1 个章节文件。"],
  warning: null,
};

/** 章标之前躺着一整章那一档：**全书章号可能集体错一位**，而界面上看不出任何异常。 */
export const IMPORT_SUMMARY_ALARM = {
  ...IMPORT_SUMMARY,
  chapter_count: 300,
  landed_count: 300,
  written_count: 300,
  preamble_chars: 3200,
  headline: "《青云记》切成了 300 章，已经建好了。",
  lines: ["切出了 300 章，其中 300 章已经可以被引用。", "新建了 300 个章节文件。"],
  warning:
    "⚠️ 第一章的标题之前还有 3200 个字，它们不属于任何一章。\n" +
    "最常见的原因是**第一章的标题没有被认出来**（比如它写成「楔子」「序章」，" +
    "或者标题那一行前面还有别的字）。真是这样的话，整本书的章号会集体差一章：" +
    "你在第 24 章记下的事，系统会记成第 23 章，而这件事在界面上看不出任何异常。\n" +
    "先打开这份 TXT 看一眼开头：如果那段字确实是第一章的正文，" +
    "把它的标题改成「第一章 ……」再重新导入一次（导入不会覆盖已经建好的书，" +
    "请新建一本）。如果那本来就是简介或者楔子，它不属于任何一章是对的，可以不管。",
};

/** 真 dump 那一份 + 上面那个 `summary`。**导入档后端一定会给它**，
 *  不给的话作者点完导入看到的还是那块「一个数字都没有」的屏幕。 */
export const BOOTSTRAP_IMPORT = { ...fixtures.bootstrapImport, summary: IMPORT_SUMMARY };

/** 默认路由表：URL → fixture。测试可以前置自己的 handler 覆盖其中任意一条。 */
const DEFAULT: Handler[] = [
  { method: "POST", match: /\/api\/projects\/bootstrap$/, body: BOOTSTRAP_IMPORT },
  { method: "POST", match: /\/sync$/, body: SYNC_CHANGED },
  { match: /\/api\/projects$/, body: fixtures.projects },
  { match: /\/roster$/, body: fixtures.roster },
  { match: /\/chapters$/, body: fixtures.chapters },
  { match: /\/chapters\/\d+\/matrix/, body: fixtures.matrix },
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
  { match: /\/chapters\/\d+\/summary$/, body: fixtures.summaryGenerated },
  { method: "POST", match: /\/chapters\/\d+\/summary$/, body: fixtures.summaryGenerated },
  { method: "PATCH", match: /\/chapters\/\d+\/summary$/, body: fixtures.summaryGenerated },
  {
    method: "DELETE",
    match: /\/chapters\/\d+\/summary$/,
    body: { ...fixtures.summaryGenerated, summary: null, created_at: null, retracted: true },
  },
  { method: "POST", match: /\/chapters\/\d+\/autopilot$/, body: AUTOPILOT_ACK },
  { match: /\/chapters\/\d+\/autopilot$/, body: AUTOPILOT_IDLE },
  { match: /\/chapters\/\d+\/scenes/, body: fixtures.scenes },
  { match: /\/chapters\/\d+\/text/, body: fixtures.chapterText },
  // 默认给**两版**那一份：一版的历史里没有还原/删除可点，照它写的界面等于没验过。
  { match: /\/chapters\/\d+\/history$/, body: fixtures.chapterHistoryTwo },
  { method: "PUT", match: /\/chapters\/\d+\/text$/, body: fixtures.chapterSaved },
  { method: "DELETE", match: /\/snapshots\//, body: { deleted: true } },
  { method: "POST", match: /\/nodes$/, body: fixtures.createNode },
  { method: "POST", match: /\/aliases$/, body: fixtures.createAlias },
  { method: "POST", match: /\/locate$/, body: fixtures.locate },
  { method: "POST", match: /\/declare\/knows$/, body: fixtures.declareKnows },
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
  // 改一条**已经生效**的事实（ADR 0020 的「可改」）。两条都是真 dump 的回执。
  { method: "POST", match: /\/canon\/knowledge$/, body: fixtures.canonKnowledge },
  { method: "POST", match: /\/canon\/events\/.*\/cast$/, body: fixtures.canonEventCast },
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
  { method: "DELETE", match: /\/rules\/\d+$/, body: fixtures.chatRuleRevoked },
  { match: /\/rules\?/, body: fixtures.chatRules },
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
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
