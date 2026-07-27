import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom 的 document 在同一个文件的多个 test 之间是共享的：不卸载，上一个 test 渲染的
// 抽屉会留在 DOM 里，下一个 test 的 getByText 就可能命中它——那种绿是假的。
afterEach(cleanup);
