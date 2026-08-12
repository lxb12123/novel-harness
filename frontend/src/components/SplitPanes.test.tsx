import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DEFAULT_LEFT, DEFAULT_RIGHT, KEY_STEP, MIN_LEFT, MIN_RIGHT } from "../layout";
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
