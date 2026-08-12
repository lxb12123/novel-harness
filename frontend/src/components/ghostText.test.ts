import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { describe, expect, it } from "vitest";
import {
  acceptSuggestion,
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
