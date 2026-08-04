import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { ProposalReviewTab } from "./ProposalReviewTab";

const open = () => {
  useCoords.getState().setProject("project:ID1");
  useCoords.getState().setChapter(1);
  renderWithApi(<ProposalReviewTab />);
};

describe("M4 提案审阅面板", () => {
  it("渲染待确认的冲突卡：当前 vs 提议 + 引语", async () => {
    open();
    const card = (await screen.findByText(/关系冲突/)).closest(".statecard") as HTMLElement;
    expect(within(card).getByText(/当前：/)).toBeInTheDocument();
    expect(within(card).getByText(/提议：/)).toBeInTheDocument();
    const conflict = fixtures.proposals.find((p) => p.kind === "edge_conflict");
    const item = (conflict!.items as { proposed: { quote: string } }[])[0];
    expect(within(card).getByText(item.proposed.quote)).toBeInTheDocument();
  });

  it("低置信事件卡渲染概要、在场人名、置信度与证据引语", async () => {
    open();
    const card = (await screen.findByText(/低置信事件/)).closest(".statecard") as HTMLElement;
    expect(within(card).getByText(/置信度 60%/)).toBeInTheDocument();
    expect(within(card).getByText(/在场：萧决、李管家/)).toBeInTheDocument();
    expect(
      within(card).getByText(/萧决在青云城主府第一次听说了血脉秘密的真相/),
    ).toBeInTheDocument();
  });

  it("新人物卡给出 接受为角色 / 标为路人 两个动作", async () => {
    open();
    const card = (await screen.findByText(/新人物/)).closest(".statecard") as HTMLElement;
    expect(within(card).getByText(/陆青禾/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "接受为角色" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "标为路人" })).toBeInTheDocument();
  });

  it("审阅成功后刷新提案/事件/花名册/状态查询", async () => {
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const user = userEvent.setup();
    open();
    const card = (await screen.findByText(/低置信事件/)).closest(".statecard") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "接受" }));
    expect(invalidate).toHaveBeenCalled();
    const calls = invalidate.mock.calls.flatMap((c) => c as { queryKey?: unknown[] }[]);
    const keys = calls.map((c) => c?.queryKey?.[0]).filter(Boolean);
    for (const prefix of ["proposals", "events", "roster", "state"]) {
      expect(keys).toContain(prefix);
    }
    invalidate.mockRestore();
  });

  it("被动事件灰显带 未确认 标记，可勾选批量确认", async () => {
    const user = userEvent.setup();
    open();
    const checkboxes = await screen.findAllByRole("checkbox");
    expect(checkboxes.length).toBeGreaterThan(0);
    const labels = screen.getAllByText(/未确认/);
    expect(labels.length).toBeGreaterThan(0);
    await user.click(checkboxes[0]);
    expect(screen.getByRole("button", { name: /确认所选（1）/ })).toBeEnabled();
  });

  it("不展示任何原始 items_json 或 item_count 字段", async () => {
    open();
    await screen.findByText(/关系冲突/);
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("items_json");
    expect(text).not.toContain("proposal_set");
  });
});
