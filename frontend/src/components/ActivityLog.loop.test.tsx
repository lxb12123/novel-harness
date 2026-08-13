import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useEvents, useProjects } from "../api/hooks";
import type { KnowledgeMatrix, NodeRef } from "../api/types";
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

const matrix = fixtures.matrix as unknown as KnowledgeMatrix;
const roster = fixtures.roster as unknown as NodeRef[];
const canonViews = fixtures.eventsCanon;

/** 日志里那条「更正认知类型」（真 dump 里唯一一条 jump 指到认知矩阵某一格的行）。 */
const KNOWLEDGE_ROW = /更正认知类型/;
const KNOWLEDGE_JUMP = fixtures.activityDecisionDetail.entry.jump!;

/** 花名册里的人物，但**不在这一章的矩阵上**——真 dump 里就有这么一个
 *  （`未来大能` 登场章在第 200 章，第 1 章的正文里当然提不到他）。 */
const OFF_TABLE = roster.find(
  (n) => n.label === "Character" && !matrix.characters.some((c) => c.id === n.id),
)!;

type Calls = { mock: { calls: unknown[][] } };
const since = (spy: Calls) => spy.mock.calls.length;
const urlsAfter = (spy: Calls, mark: number) =>
  spy.mock.calls.slice(mark).map(([url]) => String(url));
/** 改一条**已经生效**的事实的那几次 POST。
 *  换章会顺手发一次 `POST …/autopilot`（离开的那一章交后台整理），它不在这里算。 */
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
    focusCell: null,
    focusEventId: null,
  });
});

/** 展开日志里那一条，再按它的跳转按钮。措辞用后端给的 `jump.label`，前端不编第二份。 */
async function jumpFromTheLog(user: ReturnType<typeof userEvent.setup>, row: RegExp) {
  await user.click(await screen.findByRole("button", { name: row }));
  await user.click(await screen.findByRole("button", { name: `${KNOWLEDGE_JUMP.label} →` }));
}

describe("从日志页出发的闭环", () => {
  it("看得见 → 跳得过去 → **右栏真的出现一个能按的东西** → 改得掉", async () => {
    // 这一条是这份文件的主干。ADR 0020 承诺的是三段连起来，而「跳过去之后有没有得点」
    // 此前只有组件各自的单测——组件单独渲染时那个按钮当然在，接不接得上是另一回事。
    const user = userEvent.setup();
    renderWithApi(<Workbench />);
    const spy = vi.spyOn(globalThis, "fetch") as unknown as Calls;

    await jumpFromTheLog(user, KNOWLEDGE_ROW);

    // 跳到位了：中栏回正文、右栏切到认知那一格、章号跟着走。
    const state = useCoords.getState();
    expect(state.page).toBe("workbench");
    expect(state.activeTab).toBe("matrix");
    expect(state.chapter).toBe(KNOWLEDGE_JUMP.chapter_number);

    // ★ 缝在这里：后端给的坐标必须在屏幕上变成一个**具体的、能按的**东西。
    const who = matrix.characters.find((c) => c.id === KNOWLEDGE_JUMP.character_id)!;
    const what = matrix.secrets.find((s) => s.id === KNOWLEDGE_JUMP.secret_id)!;
    expect(
      await screen.findByLabelText(`${who.name} 对 ${what.name}（刚跳转到这一格）`),
    ).toBeInTheDocument();
    const open = await screen.findByRole("button", { name: `改「${who.name} 对 ${what.name}」` });

    await user.click(open);
    await user.type(await screen.findByRole("textbox"), "以为那只是个传闻");
    await user.click(screen.getByRole("button", { name: "改成「以为」" }));

    await waitFor(() => expect(canonPosts(spy)).toHaveLength(1));
    const [url, init] = canonPosts(spy)[0] as [string, RequestInit];
    expect(String(url)).toContain("/canon/knowledge");
    // 打出去的坐标就是日志给的那两个 id，中间没有任何一步是从人名反解的。
    expect(JSON.parse(String(init.body))).toMatchObject({
      character_id: KNOWLEDGE_JUMP.character_id,
      secret_id: KNOWLEDGE_JUMP.secret_id,
      to_type: "BELIEVES",
    });

    // 第三段（「这次改动自己也留了痕」）：作者回到日志页时读的是**改完之后**的那一份，
    // 不是跳走前那一屏的缓存。多出来的那一行是引擎的活，钉在
    // `tests/test_canon_edit_loop.py::test_a_system_row_can_be_walked_back…`。
    const mark = since(spy);
    act(() => useCoords.setState({ page: "log" }));
    await waitFor(() =>
      expect(urlsAfter(spy, mark).some((u) => u.includes("/activity"))).toBe(true),
    );
  });

  it("跳过来了、但这一格不在这一章的表上时，**说出来**", async () => {
    // 认知格的坐标不保证落得下：行由本章正文推（ADR 0018），而 `valid_from` 只由引语
    // 决定（ADR 0006）——作者用一句满是代词的话声明认知，两者就对不上
    //（`tests/test_canon_edit_loop.py` 里那条真 HTTP 复现了它）。
    //
    // 那一刻屏幕上是一张**看起来很正常的表**，只是作者要改的那一格不在上面：
    // 高亮和编辑入口一起落空，而且没有任何东西告诉他发生了什么。§10 约束 8 说的
    // 就是这种失败——静默的零和真的零不许长得一样。已确认情节那一侧早就把这句话
    // 说出来了（「没有在这一章找到刚才那条情节」），认知矩阵这一侧不该是另一套规矩。
    useCoords.setState({
      page: "workbench",
      activeTab: "matrix",
      focusCell: { character_id: OFF_TABLE.id, secret_id: matrix.secrets[0].id },
    });
    renderWithApi(<Workbench />);

    expect(await screen.findByText(/没有在这一章找到刚才那一格/)).toBeInTheDocument();
    // 落空的是坐标在这张表上的位置，不是编辑能力本身 —— 别说成「改不了」。
    expect(document.body.textContent).not.toMatch(/改不了|没救|无法修改/);
  });

  it("**自守卫**：那一格真在表上的时候，不许挂那句话", async () => {
    // 会漏的网抓不住东西，会误报的网更糟：每次跳转都挂一句「没找到」，
    // 作者第二次就不看它了。
    const cell = matrix.cells[0];
    useCoords.setState({
      page: "workbench",
      activeTab: "matrix",
      focusCell: { character_id: cell.character_id, secret_id: cell.secret_id },
    });
    renderWithApi(<Workbench />);

    const who = matrix.characters.find((c) => c.id === cell.character_id)!;
    const what = matrix.secrets.find((s) => s.id === cell.secret_id)!;
    await screen.findByLabelText(`${who.name} 对 ${what.name}（刚跳转到这一格）`);
    expect(screen.queryByText(/没有在这一章找到刚才那一格/)).toBeNull();
  });

  it("没人跳过来的时候，一句多余的话都不说", async () => {
    // `focusCell` 是 null 时这张表和以前逐字节一样 —— 上面那条提示只属于「刚跳过来」。
    useCoords.setState({ page: "workbench", activeTab: "matrix", focusCell: null });
    renderWithApi(<Workbench />);
    await screen.findByRole("table");
    expect(screen.queryByText(/没有在这一章找到刚才那一格/)).toBeNull();
  });
});

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
  // 409 在这个产品里是**常态不是边角**：作者一离开某一章，后台就去整理那一章
  //（`autopilot.ts`），干净的抽取结果直接升 CANON（ADR 0020），项目的 canon 版本
  // 就涨了一格 —— 而浏览器里那份 `/api/projects` 的缓存一个字都没变。
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

describe("「改得掉」不许只在一块屏幕上成立", () => {
  // `MatrixView` 今天挂在**两个**地方：右栏第二格，和章节核对页。同一个 `CellEditor`、
  // 同一个 409、同一颗「看看最新的」。
  //
  // 但「重取」这件事此前是**调用方**的责任（`onRefresh?.()`）：右栏传了，核对页没传。
  // 于是在核对页上那颗按钮点下去只把编辑器收起来，一个字节都没重读——作者再打开那一格
  // 保存，发的还是同一个旧版本号，还是 409。而 `main.tsx` 写着
  // `refetchOnWindowFocus: false`，**没有任何东西会替他补这一次重读**：换章或刷新页面
  // 之前，这条退路在那块屏幕上是死的。
  //
  // 409 在这个产品里是常态：作者一离开某一章，后台就整理那一章，干净的抽取结果直接升
  // CANON（ADR 0020），版本涨一格，而浏览器里那份矩阵是进核对页时取的。

  /** 真 dump 里唯一那格非「不知道」的坐标。 */
  const knows = matrix.cells.find((c) => c.state === "KNOWS")!;
  const cellName = () => {
    const who = matrix.characters.find((c) => c.id === knows.character_id)!;
    const what = matrix.secrets.find((s) => s.id === knows.secret_id)!;
    return `改「${who.name} 对 ${what.name}」`;
  };

  /** **版本住在这张表自己身上**（`matrix.version.canon_version`，`/matrix` 那条路由填的），
   *  所以矩阵这一侧要重取的是 `/matrix` —— 和名单那一侧是**两个不同的读端**。 */
  const reReadTheMatrix = (spy: Calls, mark: number) =>
    urlsAfter(spy, mark).some((url) => url.includes("/matrix"));

  const stale = {
    method: "POST" as const,
    match: /\/canon\/knowledge$/,
    status: 409,
    body: fixtures.errorStaleCanon,
  };

  async function hitStaleAndAskForTheLatest(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: cellName() }));
    // 按 placeholder 认，不按 role="textbox" 认：这块屏幕上不止一个文本框。
    await user.type(await screen.findByPlaceholderText("例如：以为那只是个传闻"), "以为那只是个传闻");
    await user.click(screen.getByRole("button", { name: "改成「以为」" }));
    await screen.findByText(/先看一眼最新的/);
    const spy = vi.spyOn(globalThis, "fetch") as unknown as Calls;
    const mark = since(spy);
    await user.click(screen.getByRole("button", { name: "看看最新的" }));
    return { spy, mark };
  }

  // 这儿原本是**一对**：章节核对页一条、右栏一条，钉的是「同一张表的两个挂载点
  // 不许有两套规矩」。核对页 2026-08-13 删了（`TopBar.tsx` 记着为什么），
  // 于是认知矩阵**只剩右栏这一个挂载点**——那条成对的断言跟着一起走。
  it("撞 409 之后，「看看最新的」要真的把这张表重读一遍", async () => {
    const user = userEvent.setup();
    useCoords.setState({ page: "workbench", activeTab: "matrix", chapter: 1 });
    renderWithApi(<Workbench />, [stale]);

    const { spy, mark } = await hitStaleAndAskForTheLatest(user);
    await waitFor(() => expect(reReadTheMatrix(spy, mark)).toBe(true));
  });

  it("**自守卫**：重取错那个读端的实现，上面两条必须判红", async () => {
    // 这不是一个凭空想出来的假实现——它正是读完这份文件上半截之后最容易写错的那个：
    // 名单那一侧撞 409 要重取的是 `/api/projects`（版本住在项目行上），而矩阵这一侧
    // 版本住在表自己身上。**两侧答案不同**，照抄另一侧就是这条探针的样子：
    // 它真的发了请求、真的刷新了东西，只是刷的不是作者手上那个数。
    function LeakyMatrixStale() {
      const projects = useProjects();
      return <button onClick={() => projects.refetch()}>看看最新的</button>;
    }
    const user = userEvent.setup();
    renderWithApi(<LeakyMatrixStale />);
    const spy = vi.spyOn(globalThis, "fetch") as unknown as Calls;
    const mark = since(spy);
    await user.click(screen.getByRole("button", { name: "看看最新的" }));

    // 它确实重取了点东西（不是一个什么都不做的假探针）……
    await waitFor(() =>
      expect(urlsAfter(spy, mark).some((u) => /\/api\/projects$/.test(u))).toBe(true),
    );
    // ……但重取的不是矩阵那一侧版本住的地方，网必须把它判红。
    expect(reReadTheMatrix(spy, mark)).toBe(false);
  });
});
