import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
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
  it("只能从已经存在的章节中切换", async () => {
    const user = userEvent.setup();
    renderWithApi(<TopBar />);

    const chapter = await screen.findByRole("combobox", { name: "当前章节" });
    await waitFor(() => {
      expect(within(chapter).getAllByRole("option")).toHaveLength(fixtures.chapters.length);
    });
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();

    await user.selectOptions(chapter, String(fixtures.chapters[1].number));
    expect(useCoords.getState().chapter).toBe(fixtures.chapters[1].number);
  });

  it("换章会把**刚离开的**那一章交给后台整理，而作者看不到这件事", async () => {
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    const chapter = await screen.findByRole("combobox", { name: "当前章节" });
    await waitFor(() =>
      expect(within(chapter).getAllByRole("option")).toHaveLength(fixtures.chapters.length),
    );
    const spy = vi.spyOn(globalThis, "fetch");

    await user.selectOptions(chapter, String(fixtures.chapters[1].number));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([url, init]) =>
            // 第 1 章 —— 他刚离开的那一章，不是刚进入的那一章
            String(url).endsWith("/chapters/1/autopilot") &&
            String((init as RequestInit | undefined)?.method) === "POST",
        ),
      ).toBe(true),
    );
    // 后台在干活这件事一个字都不该出现在界面上。
    expect(document.body.textContent).not.toMatch(/后台|整理中|总结中/);
  });

  it("章标长到装不下时收成「…」，全名靠悬浮读得到", async () => {
    // 中文章标经常长过下拉框。<select> 默认硬切在半个字上，看起来像标题本身是坏的。
    renderWithApi(<TopBar />);
    const chapter = await screen.findByRole("combobox", { name: "当前章节" });
    await waitFor(() =>
      expect(chapter).toHaveAttribute("title", fixtures.chapters[0].title),
    );
  });

  it("只展示可用能力和产品文案，不泄漏演示内容或研发术语", async () => {
    renderWithApi(<TopBar />);

    expect(await screen.findByRole("combobox", { name: "当前章节" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AI 规划" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/萧决|顾清音|李管家|M2|valid_from|实验状态/);
  });

  it("**「AI 起草」那个抽屉已经删了** —— 一个功能不留两个入口", async () => {
    // 它是填表式的：先填「这一场要写什么」+ 在场角色，再点按钮出一整章。
    // 而在场人物是**写出来的结果**不是写之前的输入（ADR 0018），「接下来写什么」
    // 也不该由一个表单承载。起草归模式二的 agent 面板——在那儿它是一次工具调用，
    // 不是一个界面。**后端 `/draft` 一个字没动**：它现在是 agent 的起草工具。
    renderWithApi(<TopBar />);
    await screen.findByRole("combobox", { name: "当前章节" });
    expect(screen.queryByRole("button", { name: "AI 起草" })).not.toBeInTheDocument();
  });

  it("有一个进「活动记录」的入口，而它只是个入口 —— 不弹、不红点、不推给作者", async () => {
    // 系统会自动往书里写东西（ADR 0020），所以作者必须**能去看**它写了什么。
    // 但「系统不确定时的默认动作是闭嘴」（约束 8）：这里只放一颗按钮，
    // 不在顶栏上摆待办数、不提示「有 N 条新记录」。
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    await screen.findByRole("combobox", { name: "当前章节" });

    await user.click(screen.getByRole("button", { name: "活动记录" }));
    expect(useCoords.getState().page).toBe("log");
    expect(document.body.textContent).not.toMatch(/条新|待处理|未读/);
  });

  it("写作助手是一颗开合按钮，**默认关着** —— 同活动记录那条：入口不是通知", async () => {
    const user = userEvent.setup();
    renderWithApi(<TopBar />);
    await screen.findByRole("combobox", { name: "当前章节" });

    expect(useCoords.getState().chatOpen).toBe(false);
    await user.click(screen.getByRole("button", { name: "写作助手" }));
    expect(useCoords.getState().chatOpen).toBe(true);
    // 它开的是**中栏的右半边**，不换页：日志页那一档也留着它。
    expect(useCoords.getState().page).toBe("workbench");
    await user.click(screen.getByRole("button", { name: "写作助手" }));
    expect(useCoords.getState().chatOpen).toBe(false);
  });

  it("**在章节准备页上按它会回工作台** —— 那一页换掉整块中栏，不回去就是一颗死按钮", async () => {
    const user = userEvent.setup();
    useCoords.setState({ page: "prep", chatOpen: false });
    renderWithApi(<TopBar />);
    await screen.findByRole("combobox", { name: "当前章节" });

    await user.click(screen.getByRole("button", { name: "写作助手" }));
    expect(useCoords.getState()).toMatchObject({ chatOpen: true, page: "workbench" });
  });

  it("**顶栏不再要作者填出场人物** —— 那是写出来的结果，不是写之前的输入", async () => {
    // 它曾经是顶栏第一等公民，等于对作者说「写之前先填这个」。而没名字的配角永远进不了
    // 花名册，新人物是写到那儿才需要的。现在引擎写完之后自己去正文里数（`mentioned.py`），
    // 右栏显示数出来的结果。这条断言钉住「它没有偷偷搬回来」。
    renderWithApi(<TopBar />);
    await screen.findByRole("combobox", { name: "当前章节" });
    expect(screen.queryByRole("textbox", { name: "本章出场人物" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/出场人物|在场/);
  });
});
