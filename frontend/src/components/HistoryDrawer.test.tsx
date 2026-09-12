import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { HistoryDrawer } from "./HistoryDrawer";

const older = fixtures.chapterHistoryTwo.find((s) => !s.is_current)!;

const oneVersion = [{ match: /\/history$/, body: fixtures.chapterHistory }];

function open(extra: Parameters<typeof renderWithApi>[1] = [], props = {}) {
  return renderWithApi(
    <HistoryDrawer pid="project:ID1" chapter={1} onClose={vi.fn()} {...props} />,
    extra,
  );
}

/** 按「有没有『当前』那个角标」认行——fixture 里时间戳被规范化成同一个串，认不了时间。 */
async function rows() {
  const all = await screen.findAllByRole("listitem");
  const now = all.find((r) => within(r).queryByText("当前"));
  const old = all.find((r) => !within(r).queryByText("当前"));
  return { all, now: now!, old: old! };
}

describe("历史版本", () => {
  it("开场那句话讲的是「能还原」，不是「能看差异」", async () => {
    open();
    expect(await screen.findByText(/每次保存都会留下一个版本/)).toHaveTextContent("还原");
    // 「查看它与当前正文的差异」是作者点名换掉的那句：它说的是工具的动作，
    // 不是作者想做的事。回归防线，别让它悄悄回来。
    expect(document.body.textContent).not.toContain("查看它与当前正文的差异");
  });

  it("每一版都能还原，当前那一版除外（它已经是正文了）", async () => {
    open();
    const { now, old } = await rows();
    expect(within(old).getByRole("button", { name: "还原" })).toBeInTheDocument();
    expect(within(now).queryByRole("button", { name: "还原" })).toBeNull();
  });

  it("编辑器里有没保存的修改时，当前那一版也能「还原」= 丢掉这些修改，**不写盘**", async () => {
    // 写作助手放进编辑器的那一稿、作者自己敲的字，不要了都走这儿（「放弃这一稿」那颗按钮
    // 作者 2026-09-12 说没必要存在）。磁盘上就是当前这一版，所以这一档不发 PUT。
    const onDiscard = vi.fn();
    const onRestored = vi.fn();
    const onClose = vi.fn();
    let puts = 0;
    open(
      [{ method: "PUT", match: /\/text$/, body: fixtures.chapterSaved, onRequest: () => void puts++ }],
      { dirty: true, onDiscard, onRestored, onClose },
    );
    const { now } = await rows();
    const restore = within(now).getByRole("button", { name: "还原" });
    expect(restore).toHaveAttribute("title", expect.stringContaining("放弃未保存的修改"));
    restore.click();
    expect(await screen.findByText("放弃未保存的修改，回到当前版本？")).toBeInTheDocument();
    screen.getByRole("button", { name: "确认还原" }).click();
    await waitFor(() => expect(onDiscard).toHaveBeenCalled());
    expect(onRestored).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    expect(puts).toBe(0);
  });

  it("只有一版、但编辑器里有没保存的修改：列表照画，当前那一行上有「还原」", async () => {
    open(oneVersion, { dirty: true });
    const { all, now } = await rows();
    expect(all).toHaveLength(1);
    expect(within(now).getByRole("button", { name: "还原" })).toBeInTheDocument();
  });

  it("正文现在这一版删不掉，而且当场说得出为什么", async () => {
    open();
    const { now, old } = await rows();
    const disabled = within(now).getByRole("button", { name: "删除" });
    expect(disabled).toBeDisabled();
    expect(disabled).toHaveAttribute("title", expect.stringContaining("无法删除"));
    expect(within(old).getByRole("button", { name: "删除" })).toBeEnabled();
  });

  it("还原要先确认一次 —— 它会盖掉正文，点一下就执行太快", async () => {
    const onRestored = vi.fn();
    const onClose = vi.fn();
    open([], { onRestored, onClose });

    const { old } = await rows();
    within(old).getByRole("button", { name: "还原" }).click();
    expect(await screen.findByText(/将正文还原到/)).toBeInTheDocument();
    expect(onRestored).not.toHaveBeenCalled(); // 只是问了一句，还没动正文

    screen.getByRole("button", { name: "确认还原" }).click();
    await waitFor(() => expect(onRestored).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled(); // 还原完就回正文，别让作者再找一次关闭
  });

  it("编辑器里有没保存的修改时，还原之前要说一声", async () => {
    open([], { dirty: true });
    const { old } = await rows();
    within(old).getByRole("button", { name: "还原" }).click();
    expect(await screen.findByText(/未保存的修改/)).toBeInTheDocument();
  });

  it("取消就什么都不发生", async () => {
    const onRestored = vi.fn();
    open([], { onRestored });
    const { old } = await rows();
    within(old).getByRole("button", { name: "还原" }).click();
    (await screen.findByRole("button", { name: "取消" })).click();
    await waitFor(() => expect(screen.queryByText(/将正文还原到/)).toBeNull());
    expect(onRestored).not.toHaveBeenCalled();
  });

  it("删除也要确认，并且说清删了找不回来", async () => {
    open();
    const { old } = await rows();
    within(old).getByRole("button", { name: "删除" }).click();
    expect(await screen.findByText(/删除后无法恢复/)).toBeInTheDocument();
  });

  it("后端说这一版被依据引用着时，界面说的是「依据会找不到出处」不是错误码", async () => {
    open([
      {
        method: "DELETE",
        match: /\/snapshots\//,
        status: 409,
        body: {
          error: "snapshot_in_use",
          usage: {
            snapshot_id: older.snapshot_id,
            evidence: 3,
            extraction_runs: 0,
            proposal_sets: 0,
          },
          message: "engine-side message",
        },
      },
    ]);
    const { old } = await rows();
    within(old).getByRole("button", { name: "删除" }).click();
    (await screen.findByRole("button", { name: "确认删除" })).click();

    expect(await screen.findByText(/3 条原文依据/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("snapshot_in_use");
    expect(document.body.textContent).not.toContain("engine-side message");
  });

  it("只有一版时不摆出还原和删除，只说以后会有", async () => {
    open(oneVersion);
    expect(await screen.findByText(/只有一个版本/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "还原" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
  });
});
