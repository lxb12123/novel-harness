import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useEvents } from "../api/hooks";
import { useCoords } from "../store";
import { ActivityLog } from "./ActivityLog";
import { RightPanel } from "./RightPanel";

// 闭环，**从日志页那一端出发**（ADR 0020：错了看得见 → 跳得过去 → 改得掉）。
//
// 现有的组件测试各自验一段：`ActivityLog.test.tsx` 验「跳转坐标来自后端」，
// `KnowledgeMatrix.test.tsx` / `CanonEventCast.test.tsx` 验「那一格改得动」。
// **中间那道缝没人验**：坐标交出去之后，右栏到底有没有出现一个能按的东西。
// 这份文件只测那道缝，以及它掉下去的两种方式：
//
//   ① 跳过去那一格根本不在这一章的表上（行由本章正文推，ADR 0018；
//      而 `valid_from` 只由引语决定，ADR 0006 —— 两者本来就可以对不上）；
//   ② 撞上「别处刚改过」之后，「看看最新的」重取的不是**版本住的那个读端**，
//      于是作者点几次都还是同一个 409。
//
// 装配镜像 `App.tsx`：日志页换掉的是**中栏**，右栏面板照旧在原地——「去改这一格」
// 跳的就是右边那一栏。两栏一起卸载的话，这条缝在测试里根本不存在。
//
// 喂进来的每一个字节都来自 `api.json`（真 app dump），一个字段都不是手写的。

const canonViews = fixtures.eventsCanon;

type Calls = { mock: { calls: unknown[][] } };
const since = (spy: Calls) => spy.mock.calls.length;
const urlsAfter = (spy: Calls, mark: number) =>
  spy.mock.calls.slice(mark).map(([url]) => String(url));
/** 改一条**已经生效**的事实的那几次 POST。
 *  换章会顺手发一次 `POST …/focus`（免费心跳，只上报位置），它不在这里算。 */
const canonPosts = (spy: Calls) =>
  spy.mock.calls.filter(
    ([url, init]) =>
      (init as RequestInit | undefined)?.method === "POST" && String(url).includes("/canon/"),
  );

/** **版本住在哪个读端上**，就得重取哪一个。
 *
 *  名单编辑器的 `expected_canon_version` 来自 `GET /api/projects`（`canon_version` 在
 *  project 行上，事件出参里没有它——`tests/test_canon_edit_loop.py` 钉了这条事实）。
 *  所以「看看最新的」只重取 `/events` 等于什么都没刷新：作者再按一次保存，
 *  发出去的还是同一个旧版本号，还是 409。 */
const reReadTheVersionSource = (spy: Calls, mark: number) =>
  urlsAfter(spy, mark).some((url) => /\/api\/projects$/.test(url));

/** 装配：中栏是日志页 / 正文二选一，右栏常驻（同 `App.tsx`）。 */
function Workbench() {
  const page = useCoords((s) => s.page);
  return (
    <>
      {page === "log" ? <ActivityLog /> : <div>正文</div>}
      <RightPanel />
    </>
  );
}

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    // 作者正在写第 5 章，日志那一条是第 1 章的事 —— 跳转必须把章号也带过去。
    chapter: 5,
    cast: "",
    activeTab: "roster",
    page: "log",
    focusEventId: null,
  });
});


// ── 2026-08-24：认知矩阵那两组没了 ────────────────────────────────────────
//
// 原来这里有四组：认知矩阵的闭环（跳过去 → 那一格改得掉 / 不在这一章的表上时说出来）、
// 事件名单那一半、409 之后的重取、以及「同一张表两个挂载点不许有两套规矩」。
// **第一组和第四组随秘密下线一起走了**（ADR 0039）——`MatrixView` 整个删了。
//
// 留下的两组考的是**同一道缝的事件那一半**：坐标交出去之后右栏有没有出现一个能按的
// 东西，以及撞 409 之后「看看最新的」重取的是不是版本真正住的那个读端。那道缝跟秘密无关。

describe("同一条闭环，事件那一半", () => {
  // 认知格那一半上面已经走完了。**事件名单这一半此前没有任何东西从日志页走到底**：
  // `ActivityLog.test.tsx` 只验到「`focusEventId` 被设成了后端给的那个 id」，
  // `CanonEventCast.test.tsx` 只验「组件单独渲染时那张名单改得动」——中间那一步
  //（坐标交出去之后，右栏到底有没有把**那一条**展开）没人验。
  //
  // 而这一步正是最容易假绿的一步：**真 dump 里两条已确认情节的概要一模一样**
  //（都是「萧决得知血脉秘密。」）。按概要认必然认错，认错的产物是作者改了另一条情节的
  // 名单——一次他没打算做的编辑，而且事后从界面上看不出来。

  /** 日志里那条 jump 指向已确认情节的行（真 dump，`endpoints` 非空）。 */
  const eventRow = fixtures.activity.entries.find(
    (e) => e.jump?.target === "event_cast" && e.jump.endpoints.length > 0,
  )!;

  it("跳过去之后，展开的是**后端指名的那一条**，而且当场改得掉", async () => {
    const user = userEvent.setup();
    renderWithApi(<Workbench />, [
      // 夹具只 dump 了两条详情，这里把「哪一行」换掉——两半都来自 api.json。
      { match: /\/activity\/decision/, body: { ...fixtures.activityDecisionDetail, entry: eventRow } },
    ]);
    const spy = vi.spyOn(globalThis, "fetch") as unknown as Calls;

    // 这一页上有两行标题副标题**都一样**（两次「接受：事件 1 条」），所以按第几条认，
    // 序号从夹具自己算出来——写死一个 index 就是在猜 dump 的顺序。
    const twins = fixtures.activity.entries.filter((e) => e.subtitle === eventRow.subtitle);
    const rows = await screen.findAllByRole("button", { name: new RegExp(eventRow.subtitle) });
    await user.click(rows[twins.indexOf(eventRow)]);
    await user.click(await screen.findByRole("button", { name: `${eventRow.jump!.label} →` }));

    const state = useCoords.getState();
    expect(state.activeTab).toBe("review");
    expect(state.focusEventId).toBe(eventRow.jump!.event_id);

    // 两条概要一模一样，所以「展开了一条」什么都不说明——展开的必须**恰好一条**。
    const heads = await screen.findAllByRole("button", { name: canonViews[0].event.summary });
    expect(heads).toHaveLength(2);
    await waitFor(() =>
      expect(heads.filter((h) => h.getAttribute("aria-expanded") === "true")).toHaveLength(1),
    );

    const knowers = await screen.findByRole("group", { name: "知道这件事的人" });
    await user.click(within(knowers).getByRole("checkbox", { name: canonViews[0].knowers[1].name }));
    await user.click(screen.getByRole("button", { name: "保存名单" }));

    // ★ 决定性的一条：打出去的是**日志给的那个 id**，不是屏幕上第一条同名情节。
    await waitFor(() => expect(canonPosts(spy)).toHaveLength(1));
    expect(String((canonPosts(spy)[0] as [string, RequestInit])[0])).toContain(
      // id 进路径要编码（`event%3AID35`）——比的是编码后的那一串，不是肉眼那一串。
      `/canon/events/${encodeURIComponent(eventRow.jump!.event_id!)}/cast`,
    );
  });

  it("**自守卫**：没人跳过来的时候，一条都不许自己展开", async () => {
    // 上面那条「恰好展开一条」要是因为「这个面板本来就全展开」而成立，它就什么都没验。
    useCoords.setState({ page: "workbench", activeTab: "review", chapter: 1, focusEventId: null });
    renderWithApi(<Workbench />);
    const heads = await screen.findAllByRole("button", { name: canonViews[0].event.summary });
    expect(heads.every((h) => h.getAttribute("aria-expanded") === "false")).toBe(true);
    expect(screen.queryByRole("button", { name: "保存名单" })).toBeNull();
  });
});

describe("「别处刚改过」之后，作者得真的能往下走", () => {
  // 409 在这个产品里是**常态不是边角**：作者一保存，后台就去整理那一章
  //（`api/app.py::_trigger_refresh`），干净的抽取结果直接升 CANON（ADR 0020），
  // 项目的 canon 版本就涨了一格 —— 而浏览器里那份 `/api/projects` 的缓存一个字都没变。
  // 所以他打开「待确认」改一次名单，撞 409 是**很可能发生的第一件事**。
  // 那时「看看最新的」必须真的把版本重读一遍，否则这条退路在他手里是死的。

  it("名单撞 409 之后，「看看最新的」要重取**版本住的那个读端**", async () => {
    const user = userEvent.setup();
    useCoords.setState({ page: "workbench", activeTab: "review", chapter: 1 });
    renderWithApi(<Workbench />, [
      {
        method: "POST",
        match: /\/canon\/events\/.*\/cast$/,
        status: 409,
        body: fixtures.errorStaleCanon,
      },
    ]);

    const summary = canonViews[0].event.summary;
    const heads = await screen.findAllByRole("button", { name: summary });
    await user.click(heads[0]);
    const knowers = await screen.findByRole("group", { name: "知道这件事的人" });
    await user.click(
      within(knowers).getByRole("checkbox", { name: canonViews[0].knowers[1].name }),
    );
    await user.click(screen.getByRole("button", { name: "保存名单" }));

    await screen.findByText(/先看一眼最新的/);
    const spy = vi.spyOn(globalThis, "fetch") as unknown as Calls;
    const mark = since(spy);
    await user.click(screen.getByRole("button", { name: "看看最新的" }));

    await waitFor(() => expect(reReadTheVersionSource(spy, mark)).toBe(true));
  });

  it("**自守卫**：只重取名单的那种实现，上面那条断言必须抓得住", async () => {
    // 会漏的假实现——它长得非常像对的那个（真的发了一次请求、真的把名单刷新了），
    // 唯一的差别是它重取的不是版本住的那个读端。没有这条探针，上面那句断言
    // 可能只是因为「点了任何按钮都会有请求」而恒真。
    function LeakyStale() {
      const events = useEvents("project:ID1", 1, "CANON");
      return (
        <button onClick={() => events.refetch()}>
          看看最新的
        </button>
      );
    }
    const user = userEvent.setup();
    renderWithApi(<LeakyStale />);
    const spy = vi.spyOn(globalThis, "fetch") as unknown as Calls;
    const mark = since(spy);
    await user.click(screen.getByRole("button", { name: "看看最新的" }));

    // 它确实重取了点东西（所以不是一个什么都不做的假探针）……
    await waitFor(() =>
      expect(urlsAfter(spy, mark).some((u) => u.includes("scope=CANON"))).toBe(true),
    );
    // ……但重取的不是版本住的那一个，网必须把它判红。
    expect(reReadTheVersionSource(spy, mark)).toBe(false);
  });
});

