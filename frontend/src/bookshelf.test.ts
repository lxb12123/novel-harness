import { beforeEach, describe, expect, it } from "vitest";
import { ApiError } from "./api/client";
import { devTerms, shellLines } from "./test/screenGuard";
import {
  deleteChapterError,
  newChapterError,
  nextAfterRemoving,
  readShelf,
  removable,
  shelved,
  useShelf,
} from "./bookshelf";

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

// ── 「＋ 新起一章」没成时说哪句话（2026-08-14）──────────────────────────────
//
// 这一组钉的是那条唯一的例外：**只有「服务上没这条路」那一档由前端说话**，
// 别的一律照抄后端。放宽这一条 = 又造出一份会和后端漂开的措辞源
//（这个仓库为此删过 `correctionError.ts`）。

describe("新起一章失败时那句话", () => {
  it("旧版服务（404 且没有错误码）—— 这一句只能前端说，因为后端不认识这条路", () => {
    const said = newChapterError(new ApiError(404, { message: "Not Found" }));
    expect(said).toMatch(/旧的一版/);
    // **不许出现任何一条命令**：产品的最终用户不碰命令行。
    expect(shellLines(said)).toEqual([]);
    expect(devTerms(said)).toEqual([]);
  });

  it("带错误码的 404（书没了）照抄后端，不当成「程序是旧的」", () => {
    const said = newChapterError(
      new ApiError(404, { error: "project_not_found", message: "这本书不在库里了。" }),
    );
    expect(said).toBe("这本书不在库里了。");
  });

  it("后端说得出话就原样渲染（比如另一个窗口刚建过）", () => {
    const said = newChapterError(
      new ApiError(409, { error: "chapter_exists", message: "这一章刚刚已经被建出来了。" }),
    );
    expect(said).toBe("这一章刚刚已经被建出来了。");
  });

  it("断网 / 说不出话的那一档才回落到那句通用的", () => {
    expect(newChapterError(new TypeError("Failed to fetch"))).toMatch(/再点一次/);
    expect(newChapterError(new ApiError(500, {}))).toMatch(/再点一次/);
  });
});

describe("删一章失败时那句话", () => {
  it("**拒绝的明细一个字都不改**", () => {
    // 那串数字是作者判断「这一章还连着什么」的唯一依据。换成一句笼统的「删不掉」，
    // 他会去文件夹里自己动手删那个文件——而那条路上引擎的记忆一条都不会被清理。
    const refusal = "第 1 章上还记着东西（证据 3 / 关系 2 / 情节 1 / 抽取 1 / 提案 0）";
    const said = deleteChapterError(new ApiError(409, { error: "chapter_in_use", message: refusal }));
    expect(said).toBe(refusal);
    expect(devTerms(said)).toEqual([]);
  });

  it("旧版服务（404 且没有错误码）—— 同新起一章那条，判据一样精确", () => {
    const said = deleteChapterError(new ApiError(404, { message: "Not Found" }));
    expect(said).toMatch(/旧的一版/);
    expect(shellLines(said)).toEqual([]);
    expect(devTerms(said)).toEqual([]);
  });

  it("带错误码的 404（那一章本来就没了）照抄后端", () => {
    const said = deleteChapterError(
      new ApiError(404, { error: "chapter_missing", message: "第 9 章已经不在了。" }),
    );
    expect(said).toBe("第 9 章已经不在了。");
  });

  it("断网 / 说不出话的那一档才回落到那句通用的", () => {
    expect(deleteChapterError(new TypeError("Failed to fetch"))).toMatch(/再点一次/);
    expect(deleteChapterError(new ApiError(500, {}))).toMatch(/再点一次/);
  });
});
