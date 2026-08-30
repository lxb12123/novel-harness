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
  it("挂着建议时按真实的 ArrowRight 键 —— 吃一口，不是移动光标", () => {
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
