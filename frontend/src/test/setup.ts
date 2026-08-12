import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

// jsdom 的 document 在同一个文件的多个 test 之间是共享的：不卸载，上一个 test 渲染的
// 抽屉会留在 DOM 里，下一个 test 的 getByText 就可能命中它——那种绿是假的。
afterEach(cleanup);

// ── localStorage ─────────────────────────────────────────────────────────────
// Node 22+ 自带一个实验性的全局 localStorage，它会盖掉 jsdom 装的那个；而没有
// `--localstorage-file` 时它是个空壳——连 getItem 都不存在。
// 工作台真的用 localStorage 记东西（栏宽、起草长度、本章 brief），不补上的话那些代码
// 在测试里只会一路走进 catch / `?.` 分支，**测出来的绿是假的**（原来它在
// DraftLengthControls.test.tsx 里躺着一份，第二个用到存储的组件出现时就该提上来）。
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
