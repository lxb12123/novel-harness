import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useReconcileOnFocus } from "./reconcile";

// 这条钩子把「作者手点读回改动」换成了「他切回标签页时自己发生」。
// **它没有屏幕**，所以判据只能是「发了哪些请求」——这份文件全是这一件事。

const EMPTY = { checked: 3, reread: [], refreshed: [], refused: [] };

function Probe({ pid }: { pid: string | null }) {
  useReconcileOnFocus(pid);
  return null;
}

/** 装一个只认 `/reconcile` 的 fetch，返回它收到的每一次调用。 */
function mount(pid: string | null, body: unknown = EMPTY) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(String(url));
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <Probe pid={pid} />
    </QueryClientProvider>,
  );
  return { calls, qc, view };
}

const focus = () => document.dispatchEvent(new Event("visibilitychange"));

describe("回焦时把库和磁盘对一遍", () => {
  it("开书**深对一次**（那一次要抓 mtime 撒谎的改动）", async () => {
    const { calls } = mount("project:ID1");
    await waitFor(() => expect(calls).toHaveLength(1));
    // `deep=true` 忽略 stat 快路，每一章都重新读+算哈希。少了它，`rsync -t` /
    // 从备份恢复那种「保留原 mtime」的改动**永远读不回来**（后端有一条专门钉它）。
    expect(calls[0]).toContain("/reconcile?deep=true");
  });

  it("之后每次切回标签页**快对**（只 stat，不再深对）", async () => {
    const { calls } = mount("project:ID1");
    await waitFor(() => expect(calls).toHaveLength(1));

    focus();
    await waitFor(() => expect(calls).toHaveLength(2));
    // **第二次必须是 deep=false**：深对是 244ms/722 章，每次 alt-tab 都来一遍
    // 就等于把这条路径变回它要替掉的那颗按钮那么贵。
    expect(calls[1]).toContain("/reconcile?deep=false");
  });

  it("还没开书（`pid` 为空）时一条都不发", async () => {
    const { calls } = mount(null);
    focus();
    await new Promise((r) => setTimeout(r, 30));
    expect(calls).toEqual([]);
  });

  it("页面被切走（`hidden`）时不发 —— 只在**回到**这一页时对", async () => {
    const { calls } = mount("project:ID1");
    await waitFor(() => expect(calls).toHaveLength(1));

    const spy = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    focus();
    await new Promise((r) => setTimeout(r, 30));
    expect(calls).toHaveLength(1);
    spy.mockRestore();
  });

  it("没有一章变过时**不整片失效缓存**", async () => {
    const { calls, qc } = mount("project:ID1");
    await waitFor(() => expect(calls).toHaveLength(1));
    const invalidate = vi.spyOn(qc, "invalidateQueries");

    focus();
    await waitFor(() => expect(calls).toHaveLength(2));
    await new Promise((r) => setTimeout(r, 10));

    // 每次 alt-tab 都把右栏全部重取一遍，等于用一个「什么都没发生」的检查
    // 换来一整屏的请求。
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("真有章变了才失效", async () => {
    const { calls, qc } = mount("project:ID1", { ...EMPTY, refreshed: [7] });
    await waitFor(() => expect(calls).toHaveLength(1));
    const invalidate = vi.spyOn(qc, "invalidateQueries");

    focus();
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0]);
    // 正文、章目录、历史都得重取——磁盘上那一章刚换了内容。
    expect(new Set(keys)).toEqual(
      new Set(["chapters", "text", "history", "matrix", "state", "check"]),
    );
  });

  it("端点炸了就当无事发生 —— **不弹、不重试**", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(String(url));
      throw new Error("网络断了");
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <Probe pid="project:ID1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(calls).toHaveLength(1));

    // 这条路径作者没按过任何按钮，弹一个错等于每次 alt-tab 骂他一次。
    // 下一次回焦照常再试。
    focus();
    await waitFor(() => expect(calls).toHaveLength(2));
  });
});
