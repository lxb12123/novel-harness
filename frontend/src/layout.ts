// 三栏宽度的纯逻辑：夹取 + 存取。**这里不碰 DOM**，好让下限的边界情况能被单测钉死
// （拖动本身在 jsdom 里量不出来——getBoundingClientRect 全是 0——所以规则必须能脱离渲染验证）。

/** 三栏各自的下限。作者能把分隔条拖到任意位置，**但拖不到某一栏只剩一条边框**：
 *  「拉过头就什么都看不见」是这种可拖分栏最常见的废墟形态，所以下限写死，不做成设置项。 */
// 左栏的下限跟着书名行走：那一行里除了书名还有折叠箭头、页面图标、「章目录」和 ⋯，
// 固定部分就吃掉约 120px。再窄下去书名只剩一个省略号，那一行就等于没有信息了。
export const MIN_LEFT = 190;
export const MIN_CENTER = 320;
export const MIN_RIGHT = 260;

/** 240 是 UI 设计稿一开始给左栏的宽度；书架进来之后 210 确实不够放。 */
export const DEFAULT_LEFT = 240;
export const DEFAULT_RIGHT = 400;

/** 分隔条自己占一列（这是可抓的宽度，画出来的线只有 1px）。算可用宽度时两根都要减掉。 */
export const DIVIDER_PX = 6;
/** 方向键一下调多少像素。 */
export const KEY_STEP = 16;

export interface PaneWidths {
  /** 左栏像素宽。 */
  left: number;
  /** 右栏像素宽。中栏是剩下的，不存。 */
  right: number;
}

export const DEFAULT_WIDTHS: PaneWidths = { left: DEFAULT_LEFT, right: DEFAULT_RIGHT };

/** 下限永远赢：窗口窄到连三栏下限都塞不下时上限会低于下限，这时宁可整行横向溢出，
 *  也不要算出负宽或零宽的栏（那等于把一栏彻底删掉，作者找不回来）。 */
function fit(v: number, min: number, max: number): number {
  if (!Number.isFinite(v)) return min;
  return Math.round(Math.min(Math.max(v, min), Math.max(min, max)));
}

/**
 * 把一对宽度夹进合法范围。
 *
 * `container` 是 `<main>` 的实际像素宽；**拿不到时传 0**（首帧还没量、或 jsdom 里恒为 0），
 * 那时只保下限、不算上限——总比按 0 宽度把两侧压到最小要诚实。
 */
export function clampPaneWidths(w: PaneWidths, container: number): PaneWidths {
  const avail = container > 0 ? container - 2 * DIVIDER_PX : 0;
  const maxLeft = avail > 0 ? avail - MIN_CENTER - MIN_RIGHT : Infinity;
  const left = fit(w.left, MIN_LEFT, maxLeft);
  // 右栏的上限要用**夹完的** left 算：否则两侧各自合法、加起来仍能把中栏挤没。
  const maxRight = avail > 0 ? avail - MIN_CENTER - left : Infinity;
  return { left, right: fit(w.right, MIN_RIGHT, maxRight) };
}

const STORAGE_KEY = "nh.pane-widths.v1";

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

/** 读回上次拖成的宽度。存坏了 / 存了一半 / 隐私模式读不到，一律回默认——
 *  一行坏 JSON 不该让工作台打不开。 */
export function readStoredWidths(): PaneWidths {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_WIDTHS;
    const parsed = JSON.parse(raw) as Partial<PaneWidths>;
    return clampPaneWidths(
      { left: num(parsed.left, DEFAULT_LEFT), right: num(parsed.right, DEFAULT_RIGHT) },
      0,
    );
  } catch {
    return DEFAULT_WIDTHS;
  }
}

export function writeStoredWidths(w: PaneWidths): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(w));
  } catch {
    // 隐私模式下 setItem 会抛。存不下就算了，拖宽度不该因此崩掉。
  }
}
