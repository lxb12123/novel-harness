import { render, waitFor } from "@testing-library/react";
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
type Idle = { before: string; pos: number; hasSelection: boolean };

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
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(Array.from(seen[0].before)).toHaveLength(800);

    // ③ 换一个窗口更大的模型 —— **同一个编辑器**，上文自己变长。
    //    写死一个常量的实现在这一步必红：它两次都送同样多。
    view.rerender(props(4_000));
    ref.current!.select(4_999, 4_999);
    await waitFor(() => expect(seen).toHaveLength(2));
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

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(Array.from(seen[0].before)).toHaveLength(1_200);
  });
});
