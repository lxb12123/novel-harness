import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { RightPanel } from "./RightPanel";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, cast: "", activeTab: "matrix" });
});

describe("右侧信息区", () => {
  it("空白新书只显示一个作者能理解的下一步", async () => {
    renderWithApi(<RightPanel />, [{ match: /\/roster$/, body: [] }]);

    expect(await screen.findByText(/添加人物或设定后/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /认知矩阵|一致性|约束/ })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/R[1-4]|must_not_reveal|valid_from|issue/);
  });

  it("提醒与检查面板不显示规则编号或内部字段", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      { method: "POST", match: /\/check$/, body: fixtures.check },
    ]);

    await user.click(await screen.findByRole("button", { name: "写作提醒" }));
    expect(await screen.findByText("暂时不能说破")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/must_not_reveal|forbidden_entities|fail-closed/);

    await user.click(screen.getByRole("button", { name: "检查" }));
    await user.click(screen.getByRole("button", { name: "检查本章" }));
    expect(await screen.findByText(/已检查 1 个场景/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/R[1-4]|issue|location_conflict|future_leak/);
  });
});
