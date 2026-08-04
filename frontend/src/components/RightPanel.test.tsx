import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
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
});
