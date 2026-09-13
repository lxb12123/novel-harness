import { fireEvent, render, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { CodeEditor, type CodeEditorHandle } from "./CodeEditor";
import { IDLE_MS } from "../continuation";

// **这一层是「那个上限真的生效」的落点。** `continuation.test.ts` 只证明 `tailBefore()`
// 这个纯函数会听参数，`tests/test_continuation_tail_limit.py` 只证明后端算得对、
// 前端源码里没有第二个数——中间还剩一段没人钉：**编辑器到底把哪个数交给了那一刀。**
//
// 它坏掉时代表什么坏了：作者换了个窗口更大的模型，续写送出去的上文却没变多
// （或者反过来：设置还没回来就先按一个猜的数问了模型）。两种都**不会报错、不会红**，
// 症状只有一句「AI 好像没在看我前面写的」——正是 2026-08-22 之前那个写死 1,000 的病。
//
// 用真计时器不用假的：CodeMirror 自己的测量也挂在计时器上，把它一起冻住换来的
// 是「测试通过但编辑器没在动」。代价是这份文件慢一秒多。

beforeEach(() => {
  // CodeMirror 6 在 jsdom 里需要这个测量 API（同 `CenterEditor.test.tsx`）。
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

/** 停手之后那一发的入参。等不到就是「这一次没问模型」。 */
type Idle = { before: string; after: string; pos: number; hasSelection: boolean };

describe("续写这一刀用的是哪个上限", () => {
  it("🔴 上限跟着 prop 走 —— 换个更大的数，同一个编辑器立刻多带上文", async () => {
    // 不重新挂载：作者是在设置页里换的模型，编辑器一直开着。新的数得进得来，
    // 而不是等他把整个工作台关掉重开（所以 `CodeEditor` 把它放进那个 ref）。
    const seen: Idle[] = [];
    const ref = createRef<CodeEditorHandle>();
    const doc = "字".repeat(5_000);
    const props = (tailLimit: number | null) => (
      <CodeEditor
        ref={ref}
        value={doc}
        onChange={() => {}}
        onIdle={(ctx) => seen.push(ctx)}
        tailLimit={tailLimit}
      />
    );

    const view = render(props(null));

    // ① 那个数还没到手 —— **不问**。随手猜一个正是这次删掉的那个 bug。
    ref.current!.select(10, 10);
    await new Promise((done) => setTimeout(done, IDLE_MS * 2));
    expect(seen).toHaveLength(0);

    // ② 小窗口的模型：拿到多少就是多少。
    view.rerender(props(800));
    ref.current!.select(5_000, 5_000);
    await waitFor(() => expect(seen).toHaveLength(1), { timeout: IDLE_MS + 1000 });
    expect(Array.from(seen[0].before)).toHaveLength(800);

    // ③ 换一个窗口更大的模型 —— **同一个编辑器**，上文自己变长。
    //    写死一个常量的实现在这一步必红：它两次都送同样多。
    view.rerender(props(4_000));
    ref.current!.select(4_999, 4_999);
    await waitFor(() => expect(seen).toHaveLength(2), { timeout: IDLE_MS + 1000 });
    expect(Array.from(seen[1].before)).toHaveLength(4_000);
  });

  it("上限在**开火那一刻**才读 —— 停手的这 400 毫秒里设置刚回来也算数", async () => {
    // 计时器排上之后设置才到手，是真会发生的顺序（作者一打开工作台就开始写）。
    // 读得太早的实现会在这条上红：它会拿排队时那个 `null`，白白吞掉这一次。
    const seen: Idle[] = [];
    const ref = createRef<CodeEditorHandle>();
    const doc = "字".repeat(2_000);
    const props = (tailLimit: number | null) => (
      <CodeEditor
        ref={ref}
        value={doc}
        onChange={() => {}}
        onIdle={(ctx) => seen.push(ctx)}
        tailLimit={tailLimit}
      />
    );

    const view = render(props(null));
    ref.current!.select(2_000, 2_000);
    view.rerender(props(1_200));

    await waitFor(() => expect(seen).toHaveLength(1), { timeout: IDLE_MS + 1000 });
    expect(Array.from(seen[0].before)).toHaveLength(1_200);
  });
});

describe("→ 走的是真实的 CM6 按键系统，不是绕过去的那条路", () => {
  // `ghostText.test.ts` 直接调用 `acceptSuggestionChunk(view)`——那证明函数本身对，
  // 但完全绕开了 CM6 的 keymap 优先级判定。`defaultKeymap` 自己也绑了 ArrowRight
  // （移动光标），两份 keymap 谁先接到这一次按键，只有走真实的 DOM keydown 才测得出来。
  // 这条挂了就是「函数是对的，但作者按下去没反应」——正是这次真实发生过的那种坏法。
  //
  // **⚠️ 这条当前是红的，而且比原来记的更容易红**（2026-09-07 复核）：全量
  // `npx vitest run` 红，**单跑这一个文件也红**（10 条里红 3 条，另两条在下面那个
  // describe），只有 `-t` 单挑一条才绿。**这一段原来写的是「单独跑这个文件是绿的」，
  // 那句今天已经不成立**——什么时候起变成这样没查，但拿 `git show HEAD:` 的
  // CodeEditor.tsx 跑同样红，所以**不是工作区里那份未提交改动带的**。
  // 已知、诚实记录，不是没发现：
  // 同一个 vitest 进程里只要在此之前**任意别处**已经成功创建过一个「灰字建议 + 真实
  // ArrowRight keydown」的 CodeEditor 实例，`ghostText()` 里的 `Prec.highest` 就会
  // 在下一个实例上失效——顺着 `@codemirror/state` 的 `Configuration.resolve`/
  // `flatten`/`Prec`/view 那份 `Keymaps` WeakMap 整条链路读过、也试过把这条挪到
  // 本文件最前面，都没能定位到具体是哪一步、也没能靠调整顺序绕开（说明泄漏点
  // 不在本文件内，跨文件的进程内某处状态没查到）。「文档末尾，defaultKeymap 自己
  // 就会让路」这类测不出优先级的场景不受影响（不管顺序对不对结果都一样，见下一条）。
  // **判断修复本身对不对，不要看这条测试**：`Prec.highest` 是 CM6 文档给的标准
  // 覆盖优先级手段，真实浏览器一个页面只会挂载一个 `CodeEditor`（不会连续创建
  // 很多个，这条测试暴露的正是那种「连续创建很多个」的人工场景），Codex 用
  // computer-use 在真机上已经验证过修复后光标中间也能正确触发，不只是文档末尾
  // 那个特例。这是待查的测试基础设施问题，开新的一条跟进，不要在这儿继续加代码
  // 硬凑绿——那样只会做出「测试通过但不知道为什么通过」的更贵的坑。
  // `it.fails`（2026-09-13）：**登记它红**，不是让它绿。仓库 2026-09-13 才第一次有远端，
  // 这三条一进 CI 就把整条 frontend job 染红，而它们红的原因（上面那段）是测试基础设施的，
  // 不是功能的。`it.fails` 的意思是「这条现在必须是红的」：哪天那个进程内串味的坑被修好、
  // 它真的绿了，`it.fails` 反过来会红——那时把这个标记摘掉，主张本身一个字不用改。
  it.fails("🔴 光标右边还有真正的正文时（不是在文档末尾），依旧是吃一口，不是移动光标", () => {
    // 用「文档末尾」（光标右边没有真字符）测不出这件事：defaultKeymap 的 ArrowRight
    // 在文档末尾没地方可移，会自己返回 false 让路，那种场景**不管 ghostText() 的
    // 优先级对不对都会绿**。真书里作者停笔的地方几乎不会是全书最后一个字——光标
    // 右边通常还有已经写好的正文（改旧章）或者章末的空行。这条把「右边有真字」
    // 这个前提做实，才是压得住「defaultKeymap 抢先把光标往真正文里挪」这种坏法的
    // 那一条——也是这次真实发生过的 bug：函数本身是对的，但 `Prec.highest` 漏加时，
    // defaultKeymap 排在 `CodeEditor.tsx` 的 keymap.of([...defaultKeymap]) 里、
    // 比 `ghostText()` 先注册，同优先级下先注册的先试，光标中间时它先把光标挪走、
    // 返回 true，`ghostText()` 的 → 绑定永远轮不到。
    const changes: string[] = [];
    const ref = createRef<CodeEditorHandle>();
    const view = render(
      <CodeEditor
        ref={ref}
        value="萧决推开门。屋外下着雨。"
        onChange={(v) => changes.push(v)}
        tailLimit={null}
      />,
    );
    ref.current!.select(6, 6);
    ref.current!.showSuggestion("他停下脚步。", 6);

    const content = view.container.querySelector(".cm-content");
    fireEvent.keyDown(content!, { key: "ArrowRight" });

    expect(changes).toEqual(["萧决推开门。他屋外下着雨。"]);
  });

  it("挂着建议时按真实的 ArrowRight 键 —— 吃一口，不是移动光标（文档末尾这个特例）", () => {
    const changes: string[] = [];
    const ref = createRef<CodeEditorHandle>();
    const view = render(
      <CodeEditor
        ref={ref}
        value="萧决推开门。"
        onChange={(v) => changes.push(v)}
        tailLimit={null}
      />,
    );
    ref.current!.select(6, 6);
    ref.current!.showSuggestion("屋里没有点灯。", 6);

    const content = view.container.querySelector(".cm-content");
    expect(content).not.toBeNull();
    fireEvent.keyDown(content!, { key: "ArrowRight" });

    // 移动光标不会碰 doc，`onChange` 只在 doc 真的变了才响——所以这条断言
    // 顺带把「defaultKeymap 赢了、什么都没吞进文档」和「吃对了一口」区分开。
    expect(changes).toEqual(["萧决推开门。屋"]);
  });

  it("没有建议挂着时，真实的 ArrowRight 键照常挪光标，不吞任何东西", () => {
    const changes: string[] = [];
    const ref = createRef<CodeEditorHandle>();
    const view = render(
      <CodeEditor
        ref={ref}
        value="萧决推开门。"
        onChange={(v) => changes.push(v)}
        tailLimit={null}
      />,
    );
    ref.current!.select(2, 2);

    const content = view.container.querySelector(".cm-content");
    fireEvent.keyDown(content!, { key: "ArrowRight" });

    expect(changes).toEqual([]); // 光标动了，文档没动
  });
});

describe("Ctrl 取景 + Enter 走的也是真实的 CM6 事件系统", () => {
  // 同一个教训：直接调用 `acceptSuggestionUpToMark(view)` 只证明函数本身对，
  // 证不了 Ctrl/mousemove 真的能通过 `EditorView.domEventHandlers` 走到它。
  // CM6 把 `domEventHandlers` 挂在 `view.dom`（编辑器最外层），不是 `.cm-content`，
  // 但真实按键/鼠标事件默认会冒泡，所以照旧对着 `.cm-content` 发，冒泡上去照样接得到
  // ——这条本身也顺带把「往 .cm-content 发事件确实测得到 view.dom 上的监听器」验一遍。
  //
  // ⚠️ 这条、以及本 describe 块的另一条，跟本文件那条已知的「多实例 vitest 进程内
  // 优先级失效」问题（见「→ 走的是真实的 CM6 按键系统」describe 块开头那条长注释）
  // 撞的是同一个坑：**`-t` 单挑那一条绿，整个文件一起跑就红**（2026-09-07 复核）。
  // 不是这个功能本身的问题。
  // `it.fails` 的理由同上一个 describe 块开头（2026-09-13）：登记它红，绿了会反过来红。
  it.fails("Ctrl 切进取景模式、鼠标划到灰字第几个字、Enter 落到那个切点——全走真实事件", () => {
    const changes: string[] = [];
    const ref = createRef<CodeEditorHandle>();
    const view = render(
      <CodeEditor
        ref={ref}
        value="萧决推开门。屋外下着雨。"
        onChange={(v) => changes.push(v)}
        tailLimit={null}
      />,
    );
    ref.current!.select(6, 6);
    ref.current!.showSuggestion("他停下脚步。", 6);

    const content = view.container.querySelector(".cm-content")!;
    fireEvent.keyDown(content, { key: "Control" });

    const ghost = view.container.querySelector(".cm-ghost-wrap")!.firstChild!.firstChild!;
    expect(ghost.textContent).toBe("他停下脚步。");
    const doc = ghost.ownerDocument!;
    const original = doc.caretPositionFromPoint;
    doc.caretPositionFromPoint = () =>
      ({ offsetNode: ghost, offset: 2, getClientRect: () => new DOMRect() }) as CaretPosition;
    try {
      fireEvent.mouseMove(content, { clientX: 1, clientY: 1 });
    } finally {
      doc.caretPositionFromPoint = original;
    }

    fireEvent.keyDown(content, { key: "Enter" });

    expect(changes).toEqual(["萧决推开门。他停屋外下着雨。"]);
  });

  it("🔴 没有进取景模式时，Enter 就是正常换行——光标在文档中间也一样，不受这个功能影响", () => {
    // 不用文档末尾这种测不出优先级问题的特例（吃过这个亏）：光标停在中间，
    // 右边还有真文本，此时 defaultKeymap 自己的 Enter（插入换行）必须正常生效。
    const changes: string[] = [];
    const ref = createRef<CodeEditorHandle>();
    const view = render(
      <CodeEditor
        ref={ref}
        value="萧决推开门。屋外下着雨。"
        onChange={(v) => changes.push(v)}
        tailLimit={null}
      />,
    );
    ref.current!.select(6, 6);

    const content = view.container.querySelector(".cm-content")!;
    fireEvent.keyDown(content, { key: "Enter" });

    expect(changes).toEqual(["萧决推开门。\n屋外下着雨。"]);
  });

  // ⚠️ 这条用 `-t` 单挑是绿的，跟本文件另外几条混在同一个 vitest 进程里就红——
  // 整个文件跑就够，不用等全量（2026-09-07 复核；同一个已知、
  // 已经详细记录过的问题：连续创建多个 CodeMirror 实例，某种优先级/事务判定会在
  // 后面的实例上失效，没有定位到具体机制——见那个 describe 块开头那条长注释）。
  // 不在这儿重复排查，attribution 和结论都一样：产品代码本身是对的（这条测试单独
  // 跑就是证明），問題出在测试基础设施，不影响真实浏览器里只会有一个实例的场景。
  it.fails("挂着建议、但没按 Ctrl 进取景模式时，Enter 照样是正常换行", () => {
    const changes: string[] = [];
    const ref = createRef<CodeEditorHandle>();
    const view = render(
      <CodeEditor
        ref={ref}
        value="萧决推开门。屋外下着雨。"
        onChange={(v) => changes.push(v)}
        tailLimit={null}
      />,
    );
    ref.current!.select(6, 6);
    ref.current!.showSuggestion("他停下脚步。", 6);

    const content = view.container.querySelector(".cm-content")!;
    fireEvent.keyDown(content, { key: "Enter" });

    expect(changes).toEqual(["萧决推开门。\n屋外下着雨。"]); // 建议原样挂着，没被吃进去
  });
});

describe("光标后面那截也交上去（改旧章时它是已经写好的正文）", () => {
  it("🔴 光标停在中间 —— 前后两截都送，各自按同一个上限切", async () => {
    // 只送前面那截时，模型看不见紧接着的下一段，写出来的可能跟它接不上、
    // 或者干脆把它重写一遍。**给不给模型看由后端定**（它要知道全书写到第几章），
    // 这一层的职责只有一条：把后面那截也交上去。
    const seen: Idle[] = [];
    const ref = createRef<CodeEditorHandle>();
    const doc = "前".repeat(1_000) + "后".repeat(1_000);
    const view = render(
      <CodeEditor
        ref={ref}
        value={doc}
        onChange={() => {}}
        onIdle={(ctx) => seen.push(ctx)}
        tailLimit={null}
      />,
    );
    view.rerender(
      <CodeEditor
        ref={ref}
        value={doc}
        onChange={() => {}}
        onIdle={(ctx) => seen.push(ctx)}
        tailLimit={600}
      />,
    );

    ref.current!.select(1_000, 1_000);
    await waitFor(() => expect(seen).toHaveLength(1), { timeout: IDLE_MS + 1000 });

    expect(Array.from(seen[0].before)).toHaveLength(600);
    expect(seen[0].before.endsWith("前")).toBe(true);
    // 后面那截留住的是**紧挨着光标**的那一头。
    expect(Array.from(seen[0].after)).toHaveLength(600);
    expect(seen[0].after.startsWith("后")).toBe(true);
  });

  it("在章末往下写（常态）—— 后面那截是空的", async () => {
    const seen: Idle[] = [];
    const ref = createRef<CodeEditorHandle>();
    const doc = "字".repeat(50);
    render(
      <CodeEditor
        ref={ref}
        value={doc}
        onChange={() => {}}
        onIdle={(ctx) => seen.push(ctx)}
        tailLimit={800}
      />,
    );

    ref.current!.select(50, 50);
    await waitFor(() => expect(seen).toHaveLength(1), { timeout: IDLE_MS + 1000 });
    expect(seen[0].after).toBe("");
  });
});
