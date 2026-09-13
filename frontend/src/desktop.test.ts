import { afterEach, describe, expect, it, vi } from "vitest";
import { dragWindow, isDesktop } from "./desktop";

// 桌面壳里顶栏就是标题条（作者 2026-09-13：三颗窗口按钮落到顶栏左边、白色那一条去掉）。
// 这儿钉两件事：怎么认出自己在壳里，以及顶栏空白处按下鼠标才叫壳拖窗口。

afterEach(() => {
  document.documentElement.classList.remove("desktop");
  delete window.pywebview;
});

describe("桌面壳", () => {
  it("地址带 ?desktop=1 才算开在壳里", () => {
    expect(isDesktop("?desktop=1")).toBe(true);
    expect(isDesktop("")).toBe(false);
    expect(isDesktop("?other=1")).toBe(false);
  });

  it("空白处按下 = 叫壳拖窗口；按在按钮上、右键、不在壳里都不叫", () => {
    const drag = vi.fn(() => Promise.resolve());
    window.pywebview = { api: { drag } };
    const header = document.createElement("header");
    const button = document.createElement("button");
    header.appendChild(button);
    document.body.appendChild(header);

    // 不在壳里：浏览器里这条顶栏不是标题条
    expect(dragWindow({ button: 0, target: header })).toBe(false);
    document.documentElement.classList.add("desktop");
    expect(dragWindow({ button: 0, target: header })).toBe(true);
    expect(drag).toHaveBeenCalledTimes(1);
    // 按在按钮上是点，不是拖
    expect(dragWindow({ button: 0, target: button })).toBe(false);
    // 右键不拖
    expect(dragWindow({ button: 2, target: header })).toBe(false);
    expect(drag).toHaveBeenCalledTimes(1);
    header.remove();
  });
});
