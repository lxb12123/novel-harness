import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { RosterDrawer } from "./RosterDrawer";

const open = () => renderWithApi(<RosterDrawer pid="project:ID1" onClose={vi.fn()} />);

describe("花名册抽屉", () => {
  it("只让作者建 6 类 —— StateDim / Chapter 是引擎自己的东西", async () => {
    open();
    for (const zh of ["人物", "地点", "秘密", "势力", "物品", "伏笔"]) {
      expect(await screen.findByRole("button", { name: zh })).toBeInTheDocument();
    }
    // 把这两个放进「新建」菜单 = 邀请作者手工造出引擎的内部结构。
    expect(screen.queryByRole("button", { name: "状态维度" })).toBeNull();
    expect(screen.queryByRole("button", { name: "章" })).toBeNull();
  });

  it("选了秘密才出现「内容」和「父秘密」两个格子", async () => {
    const user = userEvent.setup();
    open();
    expect(screen.queryByText(/秘密的内容/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "秘密" }));
    expect(screen.getByText(/秘密的内容/)).toBeInTheDocument();
    expect(screen.getByText(/父秘密/)).toBeInTheDocument();
  });

  it("建完给回执，并告诉作者下一步是什么", async () => {
    const user = userEvent.setup();
    open();
    await user.type(screen.getByPlaceholderText("萧决"), "顾清音");
    await user.click(screen.getByRole("button", { name: "建" }));
    // 回执里的名字来自**真后端的出参**（createNode fixture），不是我们回显输入框。
    expect(await screen.findByText(new RegExp(fixtures.createNode.name))).toBeInTheDocument();
  });

  it("1 字别名 + 规则可用：在按下按钮之前就说，且不替作者改", async () => {
    // ADR 0004：「音」「决」拿去正文里匹配 = 满篇误报。服务端会拒（422），
    // 但让作者填完再被拒是白费一次。注意拒的是 usable_for_rules 不是别名本身——
    // 1 字名的人物真书里有，存得下，只是规则不拿它开火。
    const user = userEvent.setup();
    open();
    await user.click(screen.getByRole("button", { name: "加称呼" }));
    await user.type(screen.getByPlaceholderText("魔尊"), "决");

    expect(screen.getByText(/只有 1 个字/)).toBeInTheDocument();
    // 「不替作者改」：勾还在那儿，系统没有自作主张地取消它。
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("去掉勾之后警告消失 —— 那句提示给的是出路，不是唠叨", async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByRole("button", { name: "加称呼" }));
    await user.type(screen.getByPlaceholderText("魔尊"), "决");
    await user.click(screen.getByRole("checkbox"));
    expect(screen.queryByText(/只有 1 个字/)).toBeNull();
  });

  it("服务端拒了就把它那半句人话原样端出来", async () => {
    const user = userEvent.setup();
    renderWithApi(<RosterDrawer pid="project:ID1" onClose={vi.fn()} />, [
      { method: "POST", match: /\/aliases$/, status: 422, body: fixtures.errorShortAlias },
    ]);
    await user.click(screen.getByRole("button", { name: "加称呼" }));
    await user.type(screen.getByPlaceholderText("萧决"), "萧决");
    await user.type(screen.getByPlaceholderText("魔尊"), "决");
    await user.click(screen.getByRole("checkbox")); // 绕过前端提示，逼服务端说话
    await user.click(screen.getByRole("button", { name: "加" }));

    // 后端那句话是写给作者看的（`_validation_error` 专门剥掉了 pydantic 的壳）。
    // 前端把它换成「操作失败」等于把那份用心扔掉。
    expect(await screen.findByText(new RegExp("满篇误报"))).toBeInTheDocument();
  });

  it("歧义列候选，绝不替作者挑", async () => {
    const user = userEvent.setup();
    renderWithApi(<RosterDrawer pid="project:ID1" onClose={vi.fn()} />, [
      { method: "POST", match: /\/aliases$/, status: 409, body: fixtures.errorAmbiguousName },
    ]);
    await user.click(screen.getByRole("button", { name: "加称呼" }));
    await user.type(screen.getByPlaceholderText("萧决"), "师兄");
    await user.type(screen.getByPlaceholderText("魔尊"), "大师兄");
    await user.click(screen.getByRole("button", { name: "加" }));

    expect(await screen.findByText(/系统不替你挑/)).toBeInTheDocument();
    for (const c of fixtures.errorAmbiguousName.candidates) {
      expect(screen.getByText(new RegExp(c.name))).toBeInTheDocument();
    }
  });
});
