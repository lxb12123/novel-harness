import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import {
  clampPaneWidths,
  readStoredWidths,
  writeStoredWidths,
  DEFAULT_LEFT,
  DEFAULT_RIGHT,
  DIVIDER_PX,
  KEY_STEP,
  MIN_CENTER,
  MIN_LEFT,
  MIN_RIGHT,
  type PaneWidths,
} from "../layout";

type Side = "left" | "right";

const LABEL: Record<Side, string> = { left: "调整左栏宽度", right: "调整右栏宽度" };

/**
 * 三栏骨架 + 两根可拖的分隔条。
 *
 * 栏宽是**作者的偏好**不是坐标，所以不进 `useCoords`（那个 store 只放坐标），
 * 而是本地 state + localStorage：换项目不该把拖好的版式重置掉。
 */
export function SplitPanes({
  left,
  center,
  right,
}: {
  left: ReactNode;
  center: ReactNode;
  right: ReactNode;
}) {
  const mainRef = useRef<HTMLElement>(null);
  const [widths, setWidths] = useState<PaneWidths>(readStoredWidths);
  const [dragging, setDragging] = useState<Side | null>(null);
  const [container, setContainer] = useState(0);

  // pointermove 的闭包只捕获到「按下那一刻」的 state，而拖动要连着改很多帧：
  // 用 ref 兜住最新值，别把 widths 塞进依赖数组去反复重建监听器。
  const widthsRef = useRef(widths);
  widthsRef.current = widths;

  const apply = useCallback((target: PaneWidths) => {
    const c = mainRef.current?.clientWidth ?? 0;
    setContainer(c);
    setWidths((prev) => {
      const next = clampPaneWidths(target, c);
      // 夹到边界之后每一帧都是同一组数：返回 prev 让 React 停下来，别空转重渲染。
      return next.left === prev.left && next.right === prev.right ? prev : next;
    });
  }, []);

  // 窗口变窄时重新夹一遍。不做这件事的话，把窗口拖小 = 两侧的固定宽度把中栏挤没。
  useEffect(() => {
    const onResize = () => apply(widthsRef.current);
    window.addEventListener("resize", onResize);
    onResize(); // 首帧量一次：state 的初值来自 localStorage，还没跟当前窗口对过账
    return () => window.removeEventListener("resize", onResize);
  }, [apply]);

  // 拖动中不写盘：pointermove 是 60fps，往 localStorage 灌同一个东西是白费。
  // 松手时 dragging 变回 null，这个 effect 再跑一次，落的就是最终值。
  useEffect(() => {
    if (dragging) return;
    writeStoredWidths(widths);
  }, [widths, dragging]);

  const reset = useCallback(
    (side: Side) => {
      const w = widthsRef.current;
      apply(side === "left" ? { ...w, left: DEFAULT_LEFT } : { ...w, right: DEFAULT_RIGHT });
    },
    [apply],
  );

  const nudge = useCallback(
    (side: Side, delta: number) => {
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
      const onMove = (ev: PointerEvent) => {
        const d = ev.clientX - startX;
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

  // 上限只在量到容器宽之后才是已知的；不知道就不报 aria-valuemax，别编一个数出来。
  const avail = container > 0 ? container - 2 * DIVIDER_PX : 0;
  const maxLeft = avail > 0 ? Math.max(MIN_LEFT, avail - MIN_CENTER - MIN_RIGHT) : undefined;
  const maxRight = avail > 0 ? Math.max(MIN_RIGHT, avail - MIN_CENTER - widths.left) : undefined;

  const divider = (side: Side, value: number, min: number, max: number | undefined) => (
    <div
      className="pane-divider"
      role="separator"
      tabIndex={0}
      aria-orientation="vertical"
      aria-label={LABEL[side]}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      data-side={side}
      data-dragging={dragging === side ? "true" : undefined}
      title="拖动改变宽度，双击恢复默认"
      onPointerDown={(e) => startDrag(side, e)}
      onKeyDown={(e) => onKeyDown(side, e)}
      onDoubleClick={() => reset(side)}
    />
  );

  return (
    <main
      ref={mainRef}
      style={{
        // 中栏必须是 minmax(0,1fr)：裸 1fr 的下限是 min-content，编辑器会撑住不让拖窄。
        gridTemplateColumns: `${widths.left}px ${DIVIDER_PX}px minmax(0, 1fr) ${DIVIDER_PX}px ${widths.right}px`,
      }}
    >
      {left}
      {divider("left", widths.left, MIN_LEFT, maxLeft)}
      {center}
      {divider("right", widths.right, MIN_RIGHT, maxRight)}
      {right}
    </main>
  );
}
