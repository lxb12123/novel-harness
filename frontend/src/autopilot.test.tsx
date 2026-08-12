import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useOpenChapter } from "./autopilot";
import { stubFetch } from "./test/harness";
import { useCoords } from "./store";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1 });
});

/** 每个 test 一个 QueryClient，且**在渲染之外建**（放进组件体里会每次渲染换一个）。 */
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

/** 那些 POST /chapters/N/autopilot 打的是第几章。 */
function autopilotChapters(spy: { mock: { calls: unknown[][] } }): number[] {
  return spy.mock.calls
    .filter((call) => String((call[1] as RequestInit | undefined)?.method) === "POST")
    .map((call) => /\/chapters\/(\d+)\/autopilot$/.exec(String(call[0]))?.[1])
    .filter((n): n is string => !!n)
    .map(Number);
}

describe("换章 = 上一章写完了", () => {
  it("对**刚离开的**那一章交后台整理，不是对新进入的那一章", async () => {
    stubFetch();
    const { spy, open } = mount();
    open(5);

    expect(useCoords.getState().chapter).toBe(5);
    await waitFor(() => expect(autopilotChapters(spy)).toEqual([1]));
  });

  it("点的还是当前这一章就什么都不做", async () => {
    stubFetch();
    const { spy, open } = mount();
    open(1);
    await Promise.resolve();
    expect(autopilotChapters(spy)).toEqual([]);
  });

  it("后台整理失败不打扰作者：界面照常换章", async () => {
    // 端点今天可能还是 404（后端另一条线在落地）。作者不该因此看到任何东西，
    // 也不该因此换不了章。
    stubFetch([
      { method: "POST", match: /\/autopilot$/, status: 500, body: { message: "炸了" } },
    ]);
    const { open } = mount();
    open(7);

    await waitFor(() => expect(useCoords.getState().chapter).toBe(7));
    expect(document.body.textContent).not.toMatch(/炸了/);
  });
});
