import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { SceneBar } from "./SceneBar";

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    selectedNodeId: null,
    cast: "",
  });
});

const SCENES = [
  {
    number: 1,
    cast: ["萧决", "李管家"],
    loc: "北荒",
    goal: "李管家试探萧决的身世",
    para_index: 2,
    decl_text: "<!-- nh: cast=萧决,李管家 loc=北荒 -->",
  },
  {
    number: 2,
    cast: ["苏挽"],
    loc: null,
    goal: null,
    para_index: 6,
    decl_text: "<!-- nh: cast=苏挽 -->",
  },
];

describe("场景条", () => {
  it("空态说的是「下一步干什么」，不是空", async () => {
    // fixtures.scenes = []：导入只切章、不认人，首次打开必然没有场景块。
    renderWithApi(<SceneBar />);
    expect(await screen.findByText(/这一章没有场景块/)).toBeInTheDocument();
    expect(document.body.textContent).toContain("## 场景 1");
  });

  it("渲染场景 pill：场景号 + 在场 + 地点", async () => {
    renderWithApi(<SceneBar />, [{ match: /\/scenes$/, body: SCENES }]);
    const pill = await screen.findByRole("button", { name: /场景 1/ });
    expect(pill).toHaveTextContent("萧决,李管家");
    expect(pill).toHaveTextContent("@北荒");
  });

  it("点一场把在场写进全局坐标——右栏面板的输入", async () => {
    renderWithApi(<SceneBar />, [{ match: /\/scenes$/, body: SCENES }]);
    const pill = await screen.findByRole("button", { name: /场景 1/ });
    pill.click();
    expect(useCoords.getState().cast).toBe("萧决,李管家");
  });
});
