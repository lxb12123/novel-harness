import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { DeclareDrawer } from "./DeclareDrawer";

describe("原文声明", () => {
  it("表单和成功回执不向作者展示内部字段或演示小说内容", async () => {
    const user = userEvent.setup();
    renderWithApi(<DeclareDrawer pid="project:ID1" quote="他终于知道了真相。" onClose={() => {}} />);

    expect(document.body.textContent).not.toMatch(/约束 10|valid_from|decision_log|萧决|北荒|血脉秘密/);
    await user.type(screen.getByLabelText("人物"), "主角");
    await user.type(screen.getByLabelText("秘密"), "身世真相");
    await user.click(screen.getByRole("button", { name: "保存记录" }));

    expect(await screen.findByText(/已记录/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/valid_from|decision_log|CANON|KNOWS|ch\d+/);
  });
});
