import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { CharacterBasicInfo } from "./CharacterBasicInfo";

// 人物的别名（Task 15 / §4.5）：一排 chip + 一个添加框。
//
// 吃真 dump（`fixtures.characterProfile`）：作者别名一条（source=author）。
// 揭示的是单一别名字段的正交形状：canonical 不出现、chip 有撤回杆，机器别名有
// 「自动」标记。
//
// **2026-09-04 起本名不在这张卡里**（作者点名：「这上边已经有贾环了，为什么在正文
// 还要整一个」），所以下面等数据到的信号是那条别名 chip，不再是本名。
// 基础资料（性别/性格/…）同日挪去了「状态」那一格（作者：「基础是状态里面的一个
// 子集」），它的测试跟着搬到 `CharacterStatus.test.tsx`。

const PID = "project:ID1";
const HERO = "character:ID10";

describe("CharacterBasicInfo", () => {
  it("只列别名 chips：本名不再印一遍，canonical 也不是 chip", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<CharacterBasicInfo characterId={HERO} />);
    // fixture 的 ACTIVE 别名（source=author）一个都不该少。
    const alias = fixtures.characterProfile.aliases[0].surface;
    expect(await screen.findByText(alias)).toBeInTheDocument();
    // **本名不在这张卡里**：它就在上面那一行的左边，卡片再抄一份 = 同一个字在同一
    // 屏上写两次。这条断言是那次删除的锁——没有它，下一个人会顺手把标题加回来。
    expect(screen.queryByText(fixtures.characterProfile.character.name)).toBeNull();
    // canonical 不是 chip：没有它的撤回杆。
    expect(screen.queryByLabelText(/撤回「萧决」/)).toBeNull();
  });

  it("「怎么保存」写在输入框里，标签和输入框之间不塞第二行字", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<CharacterBasicInfo characterId={HERO} />);
    await screen.findByText(fixtures.characterProfile.aliases[0].surface);
    // 作者 2026-09-04 裁定的形状：提示进 placeholder，**下面那行常驻小字撤掉**
    // （我提过 placeholder 一打字就消失，他仍然要这个形状）。两头一起钉，
    // 否则「加回一行小字」和「把提示从框里删掉」各自都不会红。
    expect(screen.getByPlaceholderText("添加别名，回车保存…")).toBeInTheDocument();
    expect(screen.queryByText("回车保存")).toBeNull();
  });

  it("添加入口存在；输入 + 回车走真路由（expected_canon_version 取 profile 的）", async () => {
    useCoords.setState({ projectId: PID });
    const user = userEvent.setup();
    renderWithApi(<CharacterBasicInfo characterId={HERO} />);
    await screen.findByText(fixtures.characterProfile.aliases[0].surface);
    const input = screen.getByPlaceholderText("添加别名，回车保存…");
    await user.type(input, "凤辣子");
    await user.keyboard("{Enter}");
    // 走 fetcher 的捕获：POST /characters/{id}/aliases -> fixture.aliasCreated。
    await waitFor(() => expect(screen.getByDisplayValue("")).toBeInTheDocument());
  });
});
