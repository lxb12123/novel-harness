import { focusManager } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { withTitle } from "../chapterTitle";
import { CenterEditor } from "./CenterEditor";

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    selectedNodeId: null,
    cast: "",
    focusNode: () => {},
    highlight: null,
    setHighlight: () => {},
  });
  // CodeMirror 6 在 jsdom 里需要这两个测量 API。
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

describe("中栏编辑器", () => {
  it("顶栏那行是这一章的**标题**，正文从 /text 拉取（fixture 真出参）", async () => {
    // 章标题 = 正文第一行，而这一行正文来自 `chapterText` 那份真 dump——
    // 所以这条断言同时钉住「显示的标题和正文是同一份东西」。
    renderWithApi(<CenterEditor />);
    expect(await screen.findByText(fixtures.chapters[0].title)).toBeInTheDocument();
  });

  it("改标题 = 改正文第一行，走的是同一条「未保存 → 保存」的路", async () => {
    // 没有 rename 端点：标题在磁盘上就是正文的第一行，第二条写路径 = 第二份真相。
    const user = userEvent.setup();
    // 2026-08-30：这一位曾经整个漏发，后端 422 拒了**所有**保存（`expected_text_sha256`
    // 是必填字段，前端只发了 `markdown`）。没有任何 vitest 看过 PUT 的真实请求体，
    // 所以它在 pytest（后端测试逐条显式传这一位）和这份手写 fixture 之间的缝里躲过了
    // 两边——这条 `onRequest` 断言把那条缝钉住：只看响应对不对不够，得看发出去的是什么。
    let putBody: { markdown?: string; expected_text_sha256?: string } | null = null;
    renderWithApi(<CenterEditor />, [
      {
        method: "PUT",
        match: /\/chapters\/\d+\/text$/,
        onRequest: (init) => {
          putBody = init?.body ? JSON.parse(String(init.body)) : null;
        },
        body: fixtures.chapterSaved,
      },
    ]);
    await screen.findByText(fixtures.chapters[0].title);

    await user.dblClick(screen.getByRole("button", { name: "当前章节" }));
    const box = screen.getByRole("textbox", { name: "改这一章的名字" });
    await user.clear(box);
    await user.type(box, "血脉（改）{Enter}");

    // 两边都得变，因为它们**是同一行字**：顶上那行标题，和正文的第一行。
    expect(within(screen.getByRole("button", { name: "当前章节" })).getByText("第一章 血脉（改）"))
      .toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector(".cm-line")?.textContent).toBe("第一章 血脉（改）"),
    );
    // 「未保存」（水滴徽标）= 这次改动落在编辑器手上那份正文里，等作者按保存
    // （没有第二条写路径；文字 2026-08-30 改成了保存按钮角上的图标，见 icons.tsx）。
    expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull();
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();

    // 按下保存，水滴换成雪花——「已保存并同步」也是这次改的同一批。
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(document.querySelector(".save-badge-icon.snowflake")).not.toBeNull(),
    );
    expect(document.querySelector(".save-badge-icon.droplet")).toBeNull();

    // 发出去的那份必须带着这一位，且是从 `GET .../text` 拿到的那份真哈希，
    // 不是空字符串（空字符串会在服务端比对时打不中现有内容，403/409，不是"忘发"那种静默）。
    expect(putBody).toEqual({
      markdown: withTitle(fixtures.chapterText.markdown, "第一章 血脉（改）"),
      expected_text_sha256: fixtures.chapterText.text_sha256,
    });
  });

  it("🔴 嵌着的场景条为空时**什么都不画** —— 正文上方不多一行常驻文案", async () => {
    // 2026-08-14 反过来了。原来这儿钉的是「空场景条给出下一步提示」，而那句提示
    // （「这一章还没有场景信息。你可以先继续写正文。」）在导进来的真书上是**永久的**：
    // 场景块是一套要作者手写的标记语法，他不去学就一章都不会有。
    // 于是那条「提示」不提示任何东西，只是在每一章的正文上方压一行引擎内部的词。
    renderWithApi(<CenterEditor />);
    // 等正文落地，否则量到的是「还没渲染」而不是「渲染成没有」。
    await waitFor(() => expect(document.querySelector(".cm-line")).not.toBeNull());
    expect(document.body.textContent).not.toMatch(/场景信息|场景 \d/);
  });

  // ── 「读回改动」那颗按钮 2026-08-15 删了 ──────────────────────────────────
  //
  // 这儿原本有三条：它在不在这一行上、按下去说不说后端那句回执、**以及「光是打开
  // 这一章不许自己发 sync」**。
  //
  // 最后那一条钉的是一个决定（「磁盘先、DB 跟」里的那个「跟」是作者的动作，ADR 0007），
  // 而那个决定 2026-08-14 被推翻了：整理跑之前会自己把这一章读回来
  // （今天是保存那条路 `api/app.py::_trigger_refresh` 和手动检查
  // `api/validation.py`），回到标签页时会把整本书对一遍（`reconcile.ts`）。
  //
  // **推翻它的不是「懒得让作者点」，是那颗按钮要求他先理解一件他不该知道的事**——
  // 屏幕上的正文来自磁盘，而库里那份快照来自这颗按钮。他不点，后台整理分析的就是
  // 旧正文，且屏幕上没有任何东西说得出来。
  //
  // 接管它的两条各自有端到端的钉子：`tests/test_chapter_refresh.py`（保存触发那条）
  // 和 `tests/test_reconcile.py` + `frontend/src/reconcile.test.tsx`。

  // ── 切出去改完回来，正文得对上磁盘（2026-08-13）─────────────────────────────
  //
  // 上面那条钉的是「不许自动 sync」（**写**路径，历史里只该有作者自己按下的保存）。
  // 这两条钉的是**读**路径，两件事不能混：不自动写快照，不等于可以显示旧正文。
  //
  // 日常回路是「标签页一直开着 → 切到 WPS 改 → 切回来」，中间**页面一次都没有重新
  // 加载**。不重取，编辑器里就一直是切走之前那份，且屏幕上没有任何东西说它旧了。
  it("在别的软件里改完切回来，正文跟着磁盘那份变（页面没有重新加载过）", async () => {
    const 改过的 = "第一章 血脉\n\n他在 WPS 里把这一段整个重写了。\n";
    let 第几次 = 0;
    renderWithApi(<CenterEditor />, [
      {
        match: /\/chapters\/\d+\/text/,
        body: () =>
          ++第几次 === 1 ? fixtures.chapterText : { ...fixtures.chapterText, markdown: 改过的 },
      },
    ]);
    await waitFor(() =>
      expect(document.querySelector(".cm-content")?.textContent).toContain("李管家什么也没说"),
    );

    focusManager.setFocused(false);
    focusManager.setFocused(true); // ← 作者切回这个标签页

    await waitFor(() =>
      expect(document.querySelector(".cm-content")?.textContent).toContain(
        "他在 WPS 里把这一段整个重写了",
      ),
    );
  });

  it("但手上有没保存的改动时**先问一句**，不闷头盖掉", async () => {
    // 「一律采纳」在这一档是错的：作者手上那份是他刚敲的字。`editorDoc.diskChange`
    // 的两档（干净 → 采纳、脏 → 拦一句）必须一起活着，只留一半就是在拿他的稿子赌。
    const user = userEvent.setup();
    let 第几次 = 0;
    renderWithApi(<CenterEditor />, [
      {
        match: /\/chapters\/\d+\/text/,
        body: () =>
          ++第几次 === 1
            ? fixtures.chapterText
            : { ...fixtures.chapterText, markdown: "第一章 血脉\n\n磁盘那边换了。\n" },
      },
    ]);
    await waitFor(() =>
      expect(document.querySelector(".cm-content")?.textContent).toContain("李管家什么也没说"),
    );

    // 在编辑器里敲两个字 = 手上这份脏了。
    await user.dblClick(screen.getByRole("button", { name: "当前章节" }));
    const box = screen.getByRole("textbox", { name: "改这一章的名字" });
    await user.clear(box);
    await user.type(box, "第一章 血脉（我改的）{Enter}");
    expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull();

    focusManager.setFocused(false);
    focusManager.setFocused(true);

    await screen.findByText(/这一章在别处变过了/);
    // 他手上那份**一个字都没被动**。
    expect(document.querySelector(".cm-content")?.textContent).toContain("（我改的）");
  });
});
