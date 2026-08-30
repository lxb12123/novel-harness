import { useMutation } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { TopBar } from "./TopBar";

beforeEach(() => {
  // `chatOpen` 也要归位：store 是模块单例，上一条 test 开着它，下一条「默认关着」就假绿。
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    selectedNodeId: null,
    page: "workbench",
    chatOpen: false,
  });
});

describe("顶栏", () => {
  it("**章节选择器不在这儿了** —— 它搬去中栏那行章标题上（一个功能不留两个入口）", async () => {
    // 顶栏这一行讲的是整个工作台（换页、设置），而「正在编辑的是哪一章」只对中栏成立。
    // 搬下去之后它和章标题合成了同一样东西：那行字既是标题，也是挑章的入口，还能双击改。
    // 换章要发的那次后台整理跟着一起搬走了（`ChapterTitle.test.tsx` 钉着）。
    renderWithApi(<TopBar />);
    await screen.findByRole("button", { name: "写作助手" });
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByText("章节")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("设置在**最右边**，而且它是一颗图标按钮 —— 图标没有名字，名字得由按钮给", async () => {
    // 左边那几颗是「现在看哪一块屏幕」（天天按），设置是「这台机器怎么配」（配完不再碰）。
    // 断言两件事：① 那根弹簧在它前面（= 它被推到最右）；② 它有无障碍名字。
    // ② 是这次换图标最容易悄悄丢的东西：`⚙` 那个字本身就是名字，一颗 <svg> 不是。
    renderWithApi(<TopBar />);
    const gear = await screen.findByRole("button", { name: "AI 设置" });
    const header = gear.closest("header") as HTMLElement;

    expect(header.lastElementChild).toBe(gear);
    expect(gear.previousElementSibling).toHaveClass("spacer");
    // 图标本身不进无障碍树：进了的话读屏会在按钮名字之外再念一遍它。
    expect(gear.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("**只有图标的按钮，悬浮要看得见名字** —— 而且不是那个等一秒的原生 tooltip", async () => {
    // 作者说过这条（2026-08-13 复述）：他以为齿轮没有名字，因为 `title` 要等约一秒才浮出来。
    // 名字由 CSS 画（`.icon-btn::after` 读 `data-tip`），.12s 就出来。
    // 同时钉住「不留 `title`」：两个都在的话悬浮会同时冒出两个气泡。
    renderWithApi(<TopBar />);
    const gear = await screen.findByRole("button", { name: "AI 设置" });

    expect(gear).toHaveAttribute("data-tip", "AI 设置");
    expect(gear).not.toHaveAttribute("title");
  });

  it("只展示可用能力和产品文案，不泄漏演示内容或研发术语", async () => {
    renderWithApi(<TopBar />);

    expect(await screen.findByRole("button", { name: "活动记录" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AI 规划" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/萧决|顾清音|李管家|M2|valid_from|实验状态/);
  });

  it("**「AI 起草」那个抽屉已经删了** —— 一个功能不留两个入口", async () => {
    // 它是填表式的：先填「这一场要写什么」+ 在场角色，再点按钮出一整章。
    // 而在场人物是**写出来的结果**不是写之前的输入（ADR 0018），「接下来写什么」
    // 也不该由一个表单承载。起草归模式二的 agent 面板——在那儿它是一次工具调用，
    // 不是一个界面。**后端 `/draft` 一个字没动**：它现在是 agent 的起草工具。
    renderWithApi(<TopBar />);
    await screen.findByRole("button", { name: "写作助手" });
    expect(screen.queryByRole("button", { name: "AI 起草" })).not.toBeInTheDocument();
  });

  it("有一个进「活动记录」的入口，而它只是个入口 —— 不弹、不红点、不推给作者", async () => {
    // 系统会自动往书里写东西（ADR 0020），所以作者必须**能去看**它写了什么。
    // 但「系统不确定时的默认动作是闭嘴」（约束 8）：这里只放一颗按钮，
    // 不在顶栏上摆待办数、不提示「有 N 条新记录」。
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    await screen.findByRole("button", { name: "写作助手" });

    await user.click(screen.getByRole("button", { name: "活动记录" }));
    expect(useCoords.getState().page).toBe("log");
    expect(document.body.textContent).not.toMatch(/条新|待处理|未读/);
  });

  it("**顶栏只剩两颗开关，都没有「工作台」那一颗** —— 两个都关着就是工作台", async () => {
    // 再摆一颗「回工作台」等于给同一件事第三个入口。两颗都是开关：
    // 点亮 = 进那个模式，再点一下 = 回到自己写。
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    const log = await screen.findByRole("button", { name: "活动记录" });

    expect(screen.queryByRole("button", { name: "工作台" })).not.toBeInTheDocument();
    expect(log).toHaveAttribute("aria-pressed", "false");

    await user.click(log);
    expect(useCoords.getState().page).toBe("log");
    expect(log).toHaveAttribute("aria-pressed", "true");

    await user.click(log); // 再点一下 = 回正文
    expect(useCoords.getState().page).toBe("workbench");
  });

  it("**状态变的是图标和悬浮的那行字，名字不变** —— 名字一变读屏就以为换了一颗按钮", async () => {
    // 悬浮那行字念的是**对面那个模式**（协助模式 ↔ novel-agent 模式）：
    // 一颗开关最该说的是「按下去会到哪儿」。
    // 图标：书合着 = 它在待命；书摊开 = 它上场了（同一个小家伙，不是两个图标）。
    // 名字（`aria-label`）钉死成「写作助手」——那是这块面板一直以来的名字，
    // 后端提示和活动记录都念它；状态走 `aria-pressed`。
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    const bot = await screen.findByRole("button", { name: "写作助手" });

    expect(bot).toHaveAttribute("aria-pressed", "false");
    expect(bot).toHaveAttribute("data-tip", "切换成 novel-agent 模式");
    const closed = bot.innerHTML;

    await user.click(bot);
    expect(await screen.findByRole("button", { name: "写作助手" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(bot).toHaveAttribute("data-tip", "切换成协助模式");
    expect(bot.innerHTML).not.toBe(closed); // 书翻开了
  });

  it("写作助手是一颗开合按钮，**默认关着** —— 同活动记录那条：入口不是通知", async () => {
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    await screen.findByRole("button", { name: "写作助手" });

    expect(useCoords.getState().chatOpen).toBe(false);
    await user.click(screen.getByRole("button", { name: "写作助手" }));
    expect(useCoords.getState().chatOpen).toBe(true);
    // 它开的是**中栏的右半边**，不换页：日志页那一档也留着它。
    expect(useCoords.getState().page).toBe("workbench");
    await user.click(screen.getByRole("button", { name: "写作助手" }));
    expect(useCoords.getState().chatOpen).toBe(false);
  });

  it("**「章节准备」那一页没了** —— 三张读卡是右栏的第二个入口，两个表单写完没人读", async () => {
    // 2026-08-13 删。它来自原始设计稿的「页面二」，但真做出来的那一版里：
    // 上一章局面 / 认知矩阵 / 写作提醒 = 右栏同名 tab；本章目标 + AI 起草长度
    // 写进 localStorage **没有任何人读**（起草请求根本不带长度，走的是后端产品默认档）。
    // 这条断言钉住「它没有偷偷搬回来」——同下面那条出场人物。
    renderWithApi(<TopBar />);
    await screen.findByRole("button", { name: "写作助手" });
    expect(screen.queryByRole("button", { name: "章节准备" })).not.toBeInTheDocument();
  });

  it("**顶栏不再要作者填出场人物** —— 那是写出来的结果，不是写之前的输入", async () => {
    // 它曾经是顶栏第一等公民，等于对作者说「写之前先填这个」。而没名字的配角永远进不了
    // 角色册，新人物是写到那儿才需要的。现在引擎写完之后自己去正文里数（`mentioned.py`），
    // 右栏显示数出来的结果。这条断言钉住「它没有偷偷搬回来」。
    renderWithApi(<TopBar />);
    await screen.findByRole("button", { name: "写作助手" });
    expect(screen.queryByRole("textbox", { name: "本章出场人物" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/出场人物|在场/);
  });

  it("平时机器人图标上没有那个转圈的提示", async () => {
    renderWithApi(<TopBar />);
    const bot = await screen.findByRole("button", { name: "写作助手" });
    expect(bot.querySelector(".bot-thinking")).toBeNull();
  });

  it("续写建议正在生成时，机器人图标右上角会转起来", async () => {
    // 续写请求跟顶栏这颗按钮隔着整棵组件树（一个在 CenterEditor，一个在 TopBar），
    // 中间没有走 `useCoords`——那个 store 只放坐标，不放这种瞬时网络状态。
    // 靠的是 `useIsMutating({ mutationKey: ["continuation"] })`，React Query 自己
    // 全局记着「这个 key 现在有几个请求在飞」，这里造一个同 key 的、故意不 resolve
    // 的 mutation 来模拟「还在飞」。
    function TriggerContinuation() {
      const m = useMutation({
        mutationKey: ["continuation"],
        mutationFn: () => new Promise(() => {}),
      });
      useEffect(() => {
        m.mutate();
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);
      return null;
    }
    renderWithApi(
      <>
        <TopBar />
        <TriggerContinuation />
      </>,
    );
    const bot = await screen.findByRole("button", { name: "写作助手" });
    await waitFor(() => expect(bot.querySelector(".bot-thinking")).not.toBeNull());
  });
});
