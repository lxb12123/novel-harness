import { StateEffect, StateField, type Extension } from "@codemirror/state";
import { Decoration, EditorView, WidgetType, keymap } from "@codemirror/view";
import { nextChunkLength } from "../continuation";

// 行内建议的「灰字」（ADR 0015）。它是**装饰，不是文档内容**——建议没被接受之前，
// `state.doc` 里一个字符都没有。这条很要紧：
//   · 作者不接受就走人时，磁盘上的正文没被动过；
//   · `onChange` 不会被它触发，所以不会把这一章误标成「未保存」；
//   · ADR 0006 那条「doc 位置 == JS 字符串下标」继续成立，锚定位不受影响。

export const setSuggestion = StateEffect.define<{ text: string; pos: number } | null>();

class GhostWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }
  eq(other: GhostWidget): boolean {
    return other.text === this.text;
  }
  toDOM(): HTMLElement {
    const span = document.createElement("span");
    span.className = "cm-ghost";
    span.textContent = this.text;
    return span;
  }
  // 灰字不参与光标移动：它不是内容，作者不该能把光标挪进去。
  ignoreEvent(): boolean {
    return false;
  }
}

export interface Suggestion {
  text: string;
  pos: number;
}

export const suggestionField = StateField.define<Suggestion | null>({
  create: () => null,
  update(value, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setSuggestion)) return effect.value;
    }
    // **任何文档改动或光标移动都作废它。** 这就是「取消」——作者一敲键，
    // 在飞的那次结果回来时也会被这条清掉（它带着旧的 pos）。
    if (tr.docChanged || tr.selection) return null;
    return value;
  },
  provide: (field) =>
    EditorView.decorations.from(field, (value) =>
      value && value.text
        ? Decoration.set([
            Decoration.widget({ widget: new GhostWidget(value.text), side: 1 }).range(value.pos),
          ])
        : Decoration.none,
    ),
});

/** 接受建议：把灰字变成真正的文档内容。返回 false = 当时没有建议，让按键继续冒泡。 */
export function acceptSuggestion(view: EditorView): boolean {
  const current = view.state.field(suggestionField, false);
  if (!current || !current.text) return false;
  view.dispatch({
    changes: { from: current.pos, insert: current.text },
    selection: { anchor: current.pos + current.text.length },
    effects: setSuggestion.of(null),
    // 这一次 docChanged **是**真的用户编辑（作者按了 Tab），所以不打 External 标记：
    // 它该标脏、该进 onChange。
  });
  return true;
}

/** 丢掉建议。返回 false = 当时没有建议，让 Esc 继续冒泡（别吞掉别人的快捷键）。 */
export function dismissSuggestion(view: EditorView): boolean {
  if (!view.state.field(suggestionField, false)) return false;
  view.dispatch({ effects: setSuggestion.of(null) });
  return true;
}

/** 接受建议的「下一口」（一个词，见 `nextChunkLength`）：那一截从灰字变真文档内容，
 *  剩下的继续挂着，光标停在刚落的字后面。整口吞完时退化成 `acceptSuggestion`
 *  （效果一样：清空建议），不留一个空字符串的建议对象在场上。
 *
 *  绑定在**光标未修饰的** `→`：建议挂着时光标必然停在 `current.pos`（挪一下就被上面
 *  那条 `update()` 清掉了），所以此刻按 `→` 除了「吃一口建议」没有别的合理含义——
 *  原生行为顶多是把它悄悄丢掉再把光标挪进后面的正文，不是作者会依赖的东西。
 *  返回 false = 当时没有建议，方向键正常移动光标。 */
export function acceptSuggestionChunk(view: EditorView): boolean {
  const current = view.state.field(suggestionField, false);
  if (!current || !current.text) return false;
  const len = nextChunkLength(current.text);
  if (len >= current.text.length) return acceptSuggestion(view);
  const chunk = current.text.slice(0, len);
  const rest = current.text.slice(len);
  view.dispatch({
    changes: { from: current.pos, insert: chunk },
    selection: { anchor: current.pos + chunk.length },
    effects: setSuggestion.of({ text: rest, pos: current.pos + chunk.length }),
  });
  return true;
}

/** 装进 CM6 的那一份。**keymap 必须排在 defaultKeymap 之前**，否则 Tab/Esc/→ 先被别人吃掉。 */
export function ghostText(): Extension {
  return [
    suggestionField,
    keymap.of([
      { key: "Tab", run: acceptSuggestion },
      { key: "ArrowRight", run: acceptSuggestionChunk },
      { key: "Escape", run: dismissSuggestion },
    ]),
  ];
}
