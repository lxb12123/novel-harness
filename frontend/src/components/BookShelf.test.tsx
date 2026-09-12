import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useShelf } from "../bookshelf";
import { useCoords } from "../store";
import { BookShelf } from "./BookShelf";

const [second, first] = fixtures.projectsTwo; // 库里的顺序：契约样书、青云记
const twoBooks = [{ match: /\/api\/projects$/, body: fixtures.projectsTwo }];

beforeEach(() => {
  useCoords.setState({ projectId: first.id, chapter: 1, selectedNodeId: null, cursorFor: null });
  useShelf.setState({ hidden: [], collapsed: [] });
});

function open(extra: Parameters<typeof renderWithApi>[1] = twoBooks) {
  return renderWithApi(<BookShelf onOpenChapter={vi.fn()} />, extra);
}

/** 一本书那一段（书名行 + 它的章目录）。 */
async function section(name: string) {
  const title = await screen.findByRole("button", { name });
  return title.closest(".book") as HTMLElement;
}

describe("侧栏书架", () => {
  it("「＋ 新书 / 导入」在最上面 —— 它是这一栏的第一个动作", async () => {
    open();
    const buttons = await screen.findAllByRole("button");
    expect(buttons[0]).toHaveAccessibleName("＋ 新书 / 导入");
  });

  it("书名和「章目录」在同一行，长书名靠省略号收住而不是换行", async () => {
    const longName = "这是一本名字长到能把整行撑爆的小说·卷一·风起青萍之末";
    open([
      {
        match: /\/api\/projects$/,
        body: [{ ...first, name: longName }],
      },
    ]);
    const row = await screen.findByRole("button", { name: longName });
    // 悬浮看全名：截断之后这是作者唯一能读到完整书名的地方。
    expect(row).toHaveAttribute("title", longName);
    // 箭头、图标、书名、「章目录」全在这一个按钮里 —— 它们是同一个命中区。
    expect(within(row).getByText("章目录")).toBeInTheDocument();
    expect(within(row).getByText(longName)).toBeInTheDocument();
  });

  it("整行都能点开章目录，不用去瞄那个小箭头", async () => {
    // 作者的原话：「点击那么一行就可以，而不是最左边那一个小点」。
    open();
    const book = await section(first.name);
    const row = within(book).getByRole("button", { name: first.name });
    expect(await within(book).findByText(fixtures.chapters[0].title)).toBeInTheDocument();
    expect(row).toHaveAttribute("aria-expanded", "true");

    row.click(); // 点行的任意处 = 收起
    await waitFor(() => expect(within(book).queryByText(fixtures.chapters[0].title)).toBeNull());
    expect(row).toHaveAttribute("aria-expanded", "false");

    row.click(); // 再点 = 展开
    expect(await within(book).findByText(fixtures.chapters[0].title)).toBeInTheDocument();
  });

  it("点另一本书的那一行 = 打开它，并且章目录是展开的", async () => {
    open();
    const other = await section(second.name);
    within(other).getByRole("button", { name: second.name }).click();

    await waitFor(() => expect(useCoords.getState().projectId).toBe(second.id));
    expect(within(other).getByRole("button", { name: second.name })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("导入的第二本是**多一段**，不是把原来那本换掉", async () => {
    open();
    expect(await screen.findByRole("button", { name: first.name })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: second.name })).toBeInTheDocument();
  });

  it("换书不再经过任何弹窗 —— 架子上就列着，点那一行即可", async () => {
    open();
    expect(screen.queryByRole("dialog")).toBeNull();
    const other = await section(second.name);
    within(other).getByRole("button", { name: second.name }).click();
    await waitFor(() => expect(useCoords.getState().projectId).toBe(second.id));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("架子上每一本摊开着的书都列着章目录 —— 不用先点书名把它「打开」", async () => {
    // 原先只有当前那本会列，别的书里摆一句「点一下书名，看这本书的章目录」：
    // 箭头朝下、里面却没有目录 = **一次假的展开**，而作者已经点开它了。
    open();
    for (const book of [await section(first.name), await section(second.name)]) {
      expect(await within(book).findByText(fixtures.chapters[0].title)).toBeInTheDocument();
    }
    expect(screen.queryByText(/点一下书名/)).toBeNull();
  });

  it("「在读的是这一章」只画在当前那本上 —— 另一本里同号的那一章不是他正看着的", async () => {
    open(); // 两本书吃的是同一份章目录夹具，所以第 1 章在两边都在
    const title = fixtures.chapters[0].title;
    // 「on」画在**整行**上（`.ch`），章名只是行里的一个 span。
    const here = (await within(await section(first.name)).findByText(title)).closest(".ch");
    const there = (await within(await section(second.name)).findByText(title)).closest(".ch");
    expect(here).toHaveClass("on");
    expect(there).not.toHaveClass("on");
  });

  it("点另一本书的某一章 = 连书带章一起翻过去，落在他点的那一章", async () => {
    useCoords.setState({ chapter: 3 });
    const onOpenChapter = vi.fn();
    renderWithApi(<BookShelf onOpenChapter={onOpenChapter} />, twoBooks);

    const other = await section(second.name);
    (await within(other).findByText(fixtures.chapters[0].title)).click();

    await waitFor(() => expect(useCoords.getState().projectId).toBe(second.id));
    expect(useCoords.getState().chapter).toBe(1);
    // 光标就此落定：不置这个标记的话，App 会把这一下当成一次换书，
    // 转手把第 1 章顶成那本书的最后一章（`App.test.tsx` 里有那一条端到端的）。
    expect(useCoords.getState().cursorFor).toBe(second.id);
    // 换书**不算**「上一章写完了」——那条路会给刚离开的那一章发一次后台整理。
    expect(onOpenChapter).not.toHaveBeenCalled();
  });

  it("收起来的那本不去拉章目录 —— 722 章的书有两本时，那才是该省的地方", async () => {
    useShelf.setState({ hidden: [], collapsed: [second.id] });
    open();
    const other = await section(second.name);
    await within(await section(first.name)).findByText(fixtures.chapters[0].title);
    expect(within(other).queryByText(fixtures.chapters[0].title)).toBeNull();
    expect(within(other).queryByText("加载中…")).toBeNull();
  });

  it("⋯ 里的「移除本书目录」只是拿下架子，书还在库里", async () => {
    open();
    const book = await section(second.name);
    within(book).getByRole("button", { name: `《${second.name}》的更多操作` }).click();
    (await screen.findByRole("button", { name: "移除本书目录" })).click();

    await waitFor(() => expect(screen.queryByRole("button", { name: second.name })).toBeNull());
    expect(useShelf.getState().hidden).toEqual([second.id]);

    // **必须有回头路**：不然「移除」就等于把书弄丢了（这是撤掉切书弹窗之后仅剩的入口）。
    (await screen.findByText("放回来")).click();
    expect(await screen.findByRole("button", { name: second.name })).toBeInTheDocument();
  });

  it("⋯ 里能看到并且能改这本书的语言（国际化第一批 ②）", async () => {
    // fixture 里两本书都是 zh（真 dump：`language` 是新加的一列，默认值）。
    // 点「English」之后**重新 GET 一次** `/api/projects` 会拿到 en——
    // 用调用计数模拟「PATCH 成功、invalidateQueries 触发重取」这条真实回路，
    // 而不是断言 fetch 被传了哪个 body（那要求 harness 支持检查 init，这里没有）。
    let getCount = 0;
    open([
      {
        match: /\/api\/projects$/,
        body: () =>
          ++getCount === 1 ? fixtures.projectsTwo : [{ ...first, language: "en" }, second],
      },
      { method: "PATCH", match: /\/language$/, body: { ...first, language: "en" } },
    ]);
    const book = await section(first.name);
    within(book).getByRole("button", { name: `《${first.name}》的更多操作` }).click();

    const zh = await screen.findByRole("button", { name: "中文" });
    const en = screen.getByRole("button", { name: "English" });
    expect(zh).toHaveAttribute("aria-pressed", "true");
    expect(en).toHaveAttribute("aria-pressed", "false");

    en.click();
    await waitFor(() => expect(getCount).toBeGreaterThan(1));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "English" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
  });

  it("一本都没拿掉的时候，不摆那条「放回来」", async () => {
    open();
    await section(first.name);
    expect(screen.queryByText("放回来")).toBeNull();
  });

  it("移除当前这本会先切到另一本，不会把作者留在空屏上", async () => {
    open();
    const book = await section(first.name);
    within(book).getByRole("button", { name: `《${first.name}》的更多操作` }).click();
    (await screen.findByRole("button", { name: "移除本书目录" })).click();

    await waitFor(() => expect(useCoords.getState().projectId).toBe(second.id));
    expect(await screen.findByRole("button", { name: second.name })).toBeInTheDocument();
  });

  it("左边只剩一本也能移除 —— 架子空了，靠「放回来」找回来，不靠硬留着", async () => {
    open([{ match: /\/api\/projects$/, body: [first] }]);
    const book = await section(first.name);
    within(book).getByRole("button", { name: `《${first.name}》的更多操作` }).click();
    const item = await screen.findByRole("button", { name: "移除本书目录" });
    expect(item).not.toBeDisabled();
    item.click();

    // 书名行没了（架子空了），但作者没被硬切走：没有下一本可切，中栏/右栏原样
    // 继续显示这本书——他不会因为左边空了就看不见自己正在写的东西。
    await waitFor(() => expect(screen.queryByRole("button", { name: first.name })).toBeNull());
    expect(useShelf.getState().hidden).toEqual([first.id]);
    expect(useCoords.getState().projectId).toBe(first.id);

    // 空状态那句话自己就带着「放回来」——底下那条常驻提示这时候不重复出现（纯噪音）。
    expect(screen.queryByText("放回来")).toBeNull();
    (await screen.findByText("把移除的书放回来")).click();
    expect(await screen.findByRole("button", { name: first.name })).toBeInTheDocument();
  });

  it("一章都没有的书，说的是下一步而不是留一片空白", async () => {
    open([...twoBooks, { match: /\/chapters$/, body: [] }]);
    const book = await section(first.name);
    expect(await within(book).findByText(/还没有章节/)).toBeInTheDocument();
  });

  // ── 「＋ 新起一章」（2026-08-14）───────────────────────────────────────────
  //
  // 在此之前浏览器里**没有任何一条路**能新起一章：引擎有（`chapters/NNNN.md` 摆在那儿、
  // `sync` 读得回来），界面没有。作者只能去文件夹里手工建一个补零对的文件名——
  // 而那正是这个产品说要替他挡掉的东西（README 里那位「用 WPS 不想碰命令行」的作者）。

  it("每本书的章目录末尾都有那颗加号", async () => {
    open();
    const book = await section(first.name);
    expect(await within(book).findByRole("button", { name: "新起一章" })).toBeInTheDocument();
  });

  it("**它只有一个加号，名字靠悬浮出来** —— 而且不是那个等一秒的原生 tooltip", async () => {
    // 同顶栏那颗齿轮（`TopBar.test.tsx` 里同一条）：作者说过他「以为没有名字」，
    // 因为原生 `title` 要等约一秒才浮出来。名字由 CSS 读 `data-tip` 画，.12s 就出来。
    // 两个都留着的话，悬浮会同时冒出两个气泡。
    open();
    const book = await section(first.name);
    const add = await within(book).findByRole("button", { name: "新起一章" });

    // 图标本身不进无障碍树：进了的话读屏会在按钮名字之外再念一遍它（同顶栏那颗齿轮）。
    expect(add.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(add).toHaveAttribute("data-tip", "新起一章");
    expect(add).not.toHaveAttribute("title");
  });

  it("一章都没有的书也能直接新起一章 —— 不是只能去导入 TXT", async () => {
    open([...twoBooks, { match: /\/chapters$/, body: [] }]);
    const book = await section(first.name);
    expect(await within(book).findByRole("button", { name: "新起一章" })).toBeInTheDocument();
  });

  it("按下去 = 建一章 + **直接翻过去**，章号由后端给", async () => {
    // 停在原来那一章 = 让作者自己再去目录里找一次他刚建的东西。
    const onOpen = vi.fn();
    renderWithApi(<BookShelf onOpenChapter={onOpen} />, [
      ...twoBooks,
      { method: "POST", match: /\/chapters$/, body: { number: 4, title: "第四章" } },
    ]);
    const book = await section(first.name);
    (await within(book).findByRole("button", { name: "新起一章" })).click();

    await waitFor(() => expect(onOpen).toHaveBeenCalledWith(4));
  });

  it("建不成的时候说一句，而不是静静地什么都没发生", async () => {
    renderWithApi(<BookShelf onOpenChapter={vi.fn()} />, [
      ...twoBooks,
      { method: "POST", match: /\/chapters$/, status: 500, body: {} },
    ]);
    const book = await section(first.name);
    (await within(book).findByRole("button", { name: "新起一章" })).click();

    expect(await within(book).findByText(/没能新起一章/)).toBeInTheDocument();
  });
});

