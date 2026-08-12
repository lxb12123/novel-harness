import { describe, expect, it } from "vitest";
import {
  centerFloor,
  chatPctAfterDrag,
  clampChatPct,
  clampPaneWidths,
  readStoredChatPct,
  readStoredWidths,
  writeStoredChatPct,
  writeStoredWidths,
  DEFAULT_CHAT_PCT,
  DEFAULT_LEFT,
  DEFAULT_RIGHT,
  DIVIDER_PX,
  MIN_CENTER,
  MIN_CHAT,
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

// ── 中栏再分一次：左边正文、右边写作助手 ─────────────────────────────────────

describe("开着写作助手时，中栏的下限", () => {
  it("关着 = 中栏就是编辑器那一块", () => {
    expect(centerFloor(false)).toBe(MIN_CENTER);
  });

  it("开着 = 两块加一根分隔条 —— **少算这一段，正文那半会被挤成一条缝**", () => {
    expect(centerFloor(true)).toBe(MIN_CENTER + DIVIDER_PX + MIN_CHAT);
  });

  it("窗口够宽时，开合都不动作者拖好的左右两栏", () => {
    const dragged = { left: 300, right: 500 };
    expect(clampPaneWidths(dragged, 1800, centerFloor(true))).toEqual(dragged);
  });

  it("窗口不够宽时，先让左右两栏，保住中栏那两块", () => {
    const w = clampPaneWidths({ left: 300, right: 500 }, 1200, centerFloor(true));
    const avail = 1200 - 2 * DIVIDER_PX;
    expect(avail - w.left - w.right).toBeGreaterThanOrEqual(centerFloor(true));
  });

  it("**夹是渲染时的事，不是存进去的事**（这一条钉住那个分工）", () => {
    // 同一份意图，两种夹法各得各的。SplitPanes 存的是意图、渲染前才夹——
    // 存夹完的结果 = 开一次写作助手就把作者拖好的版式永久压到下限，关掉也回不来。
    const intent = { left: 300, right: 500 };
    expect(clampPaneWidths(intent, 0)).toEqual(intent);
    expect(clampPaneWidths(intent, 1200, centerFloor(true))).not.toEqual(intent);
  });
});

describe("中栏那根分隔条：百分比", () => {
  it("默认对半分 —— 作者要的原话", () => {
    expect(DEFAULT_CHAT_PCT).toBe(50);
  });

  it("夹进 0–100，超出的停在两端", () => {
    expect(clampChatPct(-30)).toBe(0);
    expect(clampChatPct(160)).toBe(100);
    expect(clampChatPct(37.4)).toBe(37);
  });

  it("NaN 进来不会变成 NaN 出去（同左右两栏那条）", () => {
    expect(clampChatPct(Number.NaN)).toBe(DEFAULT_CHAT_PCT);
  });

  it("往左拖，写作助手变宽（它和右栏一样是从右边量的）", () => {
    // 中栏 806 → 可分 800；往左 200 像素 = 四分之一。
    expect(chatPctAfterDrag(50, -200, 806)).toBe(75);
    expect(chatPctAfterDrag(50, 200, 806)).toBe(25);
  });

  it("**量不到中栏宽就原样返回** —— 不按一个编出来的容器宽把分隔条弹到别处", () => {
    expect(chatPctAfterDrag(40, -300, 0)).toBe(40);
  });

  it("拖到底停在两端，不会绕回去", () => {
    expect(chatPctAfterDrag(50, -99999, 806)).toBe(100);
    expect(chatPctAfterDrag(50, 99999, 806)).toBe(0);
  });
});

describe("中栏那根的存取（**另一个键**：它是百分比不是像素）", () => {
  it("没存过就是对半分", () => {
    expect(readStoredChatPct()).toBe(DEFAULT_CHAT_PCT);
  });

  it("存了能读回来", () => {
    writeStoredChatPct(72);
    expect(readStoredChatPct()).toBe(72);
  });

  it("存坏了回默认 —— 一行坏 JSON 不该让工作台打不开", () => {
    localStorage.setItem("nh.chat-pane.v1", "{ 这不是 JSON");
    expect(readStoredChatPct()).toBe(DEFAULT_CHAT_PCT);
  });

  it("**它不和左右两栏挤同一个键** —— 存了它，那两栏一个字节都没变", () => {
    writeStoredWidths({ left: 260, right: 320 });
    writeStoredChatPct(80);
    expect(readStoredWidths()).toEqual({ left: 260, right: 320 });
  });
});
