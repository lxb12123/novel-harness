import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { RosterDrawer } from "./RosterDrawer";

const open = () => renderWithApi(<RosterDrawer pid="project:ID1" onClose={vi.fn()} />);

function renderedCopy() {
  const placeholders = [...document.querySelectorAll<HTMLElement>("[placeholder]")]
    .map((element) => element.getAttribute("placeholder") ?? "")
    .join(" ");
  return `${document.body.textContent ?? ""} ${placeholders}`;
}

describe("角色册抽屉", () => {
  it("只展示作者可以创建的 5 类条目，并使用通用作者语言", async () => {
    open();
    for (const zh of ["人物", "地点", "势力", "物品", "伏笔"]) {
      expect(await screen.findByRole("button", { name: zh })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "状态维度" })).toBeNull();
    expect(screen.queryByRole("button", { name: "章" })).toBeNull();
    expect(renderedCopy()).not.toMatch(
      /M2|ADR|kill-gate|修正案|实验状态|X0|X1|X2|萧决|顾清音|李管家|苏挽|魔尊|北荒|血脉秘密|引擎|节点|矩阵|规则|服务端|canon|幂等/,
    );
  });

  // 这儿原来还有一条「选了秘密才出现内容和所属秘密两个格子」。秘密下线之后
  // （ADR 0039）抽屉里不再有随 label 变化的字段，那条测试没有对象了。
  it("「秘密」不再是可建的一类", async () => {
    open();
    expect(screen.queryByRole("button", { name: "秘密" })).toBeNull();
  });

  it("建完给回执，并告诉作者下一步是什么", async () => {
    const user = userEvent.setup();
    open();
    await user.type(screen.getByPlaceholderText("输入名称"), "测试角色");
    await user.click(screen.getByRole("button", { name: "建" }));
    // 回执里的名字来自**真后端的出参**（createNode fixture），不是我们回显输入框。
    expect(await screen.findByText(new RegExp(fixtures.createNode.name))).toBeInTheDocument();
  });

  it("1 字别名用于识别正文时，在提交前给出清楚提示", async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByRole("button", { name: "加别名" }));
    await user.type(screen.getByPlaceholderText("输入新的别名"), "名");

    expect(screen.getByText(/只有 1 个字/)).toBeInTheDocument();
    // 「不替作者改」：勾还在那儿，系统没有自作主张地取消它。
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("去掉勾之后警告消失 —— 那句提示给的是出路，不是唠叨", async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByRole("button", { name: "加别名" }));
    await user.type(screen.getByPlaceholderText("输入新的别名"), "名");
    await user.click(screen.getByRole("checkbox"));
    expect(screen.queryByText(/只有 1 个字/)).toBeNull();
  });

  it("接口拒绝时不向作者暴露内部术语", async () => {
    const user = userEvent.setup();
    renderWithApi(<RosterDrawer pid="project:ID1" onClose={vi.fn()} />, [
      { method: "POST", match: /\/aliases$/, status: 422, body: fixtures.errorShortAlias },
    ]);
    await user.click(screen.getByRole("button", { name: "加别名" }));
    await user.type(screen.getByPlaceholderText("输入已有名称"), "测试角色");
    await user.type(screen.getByPlaceholderText("输入新的别名"), "名");
    await user.click(screen.getByRole("checkbox")); // 绕过前端提示，逼服务端说话
    await user.click(screen.getByRole("button", { name: "加" }));

    expect(await screen.findByText(/无法添加这个别名/)).toBeInTheDocument();
    expect(renderedCopy()).not.toMatch(/ADR|usable_for_rules|规则|服务端/);
  });

  it("歧义列候选，绝不替作者挑", async () => {
    const user = userEvent.setup();
    renderWithApi(<RosterDrawer pid="project:ID1" onClose={vi.fn()} />, [
      { method: "POST", match: /\/aliases$/, status: 409, body: fixtures.errorAmbiguousName },
    ]);
    await user.click(screen.getByRole("button", { name: "加别名" }));
    await user.type(screen.getByPlaceholderText("输入已有名称"), "旧称呼");
    await user.type(screen.getByPlaceholderText("输入新的别名"), "新称呼");
    await user.click(screen.getByRole("button", { name: "加" }));

    expect(await screen.findByText(/找到多个匹配项/)).toBeInTheDocument();
    for (const c of fixtures.errorAmbiguousName.candidates) {
      expect(screen.getByText(new RegExp(c.name))).toBeInTheDocument();
    }
  });
});
