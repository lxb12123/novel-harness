import { Prec, StateEffect, StateField, type Extension } from "@codemirror/state";
import { Decoration, EditorView, WidgetType, keymap } from "@codemirror/view";
import { nextChunkLength } from "../continuation";

// 行内建议的「灰字」（ADR 0015）。它是**装饰，不是文档内容**——建议没被接受之前，
// `state.doc` 里一个字符都没有。这条很要紧：
//   · 作者不接受就走人时，磁盘上的正文没被动过；
//   · `onChange` 不会被它触发，所以不会把这一章误标成「未保存」；
//   · ADR 0006 那条「doc 位置 == JS 字符串下标」继续成立，锚定位不受影响。

export const setSuggestion = StateEffect.define<{ text: string; pos: number } | null>();

/** 「取景」预览的切点（在 `text` 里的字符下标，不是文档下标）。只在按住/切到取景模式
 *  时才有意义——单独一个 effect，不并进 `setSuggestion`，因为它的更新频率天差地别：
 *  `setSuggestion` 一次会话也就发生几次，`previewMark` 鼠标一动就要更新一次，混在
 *  一起会让「建议真的换了一条」和「只是鼠标划了一下」在 `suggestionField.update()`
 *  里分不清。`null` = 关掉取景模式（连同已经预览的切点一起丢）。 */
export const setPreviewMark = StateEffect.define<number | null>();

/** 把浏览器返回的「这个坐标点在哪个文本节点第几个字符上」，换算成相对某个基准 DOM
 *  节点的纯文本偏移量——`caretPositionFromPoint`（标准，Firefox/新版 Chrome）和
 *  `caretRangeFromPoint`（老一点、WebKit 系）返回的形状不一样，这里统一成一个数字。
 *  拿不到坐标点对应位置、或者点中的不是 `root` 内部的节点时返回 `null`。 */
function textOffsetFromPoint(root: HTMLElement, x: number, y: number): number | null {
  const doc = root.ownerDocument;
  let node: Node | null = null;
  let offset = 0;
  // 两个 API 都已经在 TS 的 DOM 类型里，不用自己转型；只是**运行时**不是所有浏览器
  // 都两个都有（`caretPositionFromPoint` 是标准新写法，`caretRangeFromPoint` 是老一点、
  // WebKit 系仍在用的写法），所以还是要挨个探测。
  if (typeof doc.caretPositionFromPoint === "function") {
    const pos = doc.caretPositionFromPoint(x, y);
    if (!pos) return null;
    node = pos.offsetNode;
    offset = pos.offset;
  } else if (typeof doc.caretRangeFromPoint === "function") {
    const range = doc.caretRangeFromPoint(x, y);
    if (!range) return null;
    node = range.startContainer;
    offset = range.startOffset;
  } else {
    return null;
  }
  if (!node || !root.contains(node)) return null;
  // 把「某个文本节点内的第几个字符」换算成「相对 root 全部文本的第几个字符」：
  // 按 DOM 顺序把 root 之前的文本节点长度都加上去。
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let total = 0;
  let current = walker.nextNode();
  while (current) {
    if (current === node) return total + Math.min(offset, current.textContent?.length ?? 0);
    total += current.textContent?.length ?? 0;
    current = walker.nextNode();
  }
  return null;
}

class GhostWidget extends WidgetType {
  constructor(
    readonly text: string,
    readonly previewMark: number | null,
  ) {
    super();
  }
  eq(other: GhostWidget): boolean {
    return other.text === this.text && other.previewMark === this.previewMark;
  }
  toDOM(): HTMLElement {
    const span = document.createElement("span");
    span.className = "cm-ghost-wrap";
    const mark =
      this.previewMark != null ? Math.max(0, Math.min(this.previewMark, this.text.length)) : null;
    if (mark != null && mark > 0) {
      const committed = document.createElement("span");
      committed.className = "cm-ghost-preview";
      committed.textContent = this.text.slice(0, mark);
      span.appendChild(committed);
    }
    if (mark == null || mark < this.text.length) {
      const rest = document.createElement("span");
      rest.className = "cm-ghost";
      rest.textContent = mark != null ? this.text.slice(mark) : this.text;
      span.appendChild(rest);
    }
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
  /** 取景模式下鼠标划到的切点（`text` 里的字符下标）。`null` = 没在取景。 */
  previewMark?: number | null;
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
    if (value) {
      for (const effect of tr.effects) {
        if (effect.is(setPreviewMark)) return { ...value, previewMark: effect.value };
      }
    }
    return value;
  },
  provide: (field) =>
    EditorView.decorations.from(field, (value) =>
      value && value.text
        ? Decoration.set([
            Decoration.widget({
              widget: new GhostWidget(value.text, value.previewMark ?? null),
              side: 1,
            }).range(value.pos),
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
 *  那条 `update()` 清掉了），所以此刻按 `→` 除了「吃一口建议」没有别的合理含义。
 *  返回 false = 当时没有建议，方向键正常移动光标。
 *
 *  **`Intl.Segmenter` 抛错时退化成整段接受**，不让异常顺着抛到 CM6 的按键分发之外——
 *  那样 `run` 永远不返回 `true`，`defaultKeymap` 自己的 `→` 绑定会接手把光标挪进
 *  真正文，同步产生一个没带 `setSuggestion` 效果的事务，照样把这条建议清空，
 *  但一个字都没落下（跟下面 `ghostText()` 里防的是同一类坏法，一个是异常路径，
 *  一个是没异常但优先级排错的路径）。 */
export function acceptSuggestionChunk(view: EditorView): boolean {
  const current = view.state.field(suggestionField, false);
  if (!current || !current.text) return false;
  let len: number;
  try {
    len = nextChunkLength(current.text);
  } catch (err) {
    console.error("acceptSuggestionChunk: nextChunkLength 抛错，退化成整段接受", err);
    return acceptSuggestion(view);
  }
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

/** 按住/切到 Ctrl：进「取景模式」——鼠标在灰字上划到哪，哪就是预览切点，不落字、
 *  不碰文档、也不会惊动 `onIdle` 那个重新问模型的计时器（`previewMark` 走的是
 *  独立的 `setPreviewMark`，不带 `changes`/`selection`）。再按一次 Ctrl 退出取景，
 *  预览切点跟着丢——反悔不用付代价。返回 false = 当时没有建议，Ctrl 照常当修饰键用。 */
export function toggleGhostPreview(view: EditorView): boolean {
  const current = view.state.field(suggestionField, false);
  if (!current || !current.text) return false;
  const active = current.previewMark != null;
  view.dispatch({ effects: setPreviewMark.of(active ? null : 0) });
  return false; // Ctrl 单独按下本来就没有原生动作要拦，让它照常冒泡
}

/** 鼠标在灰字上划，取景模式下实时更新预览切点。不是取景模式、或者这次移动没落在
 *  灰字的 DOM 范围内，什么都不做——**不清空已有的切点**：作者划出灰字范围只是
 *  手滑，不代表反悔，反悔的手势是再按一次 Ctrl（`toggleGhostPreview`）或者 Esc。 */
export function updateGhostPreviewFromPoint(view: EditorView, x: number, y: number): boolean {
  const current = view.state.field(suggestionField, false);
  if (!current || !current.text || current.previewMark == null) return false;
  const wrap = view.dom.querySelector<HTMLElement>(".cm-ghost-wrap");
  if (!wrap) return false;
  const offset = textOffsetFromPoint(wrap, x, y);
  if (offset == null || offset === current.previewMark) return false;
  view.dispatch({ effects: setPreviewMark.of(offset) });
  return false;
}

/** Enter 键，只在「取景模式 + 已经划出一个切点」时才生效：切点之前那截落成正文，
 *  之后的继续挂着（跟 → 逐口接受同一套落字逻辑，只是长度来自鼠标而不是分词）。
 *  **其它任何时候都原样返回 false**——Enter 是作者换段落最常用的键，这条判断必须
 *  滴水不漏：没有建议、没有在取景、切点还没划出来（`previewMark` 是 0），都让路，
 *  按下去就是正常换行，不受这个功能影响一丝一毫。 */
export function acceptSuggestionUpToMark(view: EditorView): boolean {
  const current = view.state.field(suggestionField, false);
  if (!current || !current.text || !current.previewMark) return false;
  const mark = Math.max(0, Math.min(current.previewMark, current.text.length));
  if (mark <= 0) return false;
  if (mark >= current.text.length) return acceptSuggestion(view);
  const chunk = current.text.slice(0, mark);
  const rest = current.text.slice(mark);
  view.dispatch({
    changes: { from: current.pos, insert: chunk },
    selection: { anchor: current.pos + chunk.length },
    effects: setSuggestion.of({ text: rest, pos: current.pos + chunk.length }),
  });
  return true;
}

/** 装进 CM6 的那一份。
 *
 *  **`Prec.highest` 不是可有可无的装饰。** CM6 的 `keymap` facet 是「先注册的先试」：
 *  `defaultKeymap`（`CodeEditor.tsx` 里排在这个扩展前面）自己就绑了 `→`（挪光标）、
 *  也绑了 `Enter`（换行）。光标右边只要还有一个真字符可挪、或者随便什么位置按
 *  Enter，`defaultKeymap` 的处理函数就会成功、返回 `true`，CM6 当场停止往下试——
 *  这个扩展里的绑定永远轮不到。**只有光标已经在文档末尾、无字可挪时，`defaultKeymap`
 *  的 `→` 才会自己返回 `false` 让路**——这正是 `tests/CodeEditor.test.tsx` 早期那条
 *  测试意外全绿、却测不出真实 bug 的原因：它把光标放在一个 6 字文档的第 6 位，
 *  凑巧撞上了这个「文档末尾」的特例。`Prec.highest` 把这个扩展的 keymap 提到最高
 *  优先级，不管它在数组里排第几，永远先于 `defaultKeymap` 被尝试——`suggestionField`
 *  不需要这个（它不是 keymap）。Enter 能安全排进来，是因为 `acceptSuggestionUpToMark`
 *  自己对「没有在取景模式」的情况把关极严，不依赖优先级去兜底正常换行。 */
export function ghostText(): Extension {
  return [
    suggestionField,
    Prec.highest(
      keymap.of([
        { key: "Tab", run: acceptSuggestion },
        { key: "ArrowRight", run: acceptSuggestionChunk },
        { key: "Enter", run: acceptSuggestionUpToMark },
        { key: "Escape", run: dismissSuggestion },
      ]),
    ),
    EditorView.domEventHandlers({
      // 纯修饰键（Ctrl 单独按下）不走 `keymap` 的 `key: "Ctrl-Ctrl"` 语法（那是给
      // Ctrl 配合别的键用的），得在这一层直接认 `event.key === "Control"`。
      keydown(event, view) {
        if (event.key !== "Control" || event.repeat) return false;
        return toggleGhostPreview(view);
      },
      mousemove(event, view) {
        return updateGhostPreviewFromPoint(view, event.clientX, event.clientY);
      },
    }),
  ];
}
