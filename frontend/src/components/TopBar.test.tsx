import { useMutation } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { usePaneCollapse } from "../paneCollapse";
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
  // 同上：收起状态也是模块单例，不归位的话「默认展开」那几条会假绿。
  usePaneCollapse.setState({ left: false, right: false });
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
    // 断言两件事：① 它被那根弹簧推到最右；② 它有无障碍名字。
    // ② 是这次换图标最容易悄悄丢的东西：`⚙` 那个字本身就是名字，一颗 <svg> 不是。
    //
    // ⚠️ **2026-09-06：紧挨着齿轮的不再是那根弹簧了。** 作者把「收起右栏」那颗
    // 挪到了设置左边（他的原话：「右边那个移动到设置左边」），于是弹簧和齿轮之间
    // 隔了一颗按钮。**这条钉的仍然是同一件事**——齿轮是最后一个，而弹簧仍然把
    // 「右边这一组」整个推到边上；变的只是那一组现在有两颗。
    // 断言从「齿轮的前一个是弹簧」改成「那一组的第一个前面是弹簧」，不是放宽成
    // 只查 `lastElementChild`：只查那一条的话，谁往中间塞几颗按钮都不会红。
    renderWithApi(<TopBar />);
    const gear = await screen.findByRole("button", { name: "AI 设置" });
    const header = gear.closest("header") as HTMLElement;

    expect(header.lastElementChild).toBe(gear);
    const rightPane = screen.getByRole("button", { name: "右栏" });
    expect(gear.previousElementSibling).toBe(rightPane);
    expect(rightPane.previousElementSibling).toHaveClass("spacer");
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
    // 图标：关着 = 笔尖（作者自己写）；开着 = 小机器人（助手上场了）。
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
    expect(bot.innerHTML).not.toBe(closed); // 笔尖换成了小机器人
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

  it("平时那颗图标不动：没有 `.writing`，也没有角上的转圈", async () => {
    renderWithApi(<TopBar />);
    const bot = await screen.findByRole("button", { name: "写作助手" });
    expect(bot.classList.contains("writing")).toBe(false);
    expect(bot.querySelector(".bot-thinking")).toBeNull();
    // 笔尖的星芒是自己一条 path——续写时动的就是它（`.icon-btn.writing .nib-spark`）。
    expect(bot.querySelector(".nib-spark")).not.toBeNull();
  });

  /** 续写请求跟顶栏这颗按钮隔着整棵组件树（一个在 CenterEditor，一个在 TopBar），
   *  中间没有走 `useCoords`——那个 store 只放坐标，不放这种瞬时网络状态。
   *  靠的是 `useIsMutating({ mutationKey: ["continuation"] })`，React Query 自己
   *  全局记着「这个 key 现在有几个请求在飞」，这里造一个同 key 的、故意不 resolve
   *  的 mutation 来模拟「还在飞」。 */
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

  it("续写建议正在生成时，笔尖那颗星芒闪——不在角上另挂转圈", async () => {
    // 2026-09-12 换成笔尖之后，角上那个转圈恰好压在星芒上，转不转看着一样
    // （作者 2026-09-13：「动画没有了」）。现在动的是星芒本身：按钮拿到 `.writing`，
    // CSS 让 `.nib-spark` 闪；那个转圈在这一档**不渲染**，否则又叠回去了。
    renderWithApi(
      <>
        <TopBar />
        <TriggerContinuation />
      </>,
    );
    const bot = await screen.findByRole("button", { name: "写作助手" });
    await waitFor(() => expect(bot.classList.contains("writing")).toBe(true));
    expect(bot.querySelector(".nib-spark")).not.toBeNull();
    expect(bot.querySelector(".bot-thinking")).toBeNull();
  });

  it("novel-agent 模式下续写在飞时，小机器人右上角照旧转圈", async () => {
    // 机器人头上没有东西挡，角上的转圈在它身上看得清；星芒那条 path 此刻根本不在屏幕上。
    useCoords.setState({ chatOpen: true });
    renderWithApi(
      <>
        <TopBar />
        <TriggerContinuation />
      </>,
    );
    const bot = await screen.findByRole("button", { name: "写作助手" });
    await waitFor(() => expect(bot.querySelector(".bot-thinking")).not.toBeNull());
    expect(bot.querySelector(".nib-spark")).toBeNull();
  });

  // ── 收起两侧栏那两颗（作者 2026-09-06）────────────────────────────────────
  //
  // **这两条钉的是「按钮真的接了线」。** 这个仓库为反过来的形状栽过：
  // `useLanguage.setLabel` 的 store / 持久化 / 兜底全都在，就是没有一颗按钮调它，
  // `grep setLanguage` 只在测试里出现。收起这件事同理——store 自己是可测的，
  // 而「顶栏那颗按钮按下去真的改了它」只有这儿看得见。

  it("顶栏那两颗真的拨得动收起状态 —— 不是两颗摆着好看的图标", async () => {
    const user = userEvent.setup();
    renderWithApi(<TopBar />);

    await user.click(await screen.findByRole("button", { name: "左栏" }));
    expect(usePaneCollapse.getState().left).toBe(true);
    expect(usePaneCollapse.getState().right).toBe(false); // 一颗只管自己那一边

    await user.click(screen.getByRole("button", { name: "右栏" }));
    expect(usePaneCollapse.getState()).toMatchObject({ left: true, right: true });

    await user.click(screen.getByRole("button", { name: "左栏" }));
    expect(usePaneCollapse.getState().left).toBe(false); // 再按一次是展开
  });

  it("名字不随状态变，改的是**按下去会怎样**那句话", async () => {
    // 名字一变，读屏用户会以为按钮换了一颗（而且所有按名字找它的断言会一起烂）。
    // 状态该走 `aria-pressed`，那句悬浮的话该说下一步——同顶栏另外两颗开关的既有规矩。
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    const btn = await screen.findByRole("button", { name: "左栏" });
    expect(btn).toHaveAttribute("aria-pressed", "true"); // 展开着 = 这一栏在
    expect(btn).toHaveAttribute("data-tip", "收起左栏");

    await user.click(btn);
    expect(screen.getByRole("button", { name: "左栏" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "左栏" })).toHaveAttribute("data-tip", "展开左栏");
  });

  it("左右两颗互为镜像，收起 / 展开换的是**翅膀本身的形状**（作者 2026-09-07）", async () => {
    // 侧栏一收起，那一栏整个从屏幕上消失——光看顶栏认不出是哪边收着，所以这一颗
    // 图标（也只有这一颗）得自己把状态说出来。**别把它改回不随状态动**：那样两颗
    // 收起的按钮和两颗展开的按钮长得一模一样。
    //
    // 钉两件事：① 右边那颗是左边那颗的镜像（`transform` 在，而不是另写一份坐标）；
    // ② 收起换的是 path 本身（展开那张 → 收拢那张），不是把同一张图翻个面。
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    const svgOf = (name: string) => screen.getByRole("button", { name }).querySelector("svg")!;
    const mirrored = (name: string) => svgOf(name).querySelector("g")!.hasAttribute("transform");
    const shape = (name: string) => svgOf(name).querySelector("path")!.getAttribute("d");

    await screen.findByRole("button", { name: "左栏" });
    expect(mirrored("左栏")).toBe(false); // 作者给的那半只画的就是左翅
    expect(mirrored("右栏")).toBe(true);
    expect(shape("左栏")).toBe(shape("右栏")); // 同一个状态下两颗共用同一份 path
    // 展开还要**上强调色**（作者 2026-09-07：「展开的时候补个悬浮色」）
    expect(screen.getByRole("button", { name: "左栏" })).toHaveClass("on");

    const spread = shape("左栏");
    await user.click(screen.getByRole("button", { name: "左栏" }));
    expect(shape("左栏")).not.toBe(spread); // 收起 = 换成收拢那张
    expect(shape("右栏")).toBe(spread); // 一颗只管自己那一边
    expect(screen.getByRole("button", { name: "左栏" })).not.toHaveClass("on"); // 收起退回灰
    expect(screen.getByRole("button", { name: "右栏" })).toHaveClass("on");
  });
});

describe("笔尖右边那盏灯（作者 2026-09-13 要的）", () => {
  // 作者第一次用桌面版：「一旦我切换到角色栏……再返回这个状态就丢失」「我都不知道现在是
  // 成功了还是失败了」。灯读的是后台那一行（`/background`，每 4 秒一问），不是某颗按钮
  // 的局部状态，换 tab 换页都还在。
  it("灰：模型服务没配好——点它开到「模型服务」那一栏", async () => {
    const user = userEvent.setup();
    renderWithApi(<TopBar />, [
      { match: /\/api\/settings$/, body: fixtures.settings }, // model_configured: false
      { match: /\/background$/, body: { configured: false, running: [], queued: [] } },
    ]);
    const light = await screen.findByRole("button", { name: /未连接模型/ });
    expect(light).toHaveClass("off");
    expect(light.getAttribute("data-tip")).toMatch(/服务地址、模型和 API 密钥/);
    await user.click(light);
    expect(useCoords.getState().settingsOpen).toBe("link");
  });

  it("绿：配好了、后台闲着", async () => {
    renderWithApi(<TopBar />, [{ match: /\/api\/settings$/, body: fixtures.settingsSaved }]);
    expect(await screen.findByRole("button", { name: "模型已连接" })).toHaveClass("ready");
  });

  it("黄：后台正在整理——那句话按右栏当前那一格说它在生成什么，排队的也报", async () => {
    useCoords.setState({ activeTab: "roster" });
    renderWithApi(<TopBar />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { match: /\/background$/, body: { configured: true, running: [12], queued: [13, 14] } },
    ]);
    const light = await screen.findByRole("button", {
      name: "正在生成角色册（第 12 章），还有 2 章排队",
    });
    expect(light).toHaveClass("busy");
    // 换到「事件」那一格，同一盏灯改口
    useCoords.setState({ activeTab: "review" });
    expect(
      await screen.findByRole("button", { name: "正在生成事件（第 12 章），还有 2 章排队" }),
    ).toBeInTheDocument();
    // 「检验规则」不由模型生成：只报章号
    useCoords.setState({ activeTab: "check" });
    expect(
      await screen.findByRole("button", { name: "正在分析第 12 章，还有 2 章排队" }),
    ).toBeInTheDocument();
  });
});
