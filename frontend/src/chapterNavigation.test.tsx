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

/** 换章时除了 `/focus` 之外还打出去的 POST（**应该一个都没有**）。
 *
 *  ⚠️ 这条比它替下来的那版严：原来只数 `POST …/autopilot`，而那条端点 2026-08-20 已
 *  整块删掉（ADR 0035），一个打不到的 URL 守不住任何东西。现在数的是「除心跳外的
 *  一切 POST」——**换章不许花钱**这条纪律，从此对将来任何新加的付费动作都成立，
 *  不只对 autopilot。 */
function nonFocusPosts(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls
    .filter((call) => String((call[1] as RequestInit | undefined)?.method) === "POST")
    .map((call) => String(call[0]))
    .filter((url) => !/\/focus$/.test(url));
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
    // 不指向旧那章，也不发任何付费工作。
    await waitFor(() => expect(nonFocusPosts(spy)).toEqual([]));
  });

  it("点的还是当前这一章就什么都不做（不扫脏心跳）", async () => {
    stubFetch();
    const { spy, open } = mount();
    open(1);
    await Promise.resolve();
    expect(nonFocusPosts(spy)).toEqual([]);
    expect(focusChapters(spy)).toEqual([]);
  });
});
