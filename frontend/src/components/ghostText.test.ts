import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { describe, expect, it } from "vitest";
import {
  acceptSuggestion,
  acceptSuggestionChunk,
  acceptSuggestionUpToMark,
  dismissSuggestion,
  setPreviewMark,
  setSuggestion,
  suggestionField,
  toggleGhostPreview,
  updateGhostPreviewFromPoint,
} from "./ghostText";

// 灰字建议的核心不变式：**它不是文档内容**。
// 这一条坏掉的形态最贵：建议一旦进了 doc，`onChange` 会把这一章标成「未保存」，
// 作者不接受就走人时磁盘上多出一段模型写的字——而他以为自己什么都没写。

function view(doc: string, suggestion?: { text: string; pos: number }) {
  const v = new EditorView({
    state: EditorState.create({ doc, extensions: [suggestionField] }),
  });
  if (suggestion) v.dispatch({ effects: setSuggestion.of(suggestion) });
  return v;
}

describe("行内建议", () => {
  it("挂上去的时候一个字符都不进文档", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    expect(v.state.doc.toString()).toBe("萧决推开门。");
    expect(v.state.field(suggestionField)?.text).toBe("屋里没有点灯。");
  });

  it("按 Tab 才落字，且落在光标处", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    expect(acceptSuggestion(v)).toBe(true);
    expect(v.state.doc.toString()).toBe("萧决推开门。屋里没有点灯。");
    expect(v.state.field(suggestionField)).toBeNull();
  });

  it("Esc 丢掉它，文档一个字不动", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    expect(dismissSuggestion(v)).toBe(true);
    expect(v.state.doc.toString()).toBe("萧决推开门。");
    expect(v.state.field(suggestionField)).toBeNull();
  });

  it("没有建议时 Tab / Esc 让路，不吞掉别人的快捷键", () => {
    const v = view("萧决推开门。");
    expect(acceptSuggestion(v)).toBe(false);
    expect(dismissSuggestion(v)).toBe(false);
  });

  it("**作者一敲键，建议自动作废** —— 这就是「取消」，不是增量失效", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    v.dispatch({ changes: { from: 6, insert: "他" } });
    expect(v.state.field(suggestionField)).toBeNull();
  });

  it("光标一动也作废 —— 建议是对着某个位置算的，位置变了它就不成立", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    v.dispatch({ selection: { anchor: 2 } });
    expect(v.state.field(suggestionField)).toBeNull();
  });
});

describe("按 → 逐口接受", () => {
  it("吃一口只落一个词，剩下的还挂在建议里，光标停在刚落的字后面", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    expect(acceptSuggestionChunk(v)).toBe(true);
    expect(v.state.doc.toString()).toBe("萧决推开门。屋"); // 「屋」｜「里没有点灯。」
    expect(v.state.field(suggestionField)).toEqual({ text: "里没有点灯。", pos: 7 });
    expect(v.state.selection.main.head).toBe(7);
  });

  it("连续吃到底：文档拼建议剩文，字数从头到尾不丢不多", () => {
    const full = "屋里没有点灯。";
    let v = view("萧决推开门。", { text: full, pos: 6 });
    while (v.state.field(suggestionField)?.text) {
      expect(acceptSuggestionChunk(v)).toBe(true);
    }
    expect(v.state.doc.toString()).toBe("萧决推开门。" + full);
  });

  it("最后一口吞完整份建议时退化成整份接受 —— 不留一个空字符串的建议在场上", () => {
    // 「点灯。」一个词加一个句号，nextChunkLength 会一次报出整段长度。
    const v = view("萧决推开门。屋里没有", { text: "点灯。", pos: 10 });
    expect(acceptSuggestionChunk(v)).toBe(true);
    expect(v.state.doc.toString()).toBe("萧决推开门。屋里没有点灯。");
    expect(v.state.field(suggestionField)).toBeNull(); // 不是 {text: "", pos: ...}
  });

  it("`Intl.Segmenter` 在某些环境里抛错时，退化成整段接受而不是凭空清空建议", () => {
    // 真出这种事，`run` 要是不返回 true，CM6 就不会挡掉浏览器原生的 → 默认动作——
    // 原生挪光标会同步出一个没带 `setSuggestion` 效果的事务，照样把建议清空，
    // 但一个字都没落下。这条钉的是「宁可整段接受，也不能连字都没落就把建议丢了」。
    const originalSegmenter = globalThis.Intl.Segmenter;
    // @ts-expect-error 故意装成一个坏掉的 Intl.Segmenter
    globalThis.Intl.Segmenter = class {
      segment(): never {
        throw new Error("boom");
      }
    };
    try {
      const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
      expect(acceptSuggestionChunk(v)).toBe(true);
      expect(v.state.doc.toString()).toBe("萧决推开门。屋里没有点灯。");
      expect(v.state.field(suggestionField)).toBeNull();
    } finally {
      // @ts-expect-error 换回真的
      globalThis.Intl.Segmenter = originalSegmenter;
    }
  });

  it("没有建议时让路，不吞掉方向键正常挪动光标", () => {
    const v = view("萧决推开门。");
    expect(acceptSuggestionChunk(v)).toBe(false);
  });
});

describe("Ctrl 取景模式 + Enter 按切点接受", () => {
  it("按 Ctrl 切进取景模式，切点从 0 开始；再按一次退出，切点跟着丢", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    expect(v.state.field(suggestionField)?.previewMark).toBeUndefined();
    expect(toggleGhostPreview(v)).toBe(false); // 纯修饰键，不拦截、照常冒泡
    expect(v.state.field(suggestionField)?.previewMark).toBe(0);
    toggleGhostPreview(v);
    expect(v.state.field(suggestionField)?.previewMark).toBeNull();
  });

  it("没有建议时 Ctrl 什么都不做（不会凭空造出一个取景状态）", () => {
    const v = view("萧决推开门。");
    expect(toggleGhostPreview(v)).toBe(false);
    expect(v.state.field(suggestionField)).toBeNull();
  });

  it("切点定在哪，Enter 就把哪之前的一截落成正文，之后的继续挂着", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯，他愣住了。", pos: 6 });
    toggleGhostPreview(v); // 进取景模式，切点先是 0
    v.dispatch({ effects: setPreviewMark.of(4) }); // 模拟鼠标划到「屋里没有」之后
    expect(acceptSuggestionUpToMark(v)).toBe(true);
    expect(v.state.doc.toString()).toBe("萧决推开门。屋里没有");
    expect(v.state.field(suggestionField)).toEqual({
      text: "点灯，他愣住了。",
      pos: 10,
      previewMark: undefined,
    });
    expect(v.state.selection.main.head).toBe(10);
  });

  it("切点划到整段末尾时退化成整段接受，不留取景状态", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    toggleGhostPreview(v);
    v.dispatch({ effects: setPreviewMark.of(7) }); // 划到「屋里没有点灯。」的末尾
    expect(acceptSuggestionUpToMark(v)).toBe(true);
    expect(v.state.doc.toString()).toBe("萧决推开门。屋里没有点灯。");
    expect(v.state.field(suggestionField)).toBeNull();
  });

  it("切点还是 0（刚进取景模式、鼠标还没划过灰字）时 Enter 让路，正常换行", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    toggleGhostPreview(v); // 切点 = 0，还没落任何字
    expect(acceptSuggestionUpToMark(v)).toBe(false);
    expect(v.state.doc.toString()).toBe("萧决推开门。"); // 一个字都没落
  });

  it("没进取景模式时 Enter 让路，跟这个功能毫无关系", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    // 没调 toggleGhostPreview，previewMark 是 undefined
    expect(acceptSuggestionUpToMark(v)).toBe(false);
  });

  it("没有建议时 Enter 让路，不吞掉正常换行", () => {
    const v = view("萧决推开门。");
    expect(acceptSuggestionUpToMark(v)).toBe(false);
  });

  it("鼠标划到灰字范围外、或者取景模式没开，updateGhostPreviewFromPoint 什么都不做", () => {
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    // 没进取景模式：previewMark 是 undefined，不是数字，函数应该直接让路。
    expect(updateGhostPreviewFromPoint(v, 0, 0)).toBe(false);
    expect(v.state.field(suggestionField)?.previewMark).toBeUndefined();
  });

  it("鼠标划到灰字 DOM 文本节点的第 N 个字符，切点跟着更新（模拟浏览器的坐标定位）", () => {
    // jsdom 没有真排版，`caretPositionFromPoint` 测不出真实坐标对不对——这条钉的是
    // 「浏览器给了一个文本节点 + 节点内偏移之后，textOffsetFromPoint 的换算对不对」，
    // 真实的「这个坐标点对应哪个节点」只能靠人在真浏览器里验（already 手测过）。
    const v = view("萧决推开门。", { text: "屋里没有点灯。", pos: 6 });
    toggleGhostPreview(v);
    const wrap = v.dom.querySelector(".cm-ghost-wrap");
    const textNode = wrap!.firstChild!.firstChild!;
    expect(textNode.textContent).toBe("屋里没有点灯。");
    const doc = wrap!.ownerDocument;
    const original = doc.caretPositionFromPoint;
    // `getClientRect` 是真实 CaretPosition 接口要求的方法，这里用不到，随便给个空实现。
    doc.caretPositionFromPoint = () =>
      ({ offsetNode: textNode, offset: 3, getClientRect: () => new DOMRect() }) as CaretPosition;
    try {
      expect(updateGhostPreviewFromPoint(v, 10, 10)).toBe(false); // 不消费这次 mousemove
      expect(v.state.field(suggestionField)?.previewMark).toBe(3);
    } finally {
      doc.caretPositionFromPoint = original;
    }
  });
});
