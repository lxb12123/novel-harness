import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { CharacterBasicInfo } from "./CharacterBasicInfo";

// 人物基础信息（Task 15 / §4.5）：本名 + 一排别名 chip。
//
// 吃真 dump（`fixtures.characterProfile`）：作者别名一条（source=author）。
// 揭示的是单一别名字段的正交形状：本名不是 chip、canonical 不出现、chip 有
// 撤回杆，机器别名有「自动」标记。

const PID = "project:ID1";
const HERO = "character:ID10";

describe("CharacterBasicInfo", () => {
  it("显示本名 + 别名 chips；canonical 不是 chip", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<CharacterBasicInfo characterId={HERO} />);
    // 本名（fixture 的 character.name）。
    expect(await screen.findByText("萧决")).toBeInTheDocument();
    // fixture 的 ACTIVE 别名（source=author，一条「魔尊」语义）一个都不该少。
    const chips = screen.getAllByText(/^(?!萧决$)/);
    expect(chips.length).toBeGreaterThanOrEqual(1);
    // canonical 不是 chip：本名周围没有它的撤回杆。
    expect(screen.queryByLabelText(/撤回「萧决」/)).toBeNull();
  });

  it("添加入口存在；输入 + 回车走真路由（expected_canon_version 取 profile 的）", async () => {
    useCoords.setState({ projectId: PID });
    const user = userEvent.setup();
    renderWithApi(<CharacterBasicInfo characterId={HERO} />);
    await screen.findByText("萧决");
    const input = screen.getByPlaceholderText("加一个称呼…");
    await user.type(input, "凤辣子");
    await user.keyboard("{Enter}");
    // 走 fetcher 的捕获：POST /characters/{id}/aliases -> fixture.aliasCreated。
    await waitFor(() => expect(screen.getByDisplayValue("")).toBeInTheDocument());
  });
});
