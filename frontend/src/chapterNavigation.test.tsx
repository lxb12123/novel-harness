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

describe("换章（Task 16：不再发后台整理）", () => {
  it("切章只落坐标，**不发**任何 autopilot POST", async () => {
    stubFetch();
    const { spy, open } = mount();
    open(5);

    expect(useCoords.getState().chapter).toBe(5);
    await waitFor(() => expect(autopilotChapters(spy)).toEqual([]));
  });

  it("点的还是当前这一章就什么都不做", async () => {
    stubFetch();
    const { spy, open } = mount();
    open(1);
    await Promise.resolve();
    expect(autopilotChapters(spy)).toEqual([]);
  });
});
