import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useShelf } from "../bookshelf";
import { useCoords } from "../store";
import { BookShelf } from "./BookShelf";

const [second, first] = fixtures.projectsTwo; // 库里的顺序：契约样书、青云记
const twoBooks = [{ match: /\/api\/projects$/, body: fixtures.projectsTwo }];

beforeEach(() => {
  useCoords.setState({ projectId: first.id, chapter: 1, selectedNodeId: null });
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

  it("只有正在看的那本会去拉章目录 —— 两本 722 章的书不该一次拉两份", async () => {
    open();
    const other = await section(second.name);
    expect(within(other).getByText(/点一下书名/)).toBeInTheDocument();
    expect(within(other).queryByText(fixtures.chapters[0].title)).toBeNull();
  });

  it("⋯ 里的「从左边移除」只是拿下架子，书还在库里", async () => {
    open();
    const book = await section(second.name);
    within(book).getByRole("button", { name: `《${second.name}》的更多操作` }).click();
    (await screen.findByRole("button", { name: "从左边移除" })).click();

    await waitFor(() => expect(screen.queryByRole("button", { name: second.name })).toBeNull());
    expect(useShelf.getState().hidden).toEqual([second.id]);

    // **必须有回头路**：不然「移除」就等于把书弄丢了（这是撤掉切书弹窗之后仅剩的入口）。
    (await screen.findByText("放回来")).click();
    expect(await screen.findByRole("button", { name: second.name })).toBeInTheDocument();
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
    (await screen.findByRole("button", { name: "从左边移除" })).click();

    await waitFor(() => expect(useCoords.getState().projectId).toBe(second.id));
    expect(await screen.findByRole("button", { name: second.name })).toBeInTheDocument();
  });

  it("左边只剩一本时，移除是灰的 —— 拿掉它就没有换书的入口了", async () => {
    open([{ match: /\/api\/projects$/, body: [first] }]);
    const book = await section(first.name);
    within(book).getByRole("button", { name: `《${first.name}》的更多操作` }).click();
    const item = await screen.findByRole("button", { name: "从左边移除" });
    expect(item).toBeDisabled();
    expect(item).toHaveAttribute("title", expect.stringContaining("就剩这一本"));
  });

  it("一章都没有的书，说的是下一步而不是留一片空白", async () => {
    open([...twoBooks, { match: /\/chapters$/, body: [] }]);
    const book = await section(first.name);
    expect(await within(book).findByText(/还没有章节/)).toBeInTheDocument();
  });
});
