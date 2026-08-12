import { describe, expect, it, vi } from "vitest";
import { ensureSummaries, MAX_POLLS, SummaryPrepError, type PrepDeps } from "./summaryPrep";
import type { AutopilotStatus } from "./api/types";

function status(chapter: number, over: Partial<AutopilotStatus> = {}): AutopilotStatus {
  return {
    chapter,
    summary_ready: false,
    extraction_ready: false,
    running: false,
    ...over,
  };
}

/** 默认依赖：什么都问不到、总结一叫就成、不用真等。 */
function deps(over: { status?: PrepDeps["status"]; summarize?: PrepDeps["summarize"] } = {}) {
  return {
    status: vi.fn(over.status ?? (async () => null)),
    summarize: vi.fn(over.summarize ?? (async () => undefined)),
    onProgress: vi.fn<(p: { chapter: number; done: number; total: number }) => void>(),
    wait: vi.fn(async () => undefined),
  };
}

describe("起草前补齐前置资料", () => {
  it("缺几章就补几章，并且逐章报进度", async () => {
    const d = deps();
    await ensureSummaries([2, 5], d);

    expect(d.summarize.mock.calls.map(([n]) => n)).toEqual([2, 5]);
    expect(d.onProgress.mock.calls.map(([p]) => p)).toEqual([
      { chapter: 2, done: 0, total: 2 },
      { chapter: 5, done: 1, total: 2 },
    ]);
  });

  it("后台已经补好的那一章不再付一次钱", async () => {
    const d = deps({ status: vi.fn(async (n: number) => status(n, { summary_ready: true })) });
    await ensureSummaries([2], d);
    expect(d.summarize).not.toHaveBeenCalled();
  });

  it("后台正在整理就等它跑完，而不是自己再发一次同样的请求", async () => {
    // 作者刚从第 2 章切走 → 后台正在总结它 → 他马上按了起草。两条都跑 = 同一次
    // 模型调用付两遍钱。
    let polls = 0;
    const d = deps({
      status: vi.fn(async (n: number) => {
        polls += 1;
        return polls < 3 ? status(n, { running: true }) : status(n, { summary_ready: true });
      }),
    });
    await ensureSummaries([2], d);

    expect(d.wait).toHaveBeenCalledTimes(2);
    expect(d.summarize).not.toHaveBeenCalled();
  });

  it("后台一直不结束就别无限等下去，自己跑一遍（后端幂等，最差是白等）", async () => {
    const d = deps({ status: vi.fn(async (n: number) => status(n, { running: true })) });
    await ensureSummaries([2], d);

    expect(d.wait).toHaveBeenCalledTimes(MAX_POLLS);
    expect(d.summarize).toHaveBeenCalledWith(2);
  });

  it("某一章补不上就停下并说出是哪一章 —— 不许继续凑出一份少了记忆的稿子", async () => {
    const d = deps({
      summarize: vi.fn(async (n: number) => {
        if (n === 5) throw new Error("模型没连上");
        return undefined;
      }),
    });
    const boom = await ensureSummaries([2, 5, 8], d).catch((e: unknown) => e);

    expect(boom).toBeInstanceOf(SummaryPrepError);
    expect((boom as SummaryPrepError).chapter).toBe(5);
    expect((boom as SummaryPrepError).message).toBe("模型没连上");
    // 第 8 章没有被继续补 —— 失败之后不再往下走。
    expect(d.summarize.mock.calls.map(([n]) => n)).toEqual([2, 5]);
  });
});
