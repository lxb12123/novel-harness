import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { describe, expect, it } from "vitest";
import {
  acceptSuggestion,
  acceptSuggestionChunk,
  dismissSuggestion,
  setSuggestion,
  suggestionField,
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
