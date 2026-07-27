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

type Handler = { method?: string; match: RegExp; status?: number; body: unknown };

/** 默认路由表：URL → fixture。测试可以前置自己的 handler 覆盖其中任意一条。 */
const DEFAULT: Handler[] = [
  { match: /\/api\/projects$/, body: fixtures.projects },
  { match: /\/roster$/, body: fixtures.roster },
  { match: /\/chapters$/, body: fixtures.chapters },
  { match: /\/chapters\/\d+\/matrix/, body: fixtures.matrix },
  { match: /\/chapters\/\d+\/constraints/, body: fixtures.constraints },
  { match: /\/chapters\/\d+\/state/, body: fixtures.states },
  { match: /\/chapters\/\d+\/scenes/, body: fixtures.scenes },
  { match: /\/chapters\/\d+\/text/, body: fixtures.chapterText },
  { method: "POST", match: /\/nodes$/, body: fixtures.createNode },
  { method: "POST", match: /\/aliases$/, body: fixtures.createAlias },
  { method: "POST", match: /\/locate$/, body: fixtures.locate },
  { method: "POST", match: /\/declare\/knows$/, body: fixtures.declareKnows },
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
    return {
      ok: (hit.status ?? 200) < 400,
      status: hit.status ?? 200,
      json: async () => hit.body,
    } as Response;
  });
}

export function renderWithApi(ui: ReactElement, extra: Handler[] = []) {
  stubFetch(extra);
  // retry: false —— 默认 3 次重试会让「断言失败」变成「测试超时」，报错难读。
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
