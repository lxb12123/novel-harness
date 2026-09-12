import { useEffect, useRef, useState } from "react";
import { nextReveal, pace, revealCut, type Arrival, type Reveal } from "../typewriter";

/**
 * 把一段**只会变长**的文本按它来的速度匀速露出来（正在写的那一稿流进编辑器时用，
 * `typewriter.ts`）。
 *
 * `active` 为假时什么都不露、位置归零——下一次开始从头打。`fast` 为真时加速收尾
 * （流已经收场，落盘那一版马上就到）。
 *
 * 每一帧一次 `setState`；追平之后值不变，React 不会为同一个值重画。
 */
export function useTypewriter(
  target: string,
  active: boolean,
  fast: boolean,
): { shown: string; caughtUp: boolean } {
  // 露到哪儿是 state（画在屏幕上），不足一个字的余额是 ref（每帧都变，不值得重画）。
  const [revealed, setRevealed] = useState(0);
  const reveal = useRef<Reveal>({ revealed: 0, budget: 0 });
  const targetRef = useRef(target);
  const fastRef = useRef(fast);
  fastRef.current = fast;
  // 最近来了多少字（算速度用）。`target` 每变长一次记一笔。
  const arrivals = useRef<Arrival[]>([]);
  if (target.length > targetRef.current.length) {
    arrivals.current.push({ t: Date.now(), n: target.length - targetRef.current.length });
  }
  targetRef.current = target;

  useEffect(() => {
    if (!active) {
      reveal.current = { revealed: 0, budget: 0 };
      setRevealed(0);
      arrivals.current = [];
      return;
    }
    // jsdom 不一定有 requestAnimationFrame（`pretendToBeVisual` 关着时）：退到 16ms 一帧。
    const schedule =
      typeof requestAnimationFrame === "function"
        ? (fn: () => void) => requestAnimationFrame(fn)
        : (fn: () => void) => setTimeout(fn, 16) as unknown as number;
    const unschedule =
      typeof cancelAnimationFrame === "function"
        ? (id: number) => cancelAnimationFrame(id)
        : (id: number) => clearTimeout(id);
    let handle = 0;
    const tick = () => {
      const now = Date.now();
      const perFrame = pace(arrivals.current, now);
      const next = nextReveal(reveal.current, targetRef.current.length, perFrame, fastRef.current);
      const moved = next.revealed !== reveal.current.revealed;
      reveal.current = next;
      if (moved) setRevealed(next.revealed);
      handle = schedule(tick);
    };
    handle = schedule(tick);
    return () => unschedule(handle);
  }, [active]);

  const at = revealCut(target, Math.min(revealed, target.length));
  return { shown: active ? target.slice(0, at) : "", caughtUp: !active || at >= target.length };
}
