import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { RightPanel } from "./RightPanel";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, cast: "", activeTab: "roster" });
});

describe("右侧信息区", () => {
  it("默认停在花名册，而且它是第一格", async () => {
    renderWithApi(<RightPanel />);
    const tabs = await screen.findAllByRole("button");
    expect(tabs[0]).toHaveAccessibleName("花名册");
    expect(tabs[0]).toHaveClass("on");
    expect(await screen.findByText(fixtures.roster[0].name)).toBeInTheDocument();
  });

  it("空白新书只显示一个作者能理解的下一步", async () => {
    renderWithApi(<RightPanel />, [{ match: /\/roster$/, body: [] }]);

    expect(await screen.findByText(/添加第一个条目/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/R[1-4]|must_not_reveal|valid_from|issue/);
  });

  it("花名册空着的时候，加人的入口不能跟着一起消失", async () => {
    // 这一格原先会把**整块面板**换成一句「先去加人」——连花名册和那个「＋」一起藏掉，
    // 于是作者停在一个叫他加人、却没有加人入口的面板上。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [{ match: /\/roster$/, body: [] }]);

    expect(await screen.findByRole("button", { name: "＋" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "人物状态" }));
    expect(await screen.findByText(/添加人物或设定后/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "花名册" })).toBeInTheDocument();
  });

  it("提醒与检查面板不显示规则编号或内部字段", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      { method: "POST", match: /\/check$/, body: fixtures.check },
    ]);

    await user.click(await screen.findByRole("button", { name: "写作提醒" }));
    expect(await screen.findByText("本章尚未登场")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/forbidden_entities|fail-closed/);

    await user.click(screen.getByRole("button", { name: "检查" }));
    await user.click(screen.getByRole("button", { name: "检查本章" }));
    // **不许说「已检查 N 个场景」**：「场景」是作者要在正文里手写的标记块，
    // 导进来的真书上永远是零个；而它旁边那句「无法进行内容检查」从 R2/R3 进
    // `ALL_CHECKS`（2026-08-02）起就是假的——零场景块照样查了两条规则。
    expect(await screen.findByText(/没有发现需要处理的问题/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/场景/);
    expect(document.body.textContent).not.toMatch(/R[1-4]|issue|location_conflict|future_leak/);
  });

  it("**检查跑不起来时不许把裸码摆上屏**（曾经直接读 `(check.error as Error).message`）", async () => {
    // 真发生过的那次：这一章的正文文件已经不在了，后端只回一个码——
    // `(check.error as Error).message` 在没有 `.message` 字段时会退回 `body.error`，
    // 屏幕上出现的就是原样的 `chapter_not_found` 五个字母（国际化第四批·裸错误码审计
    // 撞见的真回归，这一格此前没有任何失败路径测试）。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      {
        method: "POST",
        match: /\/check$/,
        status: 404,
        body: { detail: { error: "chapter_not_found", params: { chapter: 1 } } },
      },
    ]);

    await user.click(await screen.findByRole("button", { name: "检查" }));
    await user.click(screen.getByRole("button", { name: "检查本章" }));

    expect(
      await screen.findByText("第 1 章已经不在了 —— 可能被删除或改了章号。刷新一下再看。"),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/chapter_not_found/);
  });
});

describe("在场是数出来的，不是作者填的", () => {
  it("直接显示这一章正文里提到了谁 —— 作者一个字都不用填", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "人物状态" }));

    expect(await screen.findByText("这一章提到：")).toBeInTheDocument();
    expect(screen.getByText(fixtures.mentioned.surfaces.join("、"))).toBeInTheDocument();
  });

  it("**措辞是「提到」不是「在场」** —— 引擎在数字符串，没有读懂剧情", async () => {
    // 回忆里的死人、信里写到的名字都会进来。说「在场」等于向作者承诺系统做不到的事。
    // 多算是安全那一侧（must_not_reveal 的判据是「至少有一个人还不知道」），
    // 但那是**工程上**安全，不代表可以在界面上把它说成另一件事。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "人物状态" }));

    await screen.findByText("这一章提到：");
    expect(document.body.textContent).not.toMatch(/在场/);
  });

  it("「还没写」和「写了但没提到人」说的不是同一句话", async () => {
    // §10 约束 8：静默的零和真的零不许长得一样。前者该说「去写」，后者该说「补花名册」。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      { match: /\/chapters\/\d+\/mentioned/, body: fixtures.mentionedEmpty },
    ]);
    await user.click(await screen.findByRole("button", { name: "人物状态" }));
    expect(await screen.findByText(/这一章还没有正文/)).toBeInTheDocument();
  });

  it("只看几个人的时候说出来，而且退得出去", async () => {
    // 场景块是作者在正文里亲手标的，比推导可信——所以它压过推导。**收窄只有这一个入口**：
    // 日志页跳过来的那个坐标走的是 `castInclude`（只加不减，`JumpCast.coord.test.tsx`
    // 钉着方向），它没把任何人从表上拿掉。这一行是那条过滤唯一的出口，没有它，
    // 一次过滤就把一张多行的表悄悄变成一行而作者不知道发生了什么。
    const user = userEvent.setup();
    useCoords.setState({ cast: "萧决" });
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "人物状态" }));

    expect(await screen.findByText("只看：")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "改回整章" }));
    expect(useCoords.getState().cast).toBe("");
  });
});
