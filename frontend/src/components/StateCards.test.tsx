import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StateSnapshot } from "../api/types";
import { fixtures, renderWithApi } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { useCoords } from "../store";
import { RightPanel } from "./RightPanel";
import { StateCards, StateTab } from "./StateCards";

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 13,
    cast: "",
    castInclude: "",
    activeTab: "state",
    focusCell: null,
    focusEventId: null,
  });
});

type Calls = { mock: { calls: unknown[][] } };
const urls = (spy: Calls) => spy.mock.calls.map(([u]) => String(u));
const writes = (spy: Calls) =>
  spy.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method);

describe("当前状态卡", () => {
  it("渲染在场角色、所在地与状态维度", () => {
    const states = fixtures.states as unknown as StateSnapshot[];
    renderWithApi(<StateCards states={states} />);
    for (const s of states) {
      const card = screen.getByText(new RegExp(s.node.name)).closest(".statecard") as HTMLElement;
      expect(
        within(card).getByText(new RegExp(s.location ? s.location.name : "未记录")),
      ).toBeInTheDocument();
    }
  });

  it("空列表给空态而不是假装有角色", () => {
    renderWithApi(<StateCards states={[]} />);
    expect(screen.getByText(/无在场角色/)).toBeInTheDocument();
  });

  it("**还没读回来**和「一个人都没有」不是同一句话", () => {
    // §10 约束 8：静默的零和真的零不许长得一样。这一条在「看上一章」之前只是理论问题
    // ——换章才会换查询键；开关按下去**每一次**都换一个键，于是那半秒的空窗会稳定地
    // 告诉作者「上一章收尾时一个人都不在」，而那是一句假话。
    renderWithApi(<StateCards states={undefined} />);
    expect(screen.getByText(/正在读/)).toBeInTheDocument();
    expect(screen.queryByText(/无在场角色/)).toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 「· 已亡」那个角标 —— 它到 2026-08-13 为止一次都没画出来过
// ══════════════════════════════════════════════════════════════════════════
//
// 病史（`ARCHITECTURE.md` 已知洞第 8 条最后一段）：`StateSnapshot.is_dead` 是一个
// `@property`，而 `model_dump()` **从不输出 property**。`api/types.ts` 里早写着
// `is_dead: boolean`、这个组件也早在渲染那个角标，可那个键在浏览器里恒为 `undefined`。
// **`tsc` 和 vitest 谁都看不见**：契约夹具是从真 app dump 的，真 app 就没发过这个键,
// 两头一致地缺。改成 `computed_field` 之后它才第一次到得了这里。
//
// 所以这两条一起才说得清那条链通了：
//  ① 真后端 dump 出来的那份快照里**有**这个键（键没了 = 那个 field 又退回 property）；
//  ② 它为真的时候，角标真的画得出来。

describe("「· 已亡」角标", () => {
  it("真后端发的那份快照里有「是否已亡」这一项 —— 它曾经整个不存在", () => {
    // 判据是「键在不在」，不是「值是什么」（真书里那两个人都活着，值恒为 false）。
    // 这一条红了 = `computed_field` 没了，而组件那一侧一个字都不会报错。
    for (const snapshot of fixtures.states) {
      expect(Object.keys(snapshot)).toContain("is_dead");
    }
    expect(Object.keys(fixtures.characterState)).toContain("is_dead");
  });

  it("是真的时候画得出来 —— 而且只画在那一个人身上", () => {
    // **不手写一份快照**：拿真 dump 改一个字段（同 `harness.tsx` 里撤回那一档的做法）。
    // 真夹具里两个人都活着，所以这一档正常数据下永远不亮，正是它躲过守卫的方式。
    const [first, second] = fixtures.states as unknown as StateSnapshot[];
    renderWithApi(<StateCards states={[{ ...first, is_dead: true }, second]} />);

    const dead = screen.getByText(first.node.name).closest(".statecard") as HTMLElement;
    expect(within(dead).getByText(/已亡/)).toBeInTheDocument();
    const alive = screen.getByText(second.node.name).closest(".statecard") as HTMLElement;
    expect(within(alive).queryByText(/已亡/)).toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 「看上一章」——那台时光机第一次交到作者手上
// ══════════════════════════════════════════════════════════════════════════

describe("看上一章：问的是哪一章", () => {
  it("默认停在本章，一个字都不多说", async () => {
    renderWithApi(<RightPanel />);
    await screen.findByRole("button", { name: "看上一章结束时" });
    expect(screen.queryByText(/正在看第/)).toBeNull();
    // 那一格的标签也没变 —— 「不是本章」只在真的不是本章时才说。
    expect(screen.getByRole("button", { name: "人物状态" })).toBeInTheDocument();
  });

  it("按下去之后，问的是上一章那个刻度（后端一个字都没改）", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await screen.findByRole("button", { name: "看上一章结束时" });
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(screen.getByRole("button", { name: "看上一章结束时" }));

    await waitFor(() => expect(urls(spy).some((u) => u.includes("/chapters/12/state"))).toBe(true));
    expect(urls(spy).some((u) => u.includes("/chapters/13/state"))).toBe(false);
    // **章号只在路径上**（那是 AS OF，是查询不是声明）——请求体里一个字节都没有。
    expect(writes(spy)).toEqual([]);
  });

  it("**它不花一分钱**：整段来回只有读，没有一次写", async () => {
    // 一次 SQL 换一块屏幕。这块面板真长出一次付费调用的话，它会长成「顺手替作者
    // 重新整理一遍上一章」——而那是他没按过的那一下。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));
    await screen.findByText(/正在看第 12 章结束时/);
    await user.click(screen.getByRole("button", { name: "回到第 13 章" }));
    await screen.findByRole("button", { name: "看上一章结束时" });

    expect(writes(spy)).toEqual([]);
  });

  it("回得去，而且回去之后问的又是本章", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));
    await screen.findByText(/正在看第 12 章结束时/);

    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(screen.getByRole("button", { name: "回到第 13 章" }));
    await waitFor(() => expect(screen.queryByText(/正在看第 12 章结束时/)).toBeNull());
    expect(urls(spy).some((u) => u.includes("/chapters/13/state"))).toBe(true);
  });
});

describe("看上一章：屏幕上一眼看得出这不是本章", () => {
  it("横幅同时说出两个章号 —— 在看第几章、你在写第几章", async () => {
    // 这块屏幕唯一真正的风险：作者对着上一章的局面去声明、去改本章的事实。
    // 那造出来的是一条 `valid_from` 错了的 CANON 边，而它在面板上长得完全正常。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));

    const banner = (await screen.findByText(/正在看第 12 章结束时/)).closest(
      ".lookback",
    ) as HTMLElement;
    expect(banner).not.toBeNull();
    expect(banner.textContent).toContain("第 12 章");
    expect(banner.textContent).toContain("你正在写的第 13 章");
    // 卡片就在这块变了色的框里 —— 不是一行小字挂在上面。
    expect(banner.querySelector(".statecard")).not.toBeNull();
  });

  it("那一格的标签也跟着改 —— 面板滚下去之后横幅就看不见了", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));

    expect(await screen.findByRole("button", { name: "人物状态 · 第 12 章" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "人物状态" })).toBeNull();
  });

  it("「这一格算的是谁」那一行也换成上一章 —— 卡片是那一章正文里数出来的人", async () => {
    // 后端的 `_effective_cast` 从**路径上那一章**的正文里数人（ADR 0018）。
    // 这一行照旧说「这一章提到：…」，就是给一块讲第 12 章的面板配一句讲第 13 章的话。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    expect(await screen.findByText("这一章提到：")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "看上一章结束时" }));
    expect(await screen.findByText("第 12 章提到：")).toBeInTheDocument();
    expect(screen.queryByText("这一章提到：")).toBeNull();
  });

  it("整块屏幕上一个研发术语都没有", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));
    await screen.findByText(/正在看第 12 章结束时/);
    expect(devTerms(screenText())).toEqual([]);
  });
});

describe("看上一章：第 1 章那一档", () => {
  it("按不下去，而且说得出为什么", async () => {
    // 零永远带着一句理由（§10 约束 8）。一颗灰着的按钮不说为什么，作者只会以为它坏了；
    // 一个空的「（无）」更糟——它把「这本书从这儿开始」说成了「上一章什么都没有」。
    useCoords.setState({ chapter: 1 });
    renderWithApi(<RightPanel />);

    const button = await screen.findByRole("button", { name: "看上一章结束时" });
    expect(button).toBeDisabled();
    expect(screen.getByText(/第 1 章是全书的开头，它前面没有一章可看/)).toBeInTheDocument();
    expect(screen.queryByText(/（无）/)).toBeNull();
  });

  it("在别的章打开着，翻到第 1 章也问不出第 0 章", async () => {
    // 这一条钉的是「界面上说的和请求里问的不许分家」。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));
    await screen.findByText(/正在看第 12 章结束时/);

    const spy = vi.spyOn(globalThis, "fetch");
    useCoords.setState({ chapter: 1 });

    await screen.findByText(/第 1 章是全书的开头/);
    expect(screen.queryByText(/正在看第/)).toBeNull();
    await waitFor(() => expect(urls(spy).some((u) => u.includes("/chapters/1/state"))).toBe(true));
    expect(urls(spy).some((u) => u.includes("/chapters/0/state"))).toBe(false);
  });
});

describe("看上一章：换一章就回到本章", () => {
  it("翻到下一章之后，这一格默认又是本章 —— 不是悄悄跟着走", async () => {
    // **默认安全的那一档值一次点击。** 开着的时候屏幕上是另一章的局面，而作者照着它
    // 去改本章的事实，造出来的是一条 `valid_from` 错了的 CANON 边——面板上长得完全正常。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));
    await screen.findByText(/正在看第 12 章结束时/);

    useCoords.setState({ chapter: 20 });

    expect(await screen.findByRole("button", { name: "看上一章结束时" })).toBeEnabled();
    expect(screen.queryByText(/正在看第/)).toBeNull();
    expect(screen.getByRole("button", { name: "人物状态" })).toBeInTheDocument();
  });

  it("**它记的是「在第几章按下的」**，所以翻回去还在，去别处就不在", async () => {
    // 这才是「存章号不存布尔」的完整语义，而它要的那条安全性质是：
    // **你没在那一章上按过这个开关，就永远不会落在上一章模式里。**
    //
    // 存布尔的话第 1 章那一档会长出一个**说谎的控件**：按钮画成没按下（那一章没有
    // 上一章），而心里那个 `true` 还留着，翻到第 5 章面板就自己跳进去了。
    // 存章号让那种状态在实现里表示不出来 —— 按钮的样子永远等于面板的样子。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));
    await screen.findByText(/正在看第 12 章结束时/);

    // 路过一段没按过开关的章：**一路都是本章**。
    useCoords.setState({ chapter: 1 });
    await screen.findByText(/第 1 章是全书的开头/);
    expect(screen.queryByText(/正在看第/)).toBeNull();
    useCoords.setState({ chapter: 20 });
    await waitFor(() => expect(screen.getByRole("button", { name: "看上一章结束时" })).toBeEnabled());
    expect(screen.queryByText(/正在看第/)).toBeNull();

    // 回到按过的那一章：停在他离开时的样子。
    useCoords.setState({ chapter: 13 });
    expect(await screen.findByText(/正在看第 12 章结束时/)).toBeInTheDocument();
  });
});

describe("看上一章：作者填不了章号（约束 10）", () => {
  it("这一格上一个数字框都没有 —— 只有「本章 / 上一章」两个位置", async () => {
    // 源码那一侧的零基线由 `tests/test_canon_edit_boundary.py::
    // test_no_screen_in_the_whole_workbench_posts_a_chapter` 钉着（全前端只许有一个
    // `<input type="number">`）。这一条量的是**渲染出来的东西**：一个从别处 import
    // 来的 `<NumberBox/>` 在源码里长得完全无害。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "看上一章结束时" }));
    await screen.findByText(/正在看第 12 章结束时/);

    expect(screen.queryAllByRole("spinbutton")).toHaveLength(0);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("combobox")).toHaveLength(0);
  });
});

describe("看上一章：这一格自己（不经右栏）", () => {
  it("开关的两个位置都由「作者停在第几章」推出来，组件自己不认识章号", () => {
    // `StateTab` 收的是**作者停在第几章**，不是「要看第几章」——「上一章」在这一层
    // 是减一算出来的。把「看第几章」做成入参，第一个调用方就会把它接到一个输入框上。
    renderWithApi(
      <StateTab states={[]} chapter={88} lookingBack onLookBack={() => {}} />,
    );
    expect(screen.getByText(/正在看第 87 章结束时/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "回到第 88 章" })).toBeInTheDocument();
  });
});
