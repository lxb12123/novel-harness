import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, stubFetch } from "../test/harness";
import { useCoords } from "../store";
import { ActivityLog } from "./ActivityLog";
import { RightPanel } from "./RightPanel";

// 日志页那个跳转坐标落到右栏的时候，**方向**对不对。
//
// ADR 0018 的全部安全性押在一句话上：推导是超集 = 多禁 = fail-closed。
// `ActivityJump.cast` 是**第一次由系统自己往面板的在场里塞东西**，而右栏三格吃的是
// 同一份在场：
//
//   · 「人物认知」少一行 = 作者看不见那个人的认知状态（看得见的失败）
//   · **「写作提醒」少一个人 = 少一批禁令**（看不见的失败，产物是一份看起来完全正常、
//     只是说破了不该说破的东西的正文）
//
// 所以这里验的不是「跳过去停在哪一格」，是**那次跳转在两个参数里选了哪一个**：
// `cast=` 是过滤（作者的场景块），`include=` 只加不减（系统的坐标）。
//
// 喂进来的每一个字节仍然来自 `api.json`（真 app dump）；下面那条派生的详情也是从那份
// dump 拼的——手写夹具等于两份手写的东西互相验证。

/** 右栏那三条读端。**它们共用一份在场**，所以三条都得看。 */
const PANEL = /\/chapters\/\d+\/(matrix|constraints|state)\?/;
/** 「写作提醒」那一条：禁令就是从这儿来的。 */
const CONSTRAINTS = /\/chapters\/\d+\/constraints\?/;

/** 这条请求在**过滤**在场（`cast=` 非空）—— 系统不许这么干。 */
const filters = (url: string) => /[?&]cast=[^&]*[^&=]/.test(url);
/** 这条请求在**加人**（`include=` 非空）。 */
const widens = (url: string) => /[?&]include=[^&]*[^&=]/.test(url);

const panelUrls = (urls: string[]) => urls.filter((u) => PANEL.test(u));

/** 那条「更正认知类型」的日志行：全页唯一一条指向认知矩阵某一格的。 */
const KNOWLEDGE_ROW = /更正认知类型/;
/** 一条已生效情节：它跳的是一份名单，不是矩阵的一行。 */
const REVIEW_ROW = /抽取结果审阅/;

/** 从真 dump 里挑出那条 `event_cast` 的折叠行（`api.json` 里就有，不手写）。 */
const EVENT_CAST_ENTRY = fixtures.activity.entries.find(
  (e) => e.jump && e.jump.target === "event_cast",
)!;

/** `renderWithApi` 会把 fetch 换掉，所以这里自己装一遍，多记一份「请求过哪些 URL」。
 *  测的正是 URL 本身：坐标进了哪个参数，只有它看得见。 */
function renderSpying(ui: ReactElement, extra: Parameters<typeof stubFetch>[0] = []) {
  stubFetch(extra);
  const inner = globalThis.fetch;
  const urls: string[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    urls.push(String(url));
    return inner(url as never, init);
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { urls, ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>) };
}

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    cast: "",
    castInclude: "",
    activeTab: "matrix",
    page: "log",
    focusCell: null,
    focusEventId: null,
  });
});

describe("日志跳转的在场坐标", () => {
  it("**只加人，不过滤** —— 三条读端一条都不许被收窄", async () => {
    const user = userEvent.setup();
    const { urls } = renderSpying(
      <>
        <ActivityLog />
        <RightPanel />
      </>,
    );

    await user.click(await screen.findByRole("button", { name: KNOWLEDGE_ROW }));
    await user.click(await screen.findByRole("button", { name: /去认知矩阵改这一格/ }));

    // 坐标真的到了面板上（否则下面那条断言是空转）。
    await waitFor(() => expect(panelUrls(urls).filter(widens).length).toBeGreaterThan(0));
    // **三条共用一份在场，所以三条都得拿到它**：写作提醒少了它就是少一批禁令。
    for (const what of ["matrix", "constraints", "state"]) {
      expect(
        urls.filter((u) => u.includes(`/${what}?`) && widens(u)),
        `${what} 没拿到那个坐标 —— 三格会各按不同的在场算`,
      ).not.toHaveLength(0);
    }
    // 一条都不许是过滤。
    expect(panelUrls(urls).filter(filters), "系统替作者把在场收窄了").toEqual([]);
    expect(useCoords.getState().cast).toBe("");
    expect(useCoords.getState().castInclude).toBe("萧决");
  });

  it("探针：坐标要是走了过滤那个参数，这张网当场抓得住", async () => {
    // 这就是 2026-08-11 之前那一版的形状——`jump.cast` 直接进 `cast`。
    // 没有这条，上面那句「一条都不许是过滤」证明不了自己认得出违规。
    useCoords.setState({ cast: "萧决" });
    const { urls } = renderSpying(<RightPanel />);

    await waitFor(() => expect(panelUrls(urls).length).toBeGreaterThan(0));
    expect(
      panelUrls(urls).filter(filters).length,
      "探针失效：`cast=萧决` 都没被认成过滤，那上面那条断言什么也拦不住",
    ).toBeGreaterThan(0);
    expect(urls.filter((u) => CONSTRAINTS.test(u) && filters(u)).length).toBeGreaterThan(0);
  });

  it("跳去改情节名单的那一档，一个人都不许往右栏里加", async () => {
    // `event_cast` 跳的是「已确认情节」那一格里的一份名单，不是矩阵的一行。
    // 给了坐标，右栏那三格会凭空多出一个谁也没要求过的人。
    const user = userEvent.setup();
    renderSpying(
      <>
        <ActivityLog />
        <RightPanel />
      </>,
      // 详情按 id 前缀分派，一份 fixture 罩住所有 decision 行——这里把那条 event_cast 的
      // 折叠行换进去（**entry 整个来自真 dump**，只是换了是哪一条）。
      [
        {
          match: /\/activity\/decision/,
          body: { ...fixtures.activityDecisionDetail, entry: EVENT_CAST_ENTRY },
        },
      ],
    );

    const rows = await screen.findAllByRole("button", { name: REVIEW_ROW });
    await user.click(rows[0]);
    await user.click(await screen.findByRole("button", { name: /去改这条事件的知情/ }));

    expect(useCoords.getState().castInclude).toBe("");
    expect(useCoords.getState().cast).toBe("");
  });

  it("跳过来的那个人只是这一次跳转的余温 —— 换章就该没了", async () => {
    // 坐标算的是**那一章**的表。换了章还留着，右栏就凭空多一行谁也没提过的人，
    // 而它是从别的章的一条日志上蹭过来的。
    const user = userEvent.setup();
    renderSpying(<ActivityLog />);
    await user.click(await screen.findByRole("button", { name: KNOWLEDGE_ROW }));
    await user.click(await screen.findByRole("button", { name: /去认知矩阵改这一格/ }));
    expect(useCoords.getState().castInclude).toBe("萧决");

    useCoords.getState().setChapter(4);
    expect(useCoords.getState().castInclude).toBe("");
  });

  it("作者自己挑了一场，系统加的那个人就得让位", async () => {
    // 场景块是作者亲手标的（ADR 0018：它压过推导）。这时还留着系统加的人，
    // 他挑的那一场就不是他看到的那一场了。
    const user = userEvent.setup();
    renderSpying(<ActivityLog />);
    await user.click(await screen.findByRole("button", { name: KNOWLEDGE_ROW }));
    await user.click(await screen.findByRole("button", { name: /去认知矩阵改这一格/ }));

    useCoords.getState().setCast("萧决,李管家");
    expect(useCoords.getState().castInclude).toBe("");
  });
});
