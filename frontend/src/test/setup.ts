import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { type Language, useLanguage } from "../language";

// jsdom 的 document 在同一个文件的多个 test 之间是共享的：不卸载，上一个 test 渲染的
// 抽屉会留在 DOM 里，下一个 test 的 getByText 就可能命中它——那种绿是假的。
afterEach(cleanup);

// ── localStorage ─────────────────────────────────────────────────────────────
// Node 22+ 自带一个实验性的全局 localStorage，它会盖掉 jsdom 装的那个；而没有
// `--localstorage-file` 时它是个空壳——连 getItem 都不存在。
// 工作台真的用 localStorage 记东西（栏宽、起草长度、本章 brief），不补上的话那些代码
// 在测试里只会一路走进 catch / `?.` 分支，**测出来的绿是假的**（原来它在
// 曾经躺在某个单独的测试文件里，第二个用到存储的组件出现时就该提上来）。
// **一份就够，别再往单个测试文件里拷第二份。**
function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(String(key), String(value)),
  };
}

if (typeof globalThis.localStorage?.getItem !== "function") {
  const storage = memoryStorage();
  for (const host of new Set<object>([globalThis, window])) {
    Object.defineProperty(host, "localStorage", { configurable: true, value: storage });
  }
}

// 每个 test 从空存储开始：不清的话「没存过应该给默认值」这类断言会被上一个 test 写进去的
// 值染绿/染红，而且测试顺序一换结论就变。
beforeEach(() => localStorage.clear());

// ── 界面语言：测试默认中文 ───────────────────────────────────────────────────
// `useLanguage` 的初始值来自 `navigator.language`（jsdom 默认 `en-US`），而这个仓库
// 绝大多数既有断言在国际化第四批之前写的时候，默认输出就是中文——不重置的话它们会
// 因为「今天恰好在英文环境里跑」而红，红的原因和被测行为毫无关系。
// **测中文行为不用做任何事**（这就是默认值）；测英文行为的用例自己
// `useLanguage.getState().setLanguage("en")`。
//
// **走 `setLanguage(...)` 这个公开方法，不直接 `setState({ language: "zh" })`**：
// 后一种写法是 `tests/test_no_language_literal_in_frontend.py` 那道守卫按形状
// 拦的东西（`language: "zh"` 这个对象字面量键值对）——那道守卫防的是**书的语言**
// 被前端焊死绕过 `Project.language`，跟这儿要重置的**界面语言**是两件事，但守卫
// 是按形状扫全树、不认字段语义（这仓库为"守卫认字段名"吃过亏，见它自己的
// docstring），撞上纯属巧合，改成调方法而不是碰对象字面量就避开了，不用给守卫开口子。
const DEFAULT_TEST_LANGUAGE: Language = "zh";
beforeEach(() => useLanguage.getState().setLanguage(DEFAULT_TEST_LANGUAGE));

// ── CodeMirror 6 要量字，而 jsdom 的 `Range` 上没有那两个测量方法 ──────────────
// 少了它们，CM6 的一次 measure 会在 `requestAnimationFrame` 里抛 TypeError。那个抛
// **发生在断言之外**：轻则一屏和被测行为毫无关系的 stderr，重则被 vitest 记成一条
// unhandled error 让整轮红——而红的原因是「jsdom 不排版」，不是代码错了。
// 补一个量到零的空实现：jsdom 里本来就没有排版，**量到零是诚实的，抛出去不是**。
// 同上面 localStorage 那段：**一份就够，别往单个测试文件里拷第二份。**
if (typeof Range.prototype.getClientRects !== "function") {
  const empty = () => Object.assign([] as DOMRect[], { item: () => null }) as unknown as DOMRectList;
  Range.prototype.getClientRects = empty;
  Range.prototype.getBoundingClientRect = () => new DOMRect();
}
