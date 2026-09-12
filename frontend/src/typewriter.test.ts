import { describe, expect, it } from "vitest";
import { FRAME_MS, MIN_PACE, nextReveal, pace, revealCut, type Reveal } from "./typewriter";

// 流进编辑器的字匀速露出来（`typewriter.ts`）：这儿钉的是速度怎么算、每帧露多少。

/** 一直跑到露完，返回用了几帧和每帧露的字数。 */
function run(total: number, perFrame: number, fast = false): { frames: number; steps: number[] } {
  let state: Reveal = { revealed: 0, budget: 0 };
  const steps: number[] = [];
  let frames = 0;
  while (state.revealed < total && frames < 10_000) {
    const next = nextReveal(state, total, perFrame, fast);
    steps.push(next.revealed - state.revealed);
    state = next;
    frames++;
  }
  return { frames, steps };
}

describe("速度跟着来的速度走", () => {
  it("一片都没来过 / 太久没来：最慢那一档（每秒三十个字）", () => {
    expect(pace([], 10_000)).toBe(MIN_PACE);
    expect(pace([{ t: 0, n: 300 }], 10_000)).toBe(MIN_PACE);
  });

  it("最近两秒来了多少字，就按那个速度露（略快一点点，积压才消得掉）", () => {
    // 每 100ms 来 5 个字 = 每秒 50 字 ≈ 每帧 0.83 字。
    const arrivals = Array.from({ length: 20 }, (_, i) => ({ t: i * 100, n: 5 }));
    const perFrame = pace(arrivals, 2000);
    expect(perFrame).toBeGreaterThan((50 / 1000) * FRAME_MS);
    expect(perFrame).toBeLessThan((50 / 1000) * FRAME_MS * 1.3);
  });

  it("第一片刚到时按至少半个窗口算——一片 600 字不能算成每帧几百字", () => {
    const perFrame = pace([{ t: 1000, n: 600 }], 1001);
    expect(perFrame).toBeLessThan(15);
  });
});

describe("每帧露多少", () => {
  it("每帧半个字时攒两帧露一个：**没有一帧跳一大段**", () => {
    const { steps } = run(30, 0.5);
    expect(Math.max(...steps)).toBe(1);
    expect(steps.filter((s) => s === 1)).toHaveLength(30);
  });

  it("来得快就露得快，但一帧只露那么多——一段 600 字铺开成几十帧，不是一下全出来", () => {
    const { frames, steps } = run(600, 10);
    expect(frames).toBeGreaterThanOrEqual(50);
    expect(Math.max(...steps)).toBeLessThanOrEqual(10);
  });

  it("积压超过三秒的量时额外加速，不让屏幕越落越远", () => {
    // 每帧 1 字的速度下积压 900（15 秒的量）：不加速要 900 帧，加速后远少于它。
    const { frames } = run(900, 1);
    expect(frames).toBeLessThan(300);
  });

  it("收尾（流已经收场）：每帧至少十二个字，十几帧内露完", () => {
    const { frames, steps } = run(600, 0.5, true);
    expect(frames).toBeLessThanOrEqual(15);
    expect(Math.min(...steps.slice(0, -1))).toBeGreaterThanOrEqual(12);
  });

  it("追平了就停在总长上，余额清零", () => {
    expect(nextReveal({ revealed: 3, budget: 0.7 }, 3, 1, false)).toEqual({ revealed: 3, budget: 0 });
    expect(nextReveal({ revealed: 5, budget: 0 }, 3, 1, false)).toEqual({ revealed: 3, budget: 0 });
  });

  it("不切在代理对中间", () => {
    const text = "雪😀落";
    // "😀" 占两个 code unit（下标 1、2）；切在 2 就是切在它中间。
    expect(revealCut(text, 2)).toBe(1);
    expect(revealCut(text, 3)).toBe(3);
    expect(revealCut(text, 0)).toBe(0);
    expect(revealCut(text, 99)).toBe(text.length);
  });
});
