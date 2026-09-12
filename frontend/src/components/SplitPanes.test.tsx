import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_CHAT_PCT,
  DEFAULT_LEFT,
  DEFAULT_RIGHT,
  KEY_PCT_STEP,
  KEY_STEP,
  MIN_CENTER,
  MIN_LEFT,
  MIN_RIGHT,
} from "../layout";
import { usePaneCollapse } from "../paneCollapse";
import { SplitPanes } from "./SplitPanes";

// jsdom 不做排版，clientWidth 恒为 0 —— 所以这儿验的是**规则和交互**（方向、下限、复位、
// 记住），「按容器宽算上限」那部分由 layout.test.ts 直接喂宽度验。

const panes = () => (
  <SplitPanes left={<div>左栏</div>} center={<div>中栏</div>} right={<div>右栏</div>} />
);

const leftBar = () => screen.getByRole("separator", { name: "调整左栏宽度" });
const rightBar = () => screen.getByRole("separator", { name: "调整右栏宽度" });
const widthOf = (bar: HTMLElement) => Number(bar.getAttribute("aria-valuenow"));

describe("可拖三栏", () => {
  it("三栏之间各有一根能抓的分隔条", () => {
    render(panes());
    expect(screen.getAllByRole("separator")).toHaveLength(2);
    for (const t of ["左栏", "中栏", "右栏"]) expect(screen.getByText(t)).toBeInTheDocument();
  });

  it("列宽真的落到 grid 上 —— 不是只改了个 aria 数字", () => {
    render(panes());
    fireEvent.keyDown(leftBar(), { key: "ArrowRight" });
    expect(screen.getByRole("main").style.gridTemplateColumns).toContain(
      `${DEFAULT_LEFT + KEY_STEP}px`,
    );
  });

  it("拖动：往右拖，左栏变宽", () => {
    render(panes());
    fireEvent.pointerDown(leftBar(), { button: 0, clientX: 210 });
    fireEvent.pointerMove(window, { clientX: 260 });
    fireEvent.pointerUp(window);
    expect(widthOf(leftBar())).toBe(DEFAULT_LEFT + 50);
  });

  it("拖动：右边那根往右拖，右栏变**窄**（它是从右边量的）", () => {
    render(panes());
    fireEvent.pointerDown(rightBar(), { button: 0, clientX: 1000 });
    fireEvent.pointerMove(window, { clientX: 1040 });
    fireEvent.pointerUp(window);
    expect(widthOf(rightBar())).toBe(DEFAULT_RIGHT - 40);
  });

  it("拖过头会停在下限 —— 这就是「拉伸到什么都看不见」的拦法", () => {
    render(panes());
    fireEvent.pointerDown(leftBar(), { button: 0, clientX: 210 });
    fireEvent.pointerMove(window, { clientX: -5000 });
    fireEvent.pointerUp(window);
    expect(widthOf(leftBar())).toBe(MIN_LEFT);

    fireEvent.pointerDown(rightBar(), { button: 0, clientX: 1000 });
    fireEvent.pointerMove(window, { clientX: 6000 });
    fireEvent.pointerUp(window);
    expect(widthOf(rightBar())).toBe(MIN_RIGHT);
  });

  it("方向键也能调（分隔条可聚焦，不只服务鼠标）", () => {
    render(panes());
    fireEvent.keyDown(leftBar(), { key: "ArrowRight" });
    expect(widthOf(leftBar())).toBe(DEFAULT_LEFT + KEY_STEP);
    fireEvent.keyDown(leftBar(), { key: "ArrowLeft" });
    expect(widthOf(leftBar())).toBe(DEFAULT_LEFT);
  });

  it("方向键按到底也停在下限", () => {
    render(panes());
    for (let i = 0; i < 40; i++) fireEvent.keyDown(leftBar(), { key: "ArrowLeft" });
    expect(widthOf(leftBar())).toBe(MIN_LEFT);
  });

  it("双击复位到默认宽", () => {
    render(panes());
    fireEvent.keyDown(leftBar(), { key: "ArrowRight" });
    expect(widthOf(leftBar())).not.toBe(DEFAULT_LEFT);
    fireEvent.doubleClick(leftBar());
    expect(widthOf(leftBar())).toBe(DEFAULT_LEFT);
  });

  it("拖完记住：重开工作台还是那个宽度", () => {
    const { unmount } = render(panes());
    fireEvent.keyDown(leftBar(), { key: "ArrowRight" });
    unmount();
    render(panes());
    expect(widthOf(leftBar())).toBe(DEFAULT_LEFT + KEY_STEP);
  });

  it("拖动结束要把 body 上的临时类摘干净 —— 否则整个应用卡在 col-resize 光标里", () => {
    render(panes());
    fireEvent.pointerDown(leftBar(), { button: 0, clientX: 210 });
    expect(document.body.classList.contains("resizing")).toBe(true);
    fireEvent.pointerUp(window);
    expect(document.body.classList.contains("resizing")).toBe(false);
  });
});

// ── 中栏对半分（写作助手开着的时候）──────────────────────────────────────────
//
// jsdom 里 clientWidth 恒为 0，所以**拖动那一条在这儿测不出来**（它要把像素换算成
// 百分比，而换算要量中栏）。那部分的判据在 `layout.test.ts::chatPctAfterDrag` 上，
// 而方向键这一条**按百分点走、不需要量任何东西**，所以它在这儿是真的可测。

const withChat = () => (
  <SplitPanes
    left={<div>左栏</div>}
    center={<div>中栏</div>}
    chat={<div>写作助手</div>}
    right={<div>右栏</div>}
  />
);
const chatBar = () => screen.getByRole("separator", { name: "调整正文和写作助手的分界" });

describe("中栏对半分", () => {
  it("不给 chat 就完全是原来那三栏 —— 连那根分隔条都不渲染", () => {
    render(panes());
    expect(screen.getAllByRole("separator")).toHaveLength(2);
    expect(document.querySelector(".center-split")).toBeNull();
  });

  it("给了就多一根，而且默认对半分", () => {
    render(withChat());
    expect(screen.getAllByRole("separator")).toHaveLength(3);
    expect(widthOf(chatBar())).toBe(DEFAULT_CHAT_PCT);
    // 两个 fr 因子加起来正好 100 —— 50/50 就是真的一半一半。
    const split = document.querySelector(".center-split") as HTMLElement;
    expect(split.style.gridTemplateColumns).toContain("50fr");
  });

  it("**左栏书架和右栏面板一个像素不动**（作者只要求对半分中间那块）", () => {
    render(panes());
    const before = screen.getByRole("main").style.gridTemplateColumns;
    cleanup();
    render(withChat());
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe(before);
  });

  it("方向键调得动：往左 = 写作助手变宽", () => {
    render(withChat());
    fireEvent.keyDown(chatBar(), { key: "ArrowLeft" });
    expect(widthOf(chatBar())).toBe(DEFAULT_CHAT_PCT + KEY_PCT_STEP);
    fireEvent.keyDown(chatBar(), { key: "ArrowRight" });
    expect(widthOf(chatBar())).toBe(DEFAULT_CHAT_PCT);
  });

  it("按到底停在两端，两边都还在（下限由 grid 的 minmax 兜）", () => {
    render(withChat());
    for (let i = 0; i < 200; i++) fireEvent.keyDown(chatBar(), { key: "ArrowLeft" });
    expect(widthOf(chatBar())).toBe(100);
    const split = document.querySelector(".center-split") as HTMLElement;
    // 拖到底 = 正文那半停在它的下限，不是消失。
    expect(split.style.gridTemplateColumns).toContain(`minmax(${MIN_CENTER}px, 0fr)`);
    expect(screen.getByText("中栏")).toBeInTheDocument();
  });

  it("双击复位回对半分", () => {
    render(withChat());
    fireEvent.keyDown(chatBar(), { key: "ArrowLeft" });
    fireEvent.doubleClick(chatBar());
    expect(widthOf(chatBar())).toBe(DEFAULT_CHAT_PCT);
  });

  it("拖完记住：关掉再打开还是那个比例", () => {
    const { unmount } = render(withChat());
    fireEvent.keyDown(chatBar(), { key: "ArrowLeft" });
    unmount();
    render(withChat());
    expect(widthOf(chatBar())).toBe(DEFAULT_CHAT_PCT + KEY_PCT_STEP);
  });
});

// ── 收起两侧栏（作者 2026-09-06，参照 Cursor）──────────────────────────────
describe("收起两侧栏", () => {
  const collapse = (side: "left" | "right") => usePaneCollapse.getState().toggle(side);

  beforeEach(() => {
    // store 是模块级的单例，收起状态会跨用例带过去。
    usePaneCollapse.setState({ left: false, right: false });
    globalThis.localStorage?.clear();
  });

  it("收起左栏 = 左栏和它那根分隔条一起不见，中栏右栏还在", () => {
    collapse("left");
    render(panes());
    expect(screen.queryByText("左栏")).toBeNull();
    // **分隔条也得走**：留一根 0 宽的在那儿，作者仍然能把收起来的栏拖回来，
    // 于是「收起」变成一个绕得过去的状态。
    expect(screen.queryByRole("separator", { name: "调整左栏宽度" })).toBeNull();
    expect(screen.getAllByRole("separator")).toHaveLength(1);
    expect(screen.getByText("中栏")).toBeInTheDocument();
    expect(screen.getByText("右栏")).toBeInTheDocument();
    // 那一栏的轨道从 grid 上真的消失了，不是被画成 0px。
    const tracks = screen.getByRole("main").style.gridTemplateColumns;
    expect(tracks).not.toContain(`${DEFAULT_LEFT}px`);
    expect(tracks).toContain(`${DEFAULT_RIGHT}px`);
  });

  it("收起右栏 —— 同一件事的另一边", () => {
    collapse("right");
    render(panes());
    expect(screen.queryByText("右栏")).toBeNull();
    expect(screen.queryByRole("separator", { name: "调整右栏宽度" })).toBeNull();
    expect(screen.getByText("左栏")).toBeInTheDocument();
    expect(screen.getByRole("main").style.gridTemplateColumns).not.toContain(`${DEFAULT_RIGHT}px`);
  });

  it("两栏都收起来时中栏还在 —— 不许出现一块什么都没有的屏幕", () => {
    collapse("left");
    collapse("right");
    render(panes());
    expect(screen.getByText("中栏")).toBeInTheDocument();
    expect(screen.queryAllByRole("separator")).toHaveLength(0);
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe("minmax(0, 1fr)");
  });

  it("🔴 收起再展开，回到**他拖成的那个宽度**，不是默认宽", () => {
    // 收起如果顺手把宽度改成 0/默认，作者展开时看到的是一栏他没拖过的宽度——
    // 而他按那颗按钮的意思只是「先挪开」。宽度存的是意图，收起不许碰它。
    render(panes());
    fireEvent.keyDown(leftBar(), { key: "ArrowRight" });
    const widened = widthOf(leftBar());
    expect(widened).toBe(DEFAULT_LEFT + KEY_STEP);

    cleanup();
    collapse("left");
    render(panes());
    expect(screen.queryByText("左栏")).toBeNull();

    cleanup();
    collapse("left"); // 再拨一次 = 展开
    render(panes());
    expect(widthOf(leftBar())).toBe(widened);
  });

  it("记住：重开工作台还是收着的", () => {
    collapse("right");
    // 重新读一遍存的那份（模拟下次打开）。
    const stored = JSON.parse(String(globalThis.localStorage?.getItem("nh.pane-collapsed.v1")));
    expect(stored).toEqual({ left: false, right: true });
  });

  it("存坏了一律当「没收起来」—— 读不回来的偏好不许把两栏藏起来", () => {
    // 作者会以为工作台坏了，而不会想到去顶栏找一颗按钮。
    globalThis.localStorage?.setItem("nh.pane-collapsed.v1", "{oops");
    render(panes());
    expect(screen.getByText("左栏")).toBeInTheDocument();
    expect(screen.getByText("右栏")).toBeInTheDocument();
  });
});
