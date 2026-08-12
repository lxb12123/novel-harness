import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { RosterTab } from "./RosterTab";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
});

describe("花名册", () => {
  it("按 label 分组，人名一个不落", async () => {
    renderWithApi(<RosterTab />);
    for (const n of fixtures.roster) {
      expect(await screen.findByText(n.name)).toBeInTheDocument();
    }
  });

  it("空花名册只给一个清楚的下一步，不解释内部实现", async () => {
    renderWithApi(<RosterTab />, [{ match: /\/roster$/, body: [] }]);
    expect(await screen.findByText(/添加第一个条目/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/导入只切章|认知矩阵|规则|ADR/);
  });

  it("点「建第一个」能开出建条目的抽屉 —— 空态那句话必须真的有出口", async () => {
    renderWithApi(<RosterTab />, [{ match: /\/roster$/, body: [] }]);
    (await screen.findByText(/添加第一个条目/)).click();
    expect(await screen.findByRole("heading", { name: "花名册" })).toBeInTheDocument();
  });

  it("点一个人 = 看他的关系图（这条在搬家之后没变）", async () => {
    renderWithApi(<RosterTab />);
    const first = fixtures.roster[0];
    (await screen.findByText(first.name)).click();
    await waitFor(() => {
      expect(useCoords.getState().selectedNodeId).toBe(first.id);
      expect(useCoords.getState().activeTab).toBe("graph");
    });
  });
});
