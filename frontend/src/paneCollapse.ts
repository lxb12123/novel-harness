import { create } from "zustand";

// 两侧栏收起来没有（作者 2026-09-06：「两侧空间区域加个能收缩的按钮」，参照 Cursor）。
//
// ── 为什么是又一个 store，而不是塞进已有的哪一个 ────────────────────────────
//
// · **不进 `useCoords`**：那个 store 只放坐标（哪本书、第几章、看哪一页）。
//   「左栏收着没有」不是坐标，是这台设备上这个人的摆法——同 `SplitPanes.tsx`
//   顶上那句「栏宽是作者的偏好不是坐标」，这是同一条判据的第二个应用。
// · **不进 `SplitPanes` 的局部 state**：按钮在顶栏，栏在 `SplitPanes` 里，
//   两者是兄弟。局部 state 只能靠一路 props 从 `App` 穿下去，而 `App` 对
//   「栏收着没有」本来毫不关心。
// · **不进 `layout.ts`**：那份文件是**纯逻辑 + 存取**，一个 React 依赖都没有
//   （它的下限规则要能脱离渲染被单测钉死）。往里塞一个 zustand store 会把
//   那条性质弄丢。
//
// 于是照 `language.ts` 的形状另起一个：偏好 + localStorage + zustand。

export interface PaneCollapse {
  left: boolean;
  right: boolean;
}

export type CollapsibleSide = keyof PaneCollapse;

const STORAGE_KEY = "nh.pane-collapsed.v1";

const NONE: PaneCollapse = { left: false, right: false };

function readStored(): PaneCollapse {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return NONE;
    const parsed = JSON.parse(raw) as Partial<PaneCollapse>;
    // 只认真正的 `true`：存坏了 / 存了一半一律当「没收起来」。
    // **默认必须是展开**——一个读不回来的偏好把作者的两栏藏起来，
    // 他会以为工作台坏了，而不会想到去顶栏找一颗按钮。
    return { left: parsed.left === true, right: parsed.right === true };
  } catch {
    return NONE;
  }
}

function writeStored(value: PaneCollapse): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // 隐私模式下 setItem 会抛。存不下就算了，收一次栏不该因此崩掉（同 layout.ts）。
  }
}

interface PaneCollapseStore extends PaneCollapse {
  toggle: (side: CollapsibleSide) => void;
}

export const usePaneCollapse = create<PaneCollapseStore>((set, get) => ({
  ...readStored(),
  toggle: (side) => {
    const next = { left: get().left, right: get().right, [side]: !get()[side] } as PaneCollapse;
    writeStored(next);
    set(next);
  },
}));
