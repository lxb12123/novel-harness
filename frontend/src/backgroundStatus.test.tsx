import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useBackgroundStatus, useRoster } from "./api/hooks";
import { fixtures, stubFetch } from "./test/harness";

// 顶栏那盏灯读的那行（`useBackgroundStatus`）除了亮灯还干一件事：**后台从「有活」变成
// 「没活」那一刻，把它会改的读端全部重取**。作者第一次用桌面版的原话：「一旦我切换到
// 角色栏……再返回这个状态就丢失」——他要的是整理完了角色册自己出现，不是换一次 tab 碰运气。

function Probe({ pid }: { pid: string }) {
  const status = useBackgroundStatus(pid);
  const roster = useRoster(pid);
  return (
    <div>
      <span data-testid="busy">{String((status.data?.running.length ?? 0) > 0)}</span>
      <span data-testid="roster">{roster.data?.length ?? "…"}</span>
    </div>
  );
}

describe("useBackgroundStatus：跑完那一刻自己重取", () => {
  it("有活 → 没活：角色册重取一次；一开始就没活：一次都不多取", async () => {
    let phase: "busy" | "idle" = "busy";
    stubFetch([
      { match: /\/background$/, body: () => ({ configured: true, running: phase === "busy" ? [3] : [], queued: [] }) },
      { match: /\/roster$/, body: fixtures.rosterWithCounts },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    const rosterReads = () =>
      spy.mock.calls.filter(([url]) => /\/roster$/.test(String(url))).length;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <Probe pid="project:ID1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("busy").textContent).toBe("true"));
    await waitFor(() => expect(rosterReads()).toBe(1));

    // 后台跑完了：下一次轮询回「没活」（这里不等 4 秒，直接让它重取一次）
    phase = "idle";
    await act(async () => {
      await qc.refetchQueries({ queryKey: ["background", "project:ID1"] });
    });
    await waitFor(() => expect(screen.getByTestId("busy").textContent).toBe("false"));
    await waitFor(() => expect(rosterReads()).toBe(2));

    // 再问一次还是没活：不该再重取（判据是变化，不是「现在没活」）
    await act(async () => {
      await qc.refetchQueries({ queryKey: ["background", "project:ID1"] });
    });
    expect(rosterReads()).toBe(2);
  });
});
