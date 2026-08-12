import { describe, expect, it } from "vitest";
import {
  clampPaneWidths,
  readStoredWidths,
  writeStoredWidths,
  DEFAULT_LEFT,
  DEFAULT_RIGHT,
  DIVIDER_PX,
  MIN_CENTER,
  MIN_LEFT,
  MIN_RIGHT,
} from "./layout";

/** 容器宽 → 三栏能分掉的宽（两根分隔条各占一列）。 */
const avail = (container: number) => container - 2 * DIVIDER_PX;

describe("三栏宽度的下限", () => {
  it("容器够宽时，作者拖成什么样就是什么样", () => {
    expect(clampPaneWidths({ left: 300, right: 500 }, 1600)).toEqual({ left: 300, right: 500 });
  });

  it("左栏拖到 0 会停在下限，不会变成一条缝", () => {
    expect(clampPaneWidths({ left: 0, right: 400 }, 1600).left).toBe(MIN_LEFT);
  });

  it("右栏拖到 0 会停在下限", () => {
    expect(clampPaneWidths({ left: 210, right: 0 }, 1600).right).toBe(MIN_RIGHT);
  });

  it("左栏往右拖过头时，中栏保得住自己的下限", () => {
    const w = clampPaneWidths({ left: 9999, right: MIN_RIGHT }, 1200);
    expect(avail(1200) - w.left - w.right).toBeGreaterThanOrEqual(MIN_CENTER);
  });

  it("右栏往左拖过头时，中栏同样保得住", () => {
    const w = clampPaneWidths({ left: MIN_LEFT, right: 9999 }, 1200);
    expect(avail(1200) - w.left - w.right).toBeGreaterThanOrEqual(MIN_CENTER);
  });

  it("两侧各自看着合法、加起来把中栏挤没 —— 也要拦住", () => {
    // 这是「先夹 left 再用夹完的 left 去算 right 上限」那行代码的理由：
    // 各夹各的会得到 left=500 / right=500，中栏只剩 188。
    const w = clampPaneWidths({ left: 500, right: 500 }, 1200);
    expect(avail(1200) - w.left - w.right).toBeGreaterThanOrEqual(MIN_CENTER);
  });

  it("窗口窄到连三栏下限都塞不下时，下限赢（宁可溢出也不给负宽）", () => {
    const w = clampPaneWidths({ left: DEFAULT_LEFT, right: DEFAULT_RIGHT }, 500);
    expect(w).toEqual({ left: MIN_LEFT, right: MIN_RIGHT });
  });

  it("量不到容器宽（首帧 / jsdom）时只保下限，不按 0 宽把两侧压扁", () => {
    expect(clampPaneWidths({ left: 300, right: 500 }, 0)).toEqual({ left: 300, right: 500 });
  });

  it("NaN 进来不会变成 NaN 出去", () => {
    expect(clampPaneWidths({ left: Number.NaN, right: Number.NaN }, 1600)).toEqual({
      left: MIN_LEFT,
      right: MIN_RIGHT,
    });
  });
});

// 存储替身 + 每个 test 前清空都在 src/test/setup.ts。
describe("宽度存取", () => {
  it("没存过就是默认值", () => {
    expect(readStoredWidths()).toEqual({ left: DEFAULT_LEFT, right: DEFAULT_RIGHT });
  });

  it("存了能原样读回来", () => {
    writeStoredWidths({ left: 260, right: 320 });
    expect(readStoredWidths()).toEqual({ left: 260, right: 320 });
  });

  it("存坏了回默认 —— 一行坏 JSON 不该让工作台打不开", () => {
    localStorage.setItem("nh.pane-widths.v1", "{ 这不是 JSON");
    expect(readStoredWidths()).toEqual({ left: DEFAULT_LEFT, right: DEFAULT_RIGHT });
  });

  it("只存了一半时，缺的那栏回默认值而不是回下限", () => {
    localStorage.setItem("nh.pane-widths.v1", JSON.stringify({ left: 260 }));
    expect(readStoredWidths()).toEqual({ left: 260, right: DEFAULT_RIGHT });
  });

  it("读回来也过一遍下限：手改过 localStorage 也拖不出一条缝", () => {
    localStorage.setItem("nh.pane-widths.v1", JSON.stringify({ left: 1, right: 1 }));
    expect(readStoredWidths()).toEqual({ left: MIN_LEFT, right: MIN_RIGHT });
  });
});
