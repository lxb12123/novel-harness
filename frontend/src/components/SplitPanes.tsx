import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
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
  KEY_PCT_STEP,
  KEY_STEP,
  MIN_CENTER,
  MIN_CHAT,
  MIN_LEFT,
  MIN_RIGHT,
  type PaneWidths,
} from "../layout";
import { useLanguage } from "../language";
import { usePaneCollapse } from "../paneCollapse";

type Side = "left" | "right" | "chat";

const LABEL: Record<Side, { zh: string; en: string }> = {
  left: { zh: "调整左栏宽度", en: "Resize the left column" },
  right: { zh: "调整右栏宽度", en: "Resize the right column" },
  chat: {
    zh: "调整正文和写作助手的分界",
    en: "Resize the divide between the text and the writing assistant",
  },
};

/**
 * 三栏骨架 + 可拖的分隔条。**中栏在开着写作助手时再分一次**（作者的原话：
 * 「文章那块对半分，左边是文章右边是 agent」），左栏书架和右栏面板一个像素不动。
 *
 * 栏宽是**作者的偏好**不是坐标，所以不进 `useCoords`（那个 store 只放坐标），
 * 而是本地 state + localStorage：换项目不该把拖好的版式重置掉。
 *
 * ── 「作者拖成什么样」和「这一刻画成什么样」是两个数 ─────────────────────────
 *
 * 2026-08-11 加中栏那一根时改的：state 里存的是**作者的意图**，渲染前才按当前容器宽
 * （和「对话面板开着没开着」）夹一遍。此前两者是同一个数，于是**开一次对话面板
 * 就会把他拖好的左右两栏永久压到下限**——中栏的下限从 320 涨到 606，夹完的结果被
 * 当成新意图存回 localStorage，关掉面板也回不来了。窗口拖窄再拖宽是同一个故障，
 * 只是那一版没人碰到过。
 */
export function SplitPanes({
  left,
  center,
  chat,
  right,
}: {
  left: ReactNode;
  center: ReactNode;
  /** 中栏右半边（写作助手）。**不给就完全是原来那三栏**，连那根分隔条都不渲染。 */
  chat?: ReactNode;
  right: ReactNode;
}) {
  const language = useLanguage((s) => s.language);
  const mainRef = useRef<HTMLElement>(null);
  const centerRef = useRef<HTMLDivElement>(null);
  const [widths, setWidths] = useState<PaneWidths>(readStoredWidths);
  const [chatPct, setChatPct] = useState<number>(readStoredChatPct);
  const [dragging, setDragging] = useState<Side | null>(null);
  const [container, setContainer] = useState(0);
  // 收起来的那一栏**连同它那根分隔条一起不渲染**（不是宽度设 0）：留着一条 0 宽的
  // 轨道，作者仍然能摸到那根分隔条把它拖回来，于是「收起」变成了一个可以被绕过的
  // 状态；而且读屏还会念到一个 `aria-valuenow=0` 的分隔条。
  // **宽度本身一个字节都不动**（`widths` 存的是意图），所以展开就回到他拖成的样子。
  const leftCollapsed = usePaneCollapse((s) => s.left);
  const rightCollapsed = usePaneCollapse((s) => s.right);
  const visible = useMemo(
    () => ({ left: !leftCollapsed, right: !rightCollapsed }),
    [leftCollapsed, rightCollapsed],
  );

  const splitCenter = chat !== undefined && chat !== null && chat !== false;
  const floor = centerFloor(splitCenter);

  // pointermove 的闭包只捕获到「按下那一刻」的 state，而拖动要连着改很多帧：
  // 用 ref 兜住最新值，别把 widths 塞进依赖数组去反复重建监听器。
  const widthsRef = useRef(widths);
  widthsRef.current = widths;
  const chatRef = useRef(chatPct);
  chatRef.current = chatPct;

  const apply = useCallback((target: PaneWidths) => {
    setContainer(mainRef.current?.clientWidth ?? 0);
    // 存的是**意图**：只过一遍下限和 NaN，不按当前容器宽夹。夹在下面渲染时做。
    setWidths((prev) => {
      const next = clampPaneWidths(target, 0);
      return next.left === prev.left && next.right === prev.right ? prev : next;
    });
  }, []);

  // 窗口变窄时重新量一遍。不做这件事的话，把窗口拖小 = 两侧的固定宽度把中栏挤没。
  useEffect(() => {
    const onResize = () => setContainer(mainRef.current?.clientWidth ?? 0);
    window.addEventListener("resize", onResize);
    onResize(); // 首帧量一次：state 的初值来自 localStorage，还没跟当前窗口对过账
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // 拖动中不写盘：pointermove 是 60fps，往 localStorage 灌同一个东西是白费。
  // 松手时 dragging 变回 null，这个 effect 再跑一次，落的就是最终值。
  useEffect(() => {
    if (dragging) return;
    writeStoredWidths(widths);
    writeStoredChatPct(chatPct);
  }, [widths, chatPct, dragging]);

  const reset = useCallback(
    (side: Side) => {
      if (side === "chat") return setChatPct(DEFAULT_CHAT_PCT);
      const w = widthsRef.current;
      apply(side === "left" ? { ...w, left: DEFAULT_LEFT } : { ...w, right: DEFAULT_RIGHT });
    },
    [apply],
  );

  const nudge = useCallback(
    (side: Side, delta: number) => {
      // 中栏那一根按**百分点**走，不按像素——它不需要量容器（见 `layout.ts` 的说明）。
      if (side === "chat") {
        return setChatPct((pct) => clampChatPct(pct - Math.sign(delta) * KEY_PCT_STEP));
      }
      const w = widthsRef.current;
      // 右栏是「从右边量」的：分隔条往右挪 = 右栏变窄，所以取反。
      apply(side === "left" ? { ...w, left: w.left + delta } : { ...w, right: w.right - delta });
    },
    [apply],
  );

  const stopRef = useRef<(() => void) | null>(null);
  useEffect(() => () => stopRef.current?.(), []); // 拖到一半被卸载：别把监听器和 body 上的类留在那

  const startDrag = useCallback(
    (side: Side, e: ReactPointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const startX = e.clientX;
      const start = widthsRef.current;
      const startPct = chatRef.current;
      // 中栏这一根要把像素位移换算成百分比，所以要量一次中栏有多宽。
      const centerPx = centerRef.current?.clientWidth ?? 0;
      const onMove = (ev: PointerEvent) => {
        const d = ev.clientX - startX;
        if (side === "chat") {
          setChatPct(chatPctAfterDrag(startPct, d, centerPx));
          return;
        }
        apply(
          side === "left" ? { ...start, left: start.left + d } : { ...start, right: start.right - d },
        );
      };
      const stop = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", stop);
        window.removeEventListener("pointercancel", stop);
        document.body.classList.remove("resizing");
        stopRef.current = null;
        setDragging(null);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
      // 光标整屏统一 + 禁掉选中：不加这句，横着拖会顺手把正文选中一大片。
      document.body.classList.add("resizing");
      stopRef.current = stop;
      setDragging(side);
    },
    [apply],
  );

  const onKeyDown = useCallback(
    (side: Side, e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        nudge(side, e.key === "ArrowRight" ? KEY_STEP : -KEY_STEP);
      } else if (e.key === "Home") {
        e.preventDefault();
        reset(side);
      }
    },
    [nudge, reset],
  );

  // 渲染用的那一对：作者的意图 + 这一刻的容器宽 + 中栏这一刻要保住多宽。
  const shown = useMemo(
    () => clampPaneWidths(widths, container, floor, visible),
    [widths, container, floor, visible],
  );

  // 上限只在量到容器宽之后才是已知的；不知道就不报 aria-valuemax，别编一个数出来。
  // 分隔条的根数跟着收起状态走——收了一栏就少一根，同 `clampPaneWidths` 里那一处。
  const dividers = (visible.left ? 1 : 0) + (visible.right ? 1 : 0);
  const avail = container > 0 ? container - dividers * DIVIDER_PX : 0;
  const maxLeft =
    avail > 0 ? Math.max(MIN_LEFT, avail - floor - (visible.right ? MIN_RIGHT : 0)) : undefined;
  const maxRight =
    avail > 0 ? Math.max(MIN_RIGHT, avail - floor - (visible.left ? shown.left : 0)) : undefined;

  const divider = (side: Side, value: number, min: number, max: number | undefined) => (
    <div
      className="pane-divider"
      role="separator"
      tabIndex={0}
      aria-orientation="vertical"
      aria-label={LABEL[side][language]}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      data-side={side}
      data-dragging={dragging === side ? "true" : undefined}
      title={
        language === "zh"
          ? "拖动改变宽度，双击恢复默认"
          : "Drag to resize, double-click to reset"
      }
      onPointerDown={(e) => startDrag(side, e)}
      onKeyDown={(e) => onKeyDown(side, e)}
      onDoubleClick={() => reset(side)}
    />
  );

  // 轨道按「这一刻有哪几栏」拼。**两栏都在时拼出来的字符串和收起功能出现之前
  // 一字不差**——那不是巧合，是这一版必须保住的东西：既有那几条钉着 grid 模板的
  // 测试是它唯一的看守。
  const tracks = [
    ...(visible.left ? [`${shown.left}px`, `${DIVIDER_PX}px`] : []),
    // 中栏必须是 minmax(0,1fr)：裸 1fr 的下限是 min-content，编辑器会撑住不让拖窄。
    "minmax(0, 1fr)",
    ...(visible.right ? [`${DIVIDER_PX}px`, `${shown.right}px`] : []),
  ].join(" ");

  return (
    <main ref={mainRef} style={{ gridTemplateColumns: tracks }}>
      {visible.left && left}
      {visible.left && divider("left", shown.left, MIN_LEFT, maxLeft)}
      {splitCenter ? (
        <div
          className="center-split"
          ref={centerRef}
          style={{
            // 两个 fr 因子加起来**正好 100**（都是整数），所以 50/50 就是真的对半分。
            // 像素下限交给 `minmax` 的第一个参数：容器不够时它自己让位，JS 这边
            // 不再算第二遍（第二份下限一定会和 CSS 这一份漂开）。
            gridTemplateColumns: `minmax(${MIN_CENTER}px, ${100 - chatPct}fr) ${DIVIDER_PX}px minmax(${MIN_CHAT}px, ${chatPct}fr)`,
          }}
        >
          {center}
          {divider("chat", chatPct, 0, 100)}
          {chat}
        </div>
      ) : (
        center
      )}
      {visible.right && divider("right", shown.right, MIN_RIGHT, maxRight)}
      {visible.right && right}
    </main>
  );
}
