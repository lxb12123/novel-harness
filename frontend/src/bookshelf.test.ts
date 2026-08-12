import { beforeEach, describe, expect, it } from "vitest";
import { nextAfterRemoving, readShelf, removable, shelved, useShelf } from "./bookshelf";

// store 是模块级单例，`localStorage.clear()`（setup.ts）清不掉它的内存态——
// 不重置的话，上一个 test 拿掉的书会跟着进下一个 test，而且顺序一换结论就变。
beforeEach(() => useShelf.setState({ hidden: [], collapsed: [] }));

const A = { id: "project:A", name: "第一本" };
const B = { id: "project:B", name: "第二本" };
const C = { id: "project:C", name: "第三本" };
const ALL = [A, B, C];

// 存储替身 + 每个 test 前清空都在 src/test/setup.ts。

describe("书架上摆哪几本", () => {
  it("默认全摆着 —— 别处新建的书不用先「加进来」才看得见", () => {
    expect(shelved(ALL, [], A.id)).toEqual(ALL);
  });

  it("拿掉的那几本不显示", () => {
    expect(shelved(ALL, [B.id], A.id)).toEqual([A, C]);
  });

  it("正在看的那本永远在架子上，哪怕它在拿掉名单里", () => {
    // 否则：右栏显示着它的内容、左边却找不到它，连「⋯」入口都没有——作者弄不回来。
    expect(shelved(ALL, [A.id, B.id], A.id)).toEqual([A, C]);
  });

  it("最后一本不许拿掉 —— 拿掉就没有书名行，也就没有换书的入口了", () => {
    expect(removable(ALL, [], A.id)).toBe(true);
    expect(removable(ALL, [B.id, C.id], A.id)).toBe(false);
  });

  it("拿掉当前这本时，说得出该切到哪一本", () => {
    expect(nextAfterRemoving(ALL, [], A.id, A.id)).toBe(B.id);
    expect(nextAfterRemoving(ALL, [B.id], A.id, A.id)).toBe(C.id);
    expect(nextAfterRemoving([A], [], A.id, A.id)).toBeNull();
  });
});

describe("摆法记得住", () => {
  it("拿掉 / 放回 / 折叠都会落盘，重开还是那个样子", () => {
    useShelf.getState().remove(B.id);
    useShelf.getState().toggleCollapsed(A.id);
    expect(readShelf()).toEqual({ hidden: [B.id], collapsed: [A.id] });

    // 放回来是「移除」唯一的回头路（切书弹窗已撤），折叠状态不该被它顺手清掉。
    useShelf.getState().restoreAll();
    expect(readShelf()).toEqual({ hidden: [], collapsed: [A.id] });
  });

  it("重复拿掉同一本不会拿两次", () => {
    useShelf.getState().remove(B.id);
    useShelf.getState().remove(B.id);
    expect(readShelf().hidden).toEqual([B.id]);
  });

  it("存坏了当作一本都没拿掉 —— 空架子比坏架子更糟", () => {
    localStorage.setItem("nh.bookshelf.v1", "{ 这不是 JSON");
    expect(readShelf()).toEqual({ hidden: [], collapsed: [] });
  });

  it("存进去的不是字符串数组时也不会把界面带崩", () => {
    localStorage.setItem("nh.bookshelf.v1", JSON.stringify({ hidden: [1, null, "project:A"] }));
    expect(readShelf()).toEqual({ hidden: ["project:A"], collapsed: [] });
  });
});
