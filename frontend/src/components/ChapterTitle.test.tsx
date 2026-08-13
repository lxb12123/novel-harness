import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { ChapterTitle } from "./ChapterTitle";

// 中栏顶栏那行章标题。它一个人干三件事（读 / 挑章 / 改名），
// 所以这儿的每一条都在钉「这三件事没有互相踩到」。

const LINE = fixtures.chapters[0].title; // 「第一章 血脉」——正文第一行，也就是章标题

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1 });
});

const open = (line: string | null = LINE, onRename = vi.fn()) => {
  renderWithApi(<ChapterTitle line={line} onRename={onRename} />);
  return onRename;
};

const trigger = () => screen.getByRole("button", { name: "当前章节" });

describe("章标题", () => {
  it("显示的是这一章的标题，不是干巴巴的「第 N 章」", async () => {
    open();
    expect(await screen.findByText(LINE)).toBeInTheDocument();
    // 收成「…」之后，悬浮是作者唯一能读到全名的地方（同左栏那一行书名）。
    expect(trigger()).toHaveAttribute("title", expect.stringContaining(LINE));
  });

  it("**它不是一个 <select>** —— 那个白框没有跟着搬下来", async () => {
    open();
    await screen.findByText(LINE);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("点开能挑章，挑完就换过去", async () => {
    const user = userEvent.setup();
    open();
    await screen.findByText(LINE);
    await user.click(trigger());

    const list = await screen.findByRole("listbox", { name: "章节" });
    expect(within(list).getAllByRole("option")).toHaveLength(fixtures.chapters.length);
    await user.click(within(list).getByRole("option", { name: /第二章/ }));

    expect(useCoords.getState().chapter).toBe(fixtures.chapters[1].number);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("换章会把**刚离开的**那一章交给后台整理，而作者看不到这件事", async () => {
    // 这条随控件一起从顶栏搬下来：换章的入口只有这一个，它掉了就没有别的地方会发那次整理。
    const user = userEvent.setup();
    open();
    await screen.findByText(LINE);
    await user.click(trigger());
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(await screen.findByRole("option", { name: /第二章/ }));

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
    expect(document.body.textContent).not.toMatch(/后台|整理中|总结中/);
  });

  it("能按章号或标题找 —— 722 章的书靠滚是找不到的", async () => {
    const user = userEvent.setup();
    open();
    await screen.findByText(LINE);
    await user.click(trigger());

    await user.type(await screen.findByRole("textbox", { name: "找章节" }), "对峙");
    const list = screen.getByRole("listbox", { name: "章节" });
    expect(within(list).getAllByRole("option")).toHaveLength(1);
    expect(within(list).getByRole("option", { name: /对峙/ })).toBeInTheDocument();
  });

  it("一条都没搜到时说清楚，不留一张空单子", async () => {
    const user = userEvent.setup();
    open();
    await screen.findByText(LINE);
    await user.click(trigger());
    await user.type(await screen.findByRole("textbox", { name: "找章节" }), "不存在的章");
    expect(screen.getByText(/没有匹配/)).toBeInTheDocument();
  });

  it("双击只改名字 —— **章号原地不动，不进输入框**", async () => {
    // 作者的原话：「双击之后就带章节名，左边的章号原地不动」。
    // 这不只是界面偏好：章号是后端切章认的那一段，它进不了框 = 这条路改不坏切章。
    const user = userEvent.setup();
    const onRename = open();
    await screen.findByText(LINE);

    await user.dblClick(trigger());
    const box = screen.getByRole("textbox", { name: "改这一章的名字" });
    expect(box).toHaveValue("血脉"); // 不是「第一章 血脉」
    expect(screen.getByText("第一章")).toBeInTheDocument(); // 章号还在屏幕上，原位
    // 双击那两下不该把挑章的单子留在屏幕上。
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await user.clear(box);
    await user.type(box, "血脉（改）{Enter}");
    // 交回去的是**整行**（章号原样带回），因为落盘落的是正文第一行。
    expect(onRename).toHaveBeenCalledWith("第一章 血脉（改）");
  });

  it("Esc 原样退出 —— 改一半反悔时唯一的退路", async () => {
    const user = userEvent.setup();
    const onRename = open();
    await screen.findByText(LINE);

    await user.dblClick(trigger());
    await user.type(screen.getByRole("textbox", { name: "改这一章的名字" }), "乱改{Escape}");
    expect(onRename).not.toHaveBeenCalled();
    expect(await screen.findByText(LINE)).toBeInTheDocument();
  });

  it("名字清空 = 这一章只剩章号（真书里大把无题章），不是把标题弄没", async () => {
    const user = userEvent.setup();
    const onRename = open();
    await screen.findByText(LINE);

    await user.dblClick(trigger());
    await user.clear(screen.getByRole("textbox", { name: "改这一章的名字" }));
    await user.type(screen.getByRole("textbox", { name: "改这一章的名字" }), "   {Enter}");
    expect(onRename).toHaveBeenCalledWith("第一章");
  });

  it("认不出章号的那一行：整行可改，但**清空它什么都不做**", async () => {
    // 首行是正文的稿子（没有「第N章」）。这时清空 = 下一行正文顶上来当章标题。
    const user = userEvent.setup();
    const onRename = open("他说第三章很好看。");
    await screen.findByText("他说第三章很好看。");

    await user.dblClick(trigger());
    const box = screen.getByRole("textbox", { name: "改这一章的名字" });
    expect(box).toHaveValue("他说第三章很好看。");
    await user.clear(box);
    await user.type(box, "{Enter}");
    expect(onRename).not.toHaveBeenCalled();
  });

  it("**编辑器手上还不是这一章的字时，不许改标题** —— 那会写进上一章的正文", async () => {
    const user = userEvent.setup();
    const onRename = open(null);
    // 这时念的是章目录里那一条（磁盘上的真相），照样有得看、照样能挑章。
    expect(await screen.findByText(LINE)).toBeInTheDocument();

    await user.dblClick(trigger());
    expect(screen.queryByRole("textbox", { name: "改这一章的标题" })).not.toBeInTheDocument();
    expect(onRename).not.toHaveBeenCalled();
  });
});
