import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useOpenChapter } from "./chapterNavigation";
import { stubFetch } from "./test/harness";
import { useCoords } from "./store";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1 });
});

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

function mount() {
  const spy = vi.spyOn(globalThis, "fetch");
  const hook = renderHook(() => useOpenChapter(), { wrapper: makeWrapper() });
  return { spy, open: (n: number) => act(() => hook.result.current(n)) };
}

/** 那些 POST /chapters/N/autopilot 打的是第几章（Task 16 起应该没有）。 */
function autopilotChapters(spy: { mock: { calls: unknown[][] } }): number[] {
  return spy.mock.calls
    .filter((call) => String((call[1] as RequestInit | undefined)?.method) === "POST")
    .map((call) => /\/chapters\/(\d+)\/autopilot$/.exec(String(call[0]))?.[1])
    .filter((n): n is string => !!n)
    .map(Number);
}

/** 换章时打的 `/focus` 免费心跳报的是第几章。 */
function focusChapters(spy: { mock: { calls: unknown[][] } }): number[] {
  return spy.mock.calls
    .filter((call) => String((call[1] as RequestInit | undefined)?.method) === "POST")
    .filter((call) => /\/focus$/.test(String(call[0])))
    .map((call) => {
      const body = (call[1] as RequestInit | undefined)?.body as string;
      try {
        return JSON.parse(body).chapter as number;
      } catch {
        return -1;
      }
    });
}

describe("换章（2026-08-18 §3：只上报位置，不发付费工作）", () => {
  it("切章落坐标 + 打 `/focus` 免费心跳（新目标章）", async () => {
    stubFetch();
    const { spy, open } = mount();
    open(5);

    expect(useCoords.getState().chapter).toBe(5);
    await waitFor(() => expect(focusChapters(spy)).toEqual([5]));
    // 不指向旧那章，不触发任何 autopilot。
    await waitFor(() => expect(autopilotChapters(spy)).toEqual([]));
  });

  it("点的还是当前这一章就什么都不做（不扫脏心跳）", async () => {
    stubFetch();
    const { spy, open } = mount();
    open(1);
    await Promise.resolve();
    expect(autopilotChapters(spy)).toEqual([]);
    expect(focusChapters(spy)).toEqual([]);
  });
});
