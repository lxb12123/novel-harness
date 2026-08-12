// 三栏宽度的纯逻辑：夹取 + 存取。**这里不碰 DOM**，好让下限的边界情况能被单测钉死
// （拖动本身在 jsdom 里量不出来——getBoundingClientRect 全是 0——所以规则必须能脱离渲染验证）。

/** 三栏各自的下限。作者能把分隔条拖到任意位置，**但拖不到某一栏只剩一条边框**：
 *  「拉过头就什么都看不见」是这种可拖分栏最常见的废墟形态，所以下限写死，不做成设置项。 */
// 左栏的下限跟着书名行走：那一行里除了书名还有折叠箭头、页面图标、「章目录」和 ⋯，
// 固定部分就吃掉约 120px。再窄下去书名只剩一个省略号，那一行就等于没有信息了。
export const MIN_LEFT = 190;
export const MIN_CENTER = 320;
export const MIN_RIGHT = 260;

/** 对话面板（模式二）的下限。中栏对半分之后它和正文各占一半，
 *  **但拖到底时两边都不许只剩一条缝**——同上面那三条，这是同一条纪律的第四个应用。 */
export const MIN_CHAT = 280;

/** 240 是 UI 设计稿一开始给左栏的宽度；书架进来之后 210 确实不够放。 */
export const DEFAULT_LEFT = 240;
export const DEFAULT_RIGHT = 400;

/** 分隔条自己占一列（这是可抓的宽度，画出来的线只有 1px）。算可用宽度时两根都要减掉。 */
export const DIVIDER_PX = 6;
/** 方向键一下调多少像素。 */
export const KEY_STEP = 16;

// ── 中栏那根分隔条：**存百分比，不存像素** ──────────────────────────────────
//
// 左右两栏存的是像素（它们是「这一栏该多宽」），而中栏这一根要兑现的是作者的原话
// ——**「文章那块对半分」**。对半分是一个比例，存成像素的话窗口一变宽它就不再是一半，
// 而且首帧量不到容器宽（jsdom 里恒为 0）的时候根本算不出「一半是多少像素」。
//
// 比例还白捡一件事：**方向键不需要量任何东西**（百分比无量纲），
// 所以那条交互在 jsdom 里是可测的，只有拖动那一条要真浏览器。

/** 对话面板占中栏的百分比。50 = 对半分。 */
export const DEFAULT_CHAT_PCT = 50;
/** 方向键一下调几个百分点。 */
export const KEY_PCT_STEP = 2;

/** 开着对话面板时中栏要同时装下正文和它 —— 下限跟着变宽。
 *
 *  **不做这件事的后果**：中栏的下限还是一块的（320），于是作者一开对话面板，
 *  正文那半就被挤成一条缝——正是这份文件开头那句「拉过头就什么都看不见」的废墟形态，
 *  只不过这次是我们自己造出来的，作者连拖都没拖。 */
export function centerFloor(chatOpen: boolean): number {
  return chatOpen ? MIN_CENTER + DIVIDER_PX + MIN_CHAT : MIN_CENTER;
}

/** 把百分比夹进 [0, 100]。
 *
 *  **两侧的像素下限不在这里兜**，在 grid 的 `minmax(下限, N fr)` 里兜：
 *  拖到 0 的含义是「正文那半停在它的下限」，不是「正文没了」。判据只有一处，
 *  而 CSS 那一处是渲染时真的按容器宽算的——JS 这边再算一遍就是第二份会漂的下限。 */
export function clampChatPct(v: number): number {
  if (!Number.isFinite(v)) return DEFAULT_CHAT_PCT;
  return Math.round(Math.min(Math.max(v, 0), 100));
}

/** 拖动之后的百分比。**往左拖 = 对话变宽**（它和右栏一样是从右边量的）。
 *
 *  `centerPx` 量不到（首帧 / jsdom）时**原样返回**：不编一个容器宽出来，
 *  宁可这一次拖动不生效，也不要按一个假宽度把分隔条弹到别处去。 */
export function chatPctAfterDrag(startPct: number, dx: number, centerPx: number): number {
  const avail = centerPx - DIVIDER_PX;
  if (!(avail > 0)) return clampChatPct(startPct);
  return clampChatPct(startPct - (dx / avail) * 100);
}

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
 *
 * `minCenter` 是中栏要保住的下限。开着对话面板时它变宽（`centerFloor`）——
 * **默认值留着**是因为「中栏就是编辑器」仍然是最常见的那一档，而且既有调用方不必都改。
 */
export function clampPaneWidths(
  w: PaneWidths,
  container: number,
  minCenter: number = MIN_CENTER,
): PaneWidths {
  const avail = container > 0 ? container - 2 * DIVIDER_PX : 0;
  const maxLeft = avail > 0 ? avail - minCenter - MIN_RIGHT : Infinity;
  const left = fit(w.left, MIN_LEFT, maxLeft);
  // 右栏的上限要用**夹完的** left 算：否则两侧各自合法、加起来仍能把中栏挤没。
  const maxRight = avail > 0 ? avail - minCenter - left : Infinity;
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

/** 中栏那根分开存，**不并进上面那个键**：它是另一种量（百分比不是像素），
 *  而且只有开着对话面板时才存在。挤进同一个 JSON 会让「读回一半」那几条既有行为
 *  多出一种含义（缺 `chat` 到底是没存过还是旧版本存的），而那正是这三条读写函数
 *  一直在避免的东西。 */
const CHAT_KEY = "nh.chat-pane.v1";

export function readStoredChatPct(): number {
  try {
    const raw = globalThis.localStorage?.getItem(CHAT_KEY);
    if (!raw) return DEFAULT_CHAT_PCT;
    return clampChatPct(Number(JSON.parse(raw)));
  } catch {
    return DEFAULT_CHAT_PCT;
  }
}

export function writeStoredChatPct(pct: number): void {
  try {
    globalThis.localStorage?.setItem(CHAT_KEY, JSON.stringify(clampChatPct(pct)));
  } catch {
    // 同上：存不下不该让拖动崩掉。
  }
}
