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
type Handler = {
  method?: string;
  match: RegExp;
  status?: number;
  body: unknown | (() => unknown | Promise<unknown>);
};

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

/** 默认路由表：URL → fixture。测试可以前置自己的 handler 覆盖其中任意一条。 */
const DEFAULT: Handler[] = [
  { method: "POST", match: /\/api\/projects\/bootstrap$/, body: fixtures.bootstrapImport },
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
  { method: "POST", match: /\/chapters\/\d+\/summary$/, body: fixtures.summaryGenerated },
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
];

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
    const body = typeof hit.body === "function" ? await hit.body() : hit.body;
    return {
      ok: (hit.status ?? 200) < 400,
      status: hit.status ?? 200,
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
