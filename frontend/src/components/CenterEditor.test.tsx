import { EditorView } from "@codemirror/view";
import { focusManager } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { withTitle } from "../chapterTitle";
import { IDLE_MS } from "../continuation";
import { CenterEditor } from "./CenterEditor";

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    // 写作助手默认关着（协助模式）。下面「模式二」那一组会把它拨开，这儿归零免得串场。
    chatOpen: false,
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
    const box = screen.getByRole("textbox", { name: "修改章节标题" });
    await user.clear(box);
    await user.type(box, "血脉（改）{Enter}");

    // 顶上那行标题变了——但正文里那行**不该跟着变**（章标那一行不进编辑器，
    // `chapterTitle.ts::splitHeading`）：磁盘上仍是同一行字，屏幕上却只露一次。
    expect(within(screen.getByRole("button", { name: "当前章节" })).getByText("第一章 血脉（改）"))
      .toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector(".cm-line")?.textContent).toBe(
        "萧决在青云城主府第一次听说了血脉秘密的真相。",
      ),
    );
    expect(document.querySelector(".cm-content")?.textContent).not.toContain("血脉（改）");
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
    const box = screen.getByRole("textbox", { name: "修改章节标题" });
    await user.clear(box);
    await user.type(box, "第一章 血脉（我改的）{Enter}");
    expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull();

    focusManager.setFocused(false);
    focusManager.setFocused(true);

    await screen.findByText(/本章已在别处修改/);
    // 他手上那份**一个字都没被动**：标题改动还在顶栏上，正文还是原来那份，
    // 磁盘那边新换的内容没有盖上来（这次的改动落在标题行，所以不会出现在 `.cm-content`
    // 里——章标那一行不进编辑器，`chapterTitle.ts::splitHeading`）。
    expect(screen.getByRole("button", { name: "当前章节" }).textContent).toContain("（我改的）");
    expect(document.querySelector(".cm-content")?.textContent).toContain("李管家什么也没说");
    expect(document.querySelector(".cm-content")?.textContent).not.toContain("磁盘那边换了");
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 没保存的改动的痕迹：减去红、增加绿（作者 2026-09-12：「不管是他写的还是他编辑的，
// 只要没有保存就要有那种编辑的痕迹，红色和绿色代表减去和增加」，指着一张 git diff 的图）
// ══════════════════════════════════════════════════════════════════════════

describe("没保存的改动的痕迹", () => {
  const view = () => EditorView.findFromDOM(document.querySelector(".cm-editor") as HTMLElement)!;
  const loaded = () =>
    waitFor(() =>
      expect(document.querySelector(".cm-content")?.textContent).toContain("李管家什么也没说"),
    );
  const added = () => [...document.querySelectorAll(".cm-line.diff-add")].map((el) => el.textContent);
  const removed = () => [...document.querySelectorAll(".diff-del")].map((el) => el.textContent);

  it("刚打开：和保存版一模一样，一处痕迹都没有", async () => {
    renderWithApi(<CenterEditor />);
    await loaded();
    expect(added()).toEqual([]);
    expect(removed()).toEqual([]);
  });

  it("作者自己改了一段：旧的那一段红（不可编辑，插在原处）、新的那一段绿", async () => {
    renderWithApi(<CenterEditor />);
    await loaded();
    // 第二段「李管家什么也没说。」改成「李管家沉默了很久。」——直接对编辑器下手，
    // 和作者敲键盘走的是同一条 `docChanged`。
    const doc = view().state.doc.toString();
    const from = doc.indexOf("李管家什么也没说。");
    view().dispatch({ changes: { from, to: from + "李管家什么也没说。".length, insert: "李管家沉默了很久。" } });

    await waitFor(() => expect(added()).toEqual(["李管家沉默了很久。"]));
    expect(removed()).toEqual(["李管家什么也没说。"]);
    // 红的那一块在绿的那一行**前面**（git 的顺序：先减后增），而且不是正文的一行——
    // 光标进不去、键盘改不了。
    const red = document.querySelector(".diff-removed")!;
    const green = document.querySelector(".cm-line.diff-add")!;
    expect(red.compareDocumentPosition(green) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(red.closest(".cm-line")).toBeNull();
    // 没动的那一段不涂。
    expect(document.querySelector(".cm-line")?.classList.contains("diff-add")).toBe(false);
    expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull();
  });

  it("加一段：只有绿，没有红；删一段：只有红，挂在它原来的位置", async () => {
    renderWithApi(<CenterEditor />);
    await loaded();
    const end = view().state.doc.length;
    view().dispatch({ changes: { from: end, insert: "\n新写的一段。" } });
    await waitFor(() => expect(added()).toEqual(["新写的一段。"]));
    expect(removed()).toEqual([]);

    // 再把第一段整个删掉（连它后面的换行）。
    const doc = view().state.doc.toString();
    const first = "萧决在青云城主府第一次听说了血脉秘密的真相。\n";
    view().dispatch({ changes: { from: doc.indexOf(first), to: doc.indexOf(first) + first.length } });
    await waitFor(() => expect(removed()).toEqual(["萧决在青云城主府第一次听说了血脉秘密的真相。"]));
    expect(added()).toEqual(["新写的一段。"]);
    // 红块在正文最前面（它原来就在那儿），绿行在最后。
    const red = document.querySelector(".diff-removed")!;
    expect(red.compareDocumentPosition(document.querySelector(".cm-line")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("改回原样，痕迹自己消失——痕迹说的是「和保存版差在哪」，不是「动过没有」", async () => {
    renderWithApi(<CenterEditor />);
    await loaded();
    const doc = view().state.doc.toString();
    const from = doc.indexOf("李管家什么也没说。");
    view().dispatch({ changes: { from, insert: "「" } });
    await waitFor(() => expect(added()).toHaveLength(1));
    view().dispatch({ changes: { from, to: from + 1 } });
    await waitFor(() => expect(added()).toEqual([]));
    expect(removed()).toEqual([]);
  });

  it("删掉半章以上：那一块红折成一行「已删除 N 段」，点开才摊开；接着敲字它不折回去", async () => {
    // 整章重写时旧章全红在上、新章全绿在下——作者要读的新稿被一整屏旧稿顶到底下
    // （`editMarks.ts` 的 `fold`）。判据：一块红里的段数到了保存版的一半以上（不足三段的不折）。
    const user = userEvent.setup();
    const six = ["一", "二", "三", "四", "五", "六"].map((s) => s + "段的正文。").join("\n\n");
    renderWithApi(<CenterEditor />, [
      { match: /\/chapters\/\d+\/text/, body: { ...fixtures.chapterText, markdown: "第一章 血脉\n\n" + six + "\n" } },
    ]);
    await waitFor(() =>
      expect(document.querySelector(".cm-content")?.textContent).toContain("六段的正文。"),
    );
    // 删掉后四段。
    const doc = view().state.doc.toString();
    view().dispatch({ changes: { from: doc.indexOf("三段的正文。"), to: doc.length } });

    const fold = await screen.findByRole("button", { name: "已删除 4 段" });
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(removed()).toEqual([]);
    expect(document.querySelector(".diff-removed")).not.toBeNull();

    await user.click(fold);
    await waitFor(() => expect(removed()).toEqual(["三段的正文。", "四段的正文。", "五段的正文。", "六段的正文。"]));
    expect(screen.getByRole("button", { name: "已删除 4 段" }).getAttribute("aria-expanded")).toBe("true");

    // 点开之后接着改别处：痕迹整个重算（第一段改了 = 多一小块红 + 一行绿），
    // 大的那一块仍然是摊开的（记在编辑器状态里，不在 DOM 上）。
    view().dispatch({ changes: { from: 0, insert: "「" } });
    await waitFor(() => expect(added()).toEqual(["「一段的正文。"]));
    expect(removed()).toEqual(["一段的正文。", "三段的正文。", "四段的正文。", "五段的正文。", "六段的正文。"]);

    // 再点一下折回去：只剩第一段那一小块红（它不足三段，从来不折）。
    await user.click(screen.getByRole("button", { name: "已删除 4 段" }));
    await waitFor(() => expect(removed()).toEqual(["一段的正文。"]));
  });

  it("删掉两段：不折——两块红不算一堵墙", async () => {
    renderWithApi(<CenterEditor />);
    await loaded();
    view().dispatch({ changes: { from: 0, to: view().state.doc.length } });
    await waitFor(() => expect(removed()).toHaveLength(2));
    expect(screen.queryByRole("button", { name: /已删除/ })).toBeNull();
  });

  it("按「保存」：痕迹当场消失（作者的原话），再改就对着刚存的这一版画", async () => {
    const user = userEvent.setup();
    renderWithApi(<CenterEditor />, [
      { method: "PUT", match: /\/chapters\/\d+\/text$/, body: fixtures.chapterSaved },
    ]);
    await loaded();
    const end = view().state.doc.length;
    view().dispatch({ changes: { from: end, insert: "\n新写的一段。" } });
    await waitFor(() => expect(added()).toEqual(["新写的一段。"]));

    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(added()).toEqual([]));
    expect(removed()).toEqual([]);

    // 刚存的那一版现在是保存版：再往下写一段，只有新的那一段绿，「新写的一段。」不再是新的。
    view().dispatch({ changes: { from: view().state.doc.length, insert: "\n又写了一段。" } });
    await waitFor(() => expect(added()).toEqual(["又写了一段。"]));
    expect(removed()).toEqual([]);
  });
});

describe("写作助手开着时的续写（模式二默认没有）", () => {
  // 作者 2026-09-10 看到助手开着、正文里还在往下冒灰字：「模式二这个就不用有这个续写了」，
  // 随后要了一颗开关放行（设置「系统功能」栏）。判据本身在 `continuation.ts`
  // （`continuation.test.ts` 钉纯函数），**这一层钉的是接线**：编辑器真的把面板开没开
  // 交给了那条判据，而且切进模式二那一下，屏幕上和路上的建议都处理掉了。
  //
  // 用真计时器（同 `CodeEditor.test.tsx`）：CodeMirror 自己的测量也挂在计时器上。

  /** 停手之后的那一发落在这条路由上。计数用 `onRequest`（2026-08-30 那条纪律：
   *  看「发没发」，别只看界面）。 */
  const draftRoute = (body: unknown = { text: "他抬起头。" }) => {
    let n = 0;
    return {
      handler: {
        method: "POST" as const,
        match: /\/chapters\/\d+\/draft$/,
        body,
        onRequest: () => {
          n += 1;
        },
      },
      count: () => n,
    };
  };

  /** 这一章的正文进了编辑器（`.cm-content` 里有它），停手才有上文可送。 */
  const textLoaded = () =>
    waitFor(() =>
      expect(document.querySelector(".cm-content")?.textContent).toContain("李管家什么也没说"),
    );

  /** 把光标挪到正文末尾——「移动光标」和「敲字」一样会重开停手计时器。 */
  const restCursorAtEnd = () => {
    const view = EditorView.findFromDOM(document.querySelector(".cm-editor") as HTMLElement)!;
    view.dispatch({ selection: { anchor: view.state.doc.length } });
  };

  it("面板一开，停手就不再问模型；屏幕上挂着的那条灰字也当场收掉", async () => {
    // 先在协助模式下让它问一次：证明设置已经到手、这条路是通的——否则下面那个「0」
    // 可能只是「上限还没回来所以没问」，那是假绿。
    const draft = draftRoute();
    renderWithApi(<CenterEditor />, [draft.handler]);
    await textLoaded();
    restCursorAtEnd();
    await waitFor(() => expect(draft.count()).toBe(1), { timeout: IDLE_MS + 1000 });
    await waitFor(() => expect(document.querySelector(".cm-ghost")).not.toBeNull());

    // 切进 novel-agent 模式。**不碰编辑器**：灰字得是这一下收掉的，不是光标动了才掉的。
    act(() => useCoords.setState({ chatOpen: true }));
    await waitFor(() => expect(document.querySelector(".cm-ghost")).toBeNull());

    // 再停手一次——面板开着，不问。
    restCursorAtEnd();
    await new Promise((done) => setTimeout(done, IDLE_MS * 2));
    expect(draft.count()).toBe(1);
  });

  it("停手时发出去、回来时面板已经开了 —— 那条建议不落地", async () => {
    // 这是作者那句「说了模式二没有续写，它还是冒出来了」最可能的形状：请求要飞几秒，
    // 这几秒里他点开了助手。
    let release: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const draft = draftRoute(async () => {
      await gate;
      return { text: "他抬起头。" };
    });
    renderWithApi(<CenterEditor />, [draft.handler]);
    await textLoaded();
    restCursorAtEnd();
    await waitFor(() => expect(draft.count()).toBe(1), { timeout: IDLE_MS + 1000 });

    act(() => useCoords.setState({ chatOpen: true }));
    release!();
    // 回执到了也不该挂上去。等一小段是因为「没出现」不能用 waitFor 等。
    await new Promise((done) => setTimeout(done, 100));
    expect(document.querySelector(".cm-ghost")).toBeNull();
  });

  it("设置里放行了，面板开着也照问", async () => {
    useCoords.setState({ chatOpen: true });
    const draft = draftRoute();
    renderWithApi(<CenterEditor />, [
      // 配好了的那一份：没配好的话续写根本不开口（`shouldSuggest` 的 `modelConfigured`）。
      { match: /\/api\/settings$/, body: { ...fixtures.settingsSaved, continuation_in_agent_mode: true } },
      draft.handler,
    ]);
    await textLoaded();
    restCursorAtEnd();
    await waitFor(() => expect(draft.count()).toBe(1), { timeout: IDLE_MS + 1000 });
    await waitFor(() => expect(document.querySelector(".cm-ghost")).not.toBeNull());
  });
});
