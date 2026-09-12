import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  keymap,
  drawSelection,
  placeholder as cmPlaceholder,
  WidgetType,
} from "@codemirror/view";
import { history, defaultKeymap, historyKeymap, isolateHistory } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import {
  Annotation,
  Compartment,
  type Range,
  StateEffect,
  StateField,
  type Text,
} from "@codemirror/state";
import { ghostText, setSuggestion, suggestionField } from "./ghostText";
import { IDLE_MS, tailAfter, tailBefore } from "../continuation";
import { editMarks } from "../editMarks";
import { type Language, useLanguage } from "../language";

// 标记「外部灌入」的事务（换章时替换整篇 doc）。用它把外部替换和用户输入分开——
// 否则换章那次 docChanged 会 onChange 回去，把新打开的章误标成「未保存」。
const External = Annotation.define<boolean>();

// ── 没保存的改动的痕迹（`editMarks.ts`）────────────────────────────────────
//
// 编辑器里这份正文和上一次保存的那一版（`baseline`）之间的差：减去的段是一块红的、不可编辑
// 的字块，插回它原来的位置；增加的行整行绿。**每一次 doc 变都重算**（作者敲一个键、流进来
// 一片字），而不是把旧的区间 `map` 过去——痕迹说的是「现在和保存版差在哪」，那只能算，
// 不能挪：作者把一个绿行里的字删光，挪过去的区间还在，算出来的就没了。算的代价见 `diff.ts`
// 开头（两头相同的行先剥掉，一章几百行每键只剩几行的表）。
//
// 画在 StateField 里而不是 ViewPlugin 里：**块状 widget 只能由 StateField 提供**（CM6 的
// 规矩），而红块正是块状的——它占一整行，光标跳不进去。
//
// 大块红折成一行「已删除 N 段」（`editMarks.ts` 的 `fold`），点开才摊开。摊没摊开记在这个
// field 里、按那一块的内容认（`foldKey`）：作者点开一块之后接着敲字，痕迹整个重算，
// 记在 DOM 上的话那一块就又折回去了；换了保存版（刚保存 / 换章）全部归零。
const setBaseline = StateEffect.define<{
  text: string | null;
  streaming: boolean;
  language: Language;
}>();
const toggleFold = StateEffect.define<string>();
const ADDED_LINE = Decoration.line({ class: "diff-add" });

const foldKey = (lines: readonly string[]) => lines.join("\n");

/** 保存版里有、现在没有的那几段。 */
class RemovedLines extends WidgetType {
  constructor(
    readonly lines: readonly string[],
    /** 大到该折的一块：先画一行「已删除 N 段」，`expanded` 才把段摊在它底下。 */
    readonly foldable: boolean,
    readonly expanded: boolean,
    readonly language: Language,
  ) {
    super();
  }
  eq(other: RemovedLines): boolean {
    return (
      other.foldable === this.foldable &&
      other.expanded === this.expanded &&
      other.language === this.language &&
      other.lines.length === this.lines.length &&
      other.lines.every((l, i) => l === this.lines[i])
    );
  }
  toDOM(view: EditorView): HTMLElement {
    const box = document.createElement("div");
    box.className = "diff-removed";
    if (this.foldable) {
      const n = this.lines.length;
      const head = document.createElement("button");
      head.type = "button";
      head.className = "diff-fold";
      head.setAttribute("aria-expanded", String(this.expanded));
      head.textContent =
        this.language === "zh" ? `已删除 ${n} 段` : `${n} paragraph${n === 1 ? "" : "s"} removed`;
      const key = foldKey(this.lines);
      head.addEventListener("click", () => view.dispatch({ effects: toggleFold.of(key) }));
      box.appendChild(head);
      if (!this.expanded) return box;
    }
    for (const line of this.lines) {
      const el = document.createElement("div");
      el.className = "diff-del";
      el.textContent = line;
      box.appendChild(el);
    }
    return box;
  }
}

function diffDecorations(
  doc: Text,
  baseline: string,
  streaming: boolean,
  expanded: ReadonlySet<string>,
  language: Language,
): DecorationSet {
  const marks = editMarks(baseline, doc.toString(), streaming);
  if (marks.length === 0) return Decoration.none;
  const ranges: Range<Decoration>[] = [];
  for (const mark of marks) {
    if (mark.kind === "added") {
      ranges.push(ADDED_LINE.range(doc.line(mark.line + 1).from));
      continue;
    }
    const widget = new RemovedLines(mark.lines, mark.fold, expanded.has(foldKey(mark.lines)), language);
    if (mark.before < doc.lines) {
      ranges.push(
        Decoration.widget({ widget, block: true, side: -1 }).range(doc.line(mark.before + 1).from),
      );
    } else {
      ranges.push(Decoration.widget({ widget, block: true, side: 1 }).range(doc.length));
    }
  }
  return Decoration.set(ranges, true);
}

interface DiffState {
  baseline: string | null;
  streaming: boolean;
  /** 界面语言（折起来的那一行字用）。**不在这儿写死一个默认值**：它和保存版一起由
   *  `setBaseline` 送进来，保存版还没到手时痕迹本来就一处都不画。 */
  language: Language | null;
  /** 作者点开了的那几块红（按内容认）。 */
  expanded: ReadonlySet<string>;
  deco: DecorationSet;
}
const diffField = StateField.define<DiffState>({
  create: () => ({
    baseline: null,
    streaming: false,
    language: null,
    expanded: new Set(),
    deco: Decoration.none,
  }),
  update(value, tr) {
    let { baseline, streaming, language, expanded } = value;
    let changed = false;
    for (const effect of tr.effects) {
      if (effect.is(setBaseline)) {
        if (effect.value.text !== baseline) expanded = new Set();
        baseline = effect.value.text;
        streaming = effect.value.streaming;
        language = effect.value.language;
        changed = true;
      } else if (effect.is(toggleFold)) {
        const next = new Set(expanded);
        if (next.has(effect.value)) next.delete(effect.value);
        else next.add(effect.value);
        expanded = next;
        changed = true;
      }
    }
    if (!changed && !tr.docChanged) return value;
    const deco =
      baseline === null || language === null
        ? Decoration.none
        : diffDecorations(tr.state.doc, baseline, streaming, expanded, language);
    return { baseline, streaming, language, expanded, deco };
  },
  provide: (field) => EditorView.decorations.from(field, (v) => v.deco),
});

/** 一章都没打开时编辑器里那一句。 */
const placeholderText = (language: Language) =>
  language === "zh" ? "在左侧选择章节以打开正文" : "Select a chapter on the left to open its text";

/** 离底不到这么多像素算「贴着底」：一行正文的高度（亚像素取整之外还要容一行）。 */
const PIN_SLACK = 40;
/** 作者的一下滚动手势（滚轮 / 触摸 / 拖滚动条 / 键盘）之后多久以内的 scroll 事件算他的。
 *  触控板的惯性滚动整个过程都在发 wheel 事件，所以这个数不用大。 */
const GESTURE_MS = 250;

// CodeMirror 6 编辑器（§2.4）——**不是 TipTap**。
// 守 ADR 0006 的方式：CM6 停在纯文本/markdown 心智，doc 位置就是 JS 字符串的 code unit
// 偏移，和 anchor.locate() 返回的下标**直接对齐**，不需要 pos↔(para,quote,k) 映射层
// （那正是 ProseMirror 会买来的 offset 地狱）。段落 = 空行分隔的文本块，锚靠重寻 quote 定位。
//
// 它只是磁盘 chapters/NNNN.md 的便利视图：打开=读盘（value 换），保存=写回同一个 md。
// `value` 不是整份正文——章标那一行被 `CenterEditor`（`chapterTitle.ts::splitHeading`）
// 截掉了，这儿的下标因此是「扣掉 head 长度」之后那份坐标系，不是 doc 原始下标。

export interface CodeEditorHandle {
  /** 选中 [from, to) 并滚进视野（R4 冲突回跳、证据回跳用）。位置是字符串 code unit 下标。 */
  select: (from: number, to: number) => void;
  /** 滚到最底、重新贴上（作者点「滑到最下方」那颗按钮）。 */
  scrollToEnd: () => void;
  /** 在 `pos` 处挂一条灰字建议（ADR 0015）。**不写进 doc**——作者按 Tab 才落字。 */
  showSuggestion: (text: string, pos: number) => void;
  /** 丢掉当前建议。作者一敲键 CM6 自己也会丢，这个给「请求失败/换章」用。 */
  clearSuggestion: () => void;
}

const theme = EditorView.theme({
  "&": { backgroundColor: "var(--bg)", color: "var(--ink)", height: "100%" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    overflow: "auto",
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontSize: "14px",
    lineHeight: "1.85",
    // 同左栏书架 `.shelf-scroll`（styles.css）：宽度常驻、只切换颜色，
    // 平时透明、悬浮才现身——避免宽度跟着 hover 抖动。
    scrollbarWidth: "thin",
    scrollbarColor: "transparent transparent",
  },
  ".cm-scroller:hover": { scrollbarColor: "var(--line) transparent" },
  ".cm-scroller::-webkit-scrollbar": { width: "6px" },
  ".cm-scroller::-webkit-scrollbar-track": { background: "transparent" },
  ".cm-scroller::-webkit-scrollbar-thumb": { background: "transparent", borderRadius: "3px" },
  ".cm-scroller:hover::-webkit-scrollbar-thumb": { background: "var(--line)" },
  // 左右内边距**不是一个定值，是「把正文挤成一栏」的那道留白**：窗口越宽，它越大，
  // 正文的行宽被 `--text-column` 钉住不动（`styles.css` 里那一条，同一个数还管着
  // 顶栏的标题和图标，所以三者永远对齐）。窄的时候 `max()` 落回 16px，跟以前一样。
  // **用内边距而不是 `max-width`**：`.cm-content` 保持满宽，作者点在留白里也落得到光标；
  // 收窄成一栏之后再让他「点不中的地方」变多，那是拿一个毛病换另一个。
  ".cm-content": {
    padding: "14px max(16px, calc((100% - var(--text-column, 720px)) / 2))",
    caretColor: "var(--ink)",
  },
  ".cm-cursor": { borderLeftColor: "var(--ink)" },
  // 程序化选区（R4 高亮）必须看得见——drawSelection 画的是这个类，两种主题都给足对比。
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--sel)",
  },
  ".cm-placeholder": { color: "var(--dim)" },
});

export const CodeEditor = forwardRef<
  CodeEditorHandle,
  {
    value: string;
    onChange: (v: string) => void;
    /** 停手 `IDLE_MS` 之后触发一次，带上光标**前后**的正文。**取消由调用方负责**：
     *  作者一敲键这个计时器就重置，在飞的那次请求该被丢弃。
     *
     *  `after` 是光标后面那截（改旧章时它是已经写好的正文）。**给不给模型看由后端定**：
     *  它只在「这一章不是全书最后一章」时才渲染成【下文】——那个判断要知道全书写到
     *  第几章，前端不知道，也不该猜。 */
    onIdle?: (ctx: {
      before: string;
      after: string;
      pos: number;
      hasSelection: boolean;
    }) => void;
    /** 送出去的上文最多几个 code point。**后端算的**（`GET /api/settings` 的
     *  `continuation_tail_limit`），这一层只负责按它切——理由见 `continuation.ts`。
     *
     *  `null` = 那个数还没到手（设置还在路上，或后端连模型都认不出）。
     *  **这时不问**：随手猜一个数正是这次删掉的那个 bug，而「不确定就闭嘴」
     *  是这个仓库的默认动作。 */
    tailLimit: number | null;
    /** 写作助手正往这一章里写（`liveDraft.ts`）时锁住键盘：这几十秒里作者敲的字会和
     *  流进来的字混在一起，而且落盘那一刻会被磁盘上那一版盖掉。默认可编辑。 */
    editable?: boolean;
    /** 外部灌进来的字**长在末尾**时跟着滚到底（正在写的那一稿）。作者自己翻上去看
     *  开头时不拽。**判「他翻上去了」只认他自己的滚动手势**（滚轮 / 触摸 / 拖滚动条 /
     *  键盘）之后的 scroll 事件；编辑器自己滚（跟底那一下、CM6 量完行高再对一次）也会发
     *  scroll，那时离底几十像素是正文刚长出来一截还没跟上，不是他翻了——按位置判会在
     *  正文长到满一屏之后每隔一会儿就把跟底掐断一次（作者 2026-09-12 撞到的就是这个）。
     *  滚回底了（不管谁滚的）就又贴上。翻上去了 / 又滚回底了，`onPinnedChange` 说一声——
     *  外面据此画「滑到最下方」那颗按钮。 */
    follow?: boolean;
    onPinnedChange?: (pinned: boolean) => void;
    /** 上一次保存的那一版正文（同 `value` 的坐标系：不含章标那一行）。给了它，编辑器里
     *  和它不一样的地方就画出痕迹（`editMarks.ts`：减去红、增加绿），按保存换成新的一版
     *  痕迹就没了。`null` = 还不知道保存版是什么（这一章还没读回来），一处都不画。 */
    baseline?: string | null;
    /** 正文正在流进来（最后一行还在打、后面的段还没到）：痕迹按半份正文的规矩画。 */
    streaming?: boolean;
  }
>(function CodeEditor(
  {
    value,
    onChange,
    onIdle,
    tailLimit,
    editable = true,
    follow = false,
    onPinnedChange,
    baseline = null,
    streaming = false,
  },
  ref,
) {
  const host = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const editableConf = useRef(new Compartment());
  const placeholderConf = useRef(new Compartment());
  const language = useLanguage((s) => s.language);
  const followRef = useRef(follow);
  followRef.current = follow;
  const pinnedRef = useRef(true);
  const pinnedCb = useRef(onPinnedChange);
  pinnedCb.current = onPinnedChange;
  const setPinned = (on: boolean) => {
    if (pinnedRef.current === on) return;
    pinnedRef.current = on;
    pinnedCb.current?.(on);
  };
  // 回调放 ref，避免把它们进 mount 的 deps（否则每次 render 重建整个编辑器）。
  // 上限也放这个 ref：编辑器只挂载一次（下面那个 `[]`），作者在设置页换了模型之后
  // 新的数得进得来，而不是等他把整个工作台关掉重开。
  const cb = useRef({ onChange, onIdle, tailLimit });
  cb.current = { onChange, onIdle, tailLimit };
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const view = new EditorView({
      doc: value,
      parent: host.current,
      extensions: [
        editableConf.current.of(EditorView.editable.of(editable)),
        diffField,
        history(),
        drawSelection(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        markdown(),
        // 排在这里（defaultKeymap 之后）纯粹是顺序好读；真正保证 Tab/Esc/→ 先于
        // defaultKeymap 被尝试的是 `ghostText()` 内部的 `Prec.highest`，不是数组位置——
        // CM6 的 keymap facet 同优先级下是「先注册的先试」，光靠挪位置排不对。
        ghostText(),
        EditorView.lineWrapping,
        placeholderConf.current.of(cmPlaceholder(placeholderText(language))),
        theme,
        EditorView.updateListener.of((u) => {
          // 外部替换（换章）不回调 onChange——那不是用户编辑，不该标脏。
          const external = u.transactions.some((tr) => tr.annotation(External));
          if (u.docChanged && !external) cb.current.onChange(u.state.doc.toString());
          // 停手计时：任何编辑或移动光标都重来一次。**这就是「取消」**——
          // 续写不需要增量失效，只需要过期的那次别落地（ghostText 的 field 会丢掉它）。
          // **外部灌入不算停手**：换章、写作助手往里流字，都不是作者在写——按它们计时
          // 会在稿子正长着的时候去要一条续写建议。
          if (!external && (u.docChanged || u.selectionSet)) {
            if (idleTimer.current) clearTimeout(idleTimer.current);
            const fire = cb.current.onIdle;
            if (fire) {
              const state = u.state;
              idleTimer.current = setTimeout(() => {
                // 上限在**开火那一刻**才读：作者停手的这 400 毫秒里设置可能刚回来。
                const limit = cb.current.tailLimit;
                if (limit === null) return;
                const { from, to } = state.selection.main;
                const doc = state.doc.toString();
                fire({
                  before: tailBefore(doc, from, limit),
                  // 光标后面那截同章正文。**同一个额度**，两刀都朝着光标切。
                  after: tailAfter(doc, from, limit),
                  pos: from,
                  hasSelection: from !== to,
                });
              }, IDLE_MS);
            }
          }
        }),
      ],
    });
    viewRef.current = view;
    // 贴没贴着底。到了底（不管谁滚的）就贴上；离开底只认作者自己的手势之后那一下——
    // 见 `follow` 那条说明。拖滚动条是一段时间（pointerdown 到 pointerup），不是一下。
    const sc = view.scrollDOM;
    let gestureAt = 0;
    let dragging = false;
    const gesture = () => {
      gestureAt = Date.now();
    };
    const dragStart = () => {
      dragging = true;
      gesture();
    };
    const dragEnd = () => {
      dragging = false;
      gesture();
    };
    const onScroll = () => {
      const atBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < PIN_SLACK;
      if (atBottom) setPinned(true);
      else if (dragging || Date.now() - gestureAt < GESTURE_MS) setPinned(false);
    };
    sc.addEventListener("scroll", onScroll);
    sc.addEventListener("wheel", gesture, { passive: true });
    sc.addEventListener("touchmove", gesture, { passive: true });
    sc.addEventListener("pointerdown", dragStart);
    window.addEventListener("pointerup", dragEnd);
    view.dom.addEventListener("keydown", gesture);
    return () => {
      if (idleTimer.current) clearTimeout(idleTimer.current);
      sc.removeEventListener("scroll", onScroll);
      sc.removeEventListener("wheel", gesture);
      sc.removeEventListener("touchmove", gesture);
      sc.removeEventListener("pointerdown", dragStart);
      window.removeEventListener("pointerup", dragEnd);
      view.dom.removeEventListener("keydown", gesture);
      view.destroy();
      viewRef.current = null;
    };
    // 只挂载一次；外部 value 变化由下面那个 effect 增量灌进去。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 换章 = 外部 value 变了 → 替换 doc。与当前一致时不 dispatch（避免用户输入触发的回灌抖动）。
  // **只多了一截尾巴时只插尾巴**：正在写的那一稿一片一片到，整篇重换一次是把几千字
  // 删了再写回去——光标、选区、滚动位置全丢，每一片都闪一下。
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const cur = view.state.doc.toString();
    if (value === cur) return;
    const appended = value.startsWith(cur);
    // 跟着底走：作者没翻上去时（`pinnedRef`），新长出来的字始终在视野里；翻上去了就不拽，
    // 外面画一颗「滑到最下方」让他随时回来。
    // **整篇换掉那一下在撤销历史里自成一步**（`isolateHistory`）：写作助手的稿子紧跟着作者
    // 刚敲的字进来时，CM6 会按「挨着、时间近」把两笔并成一步，一个 ⌘Z 就把他的字也退掉了。
    // 隔开之后 ⌘Z 先退稿子，再退才是他的字。
    view.dispatch({
      changes: appended
        ? { from: cur.length, insert: value.slice(cur.length) }
        : { from: 0, to: cur.length, insert: value },
      annotations: appended ? External.of(true) : [External.of(true), isolateHistory.of("before")],
      effects:
        followRef.current && appended && pinnedRef.current
          ? EditorView.scrollIntoView(value.length, { y: "end" })
          : undefined,
    });
  }, [value]);

  // 开始跟着一条流的时候从「贴着底」起步：第一片字到手时正文很短、没有滚动条，
  // 那一刻本来就在底上。
  useEffect(() => {
    if (follow) setPinned(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [follow]);

  // 锁 / 解锁键盘（正在写的那一稿进来 / 写完了）。Compartment 重配置不重建编辑器。
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: editableConf.current.reconfigure(EditorView.editable.of(editable)) });
  }, [editable]);

  // 保存版换了（读回来了 / 刚保存 / 换章）或者流开始、收场：痕迹对着新的保存版重算。
  // **排在 value 那个 effect 后面**：换章那一下 doc 和保存版一起换，先换 doc 再换保存版，
  // 中间那一帧不会拿新保存版对着旧正文画出一屏红绿。界面语言也从这儿进去（折起来的那一行字）。
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: setBaseline.of({ text: baseline, streaming, language }) });
  }, [baseline, streaming, language]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: placeholderConf.current.reconfigure(cmPlaceholder(placeholderText(language))),
    });
  }, [language]);

  useImperativeHandle(
    ref,
    () => ({
      showSuggestion(text, pos) {
        const view = viewRef.current;
        if (!view) return;
        // 位置越界 = 这条建议是对着旧文本算的，丢掉。作者删字比模型返回快是常态。
        if (pos > view.state.doc.length) return;
        view.dispatch({ effects: setSuggestion.of({ text, pos }) });
      },
      clearSuggestion() {
        const view = viewRef.current;
        if (!view || !view.state.field(suggestionField, false)) return;
        view.dispatch({ effects: setSuggestion.of(null) });
      },
      scrollToEnd() {
        const view = viewRef.current;
        if (!view) return;
        setPinned(true);
        view.dispatch({ effects: EditorView.scrollIntoView(view.state.doc.length, { y: "end" }) });
        const sc = view.scrollDOM;
        sc.scrollTop = sc.scrollHeight;
      },
      select(from, to) {
        const view = viewRef.current;
        if (!view) return;
        const len = view.state.doc.length;
        // 下限兜底 0：CenterEditor 现在喂进来的是「整份 doc 下标 - 头部长度」，
        // 锚在被藏起来的章标那一行时会算出负数（CM6 拒收，选区下标不许 < 0）。
        const clamp = (n: number) => Math.max(0, Math.min(n, len));
        view.dispatch({
          selection: { anchor: clamp(from), head: clamp(to) },
          scrollIntoView: true,
        });
        view.focus();
      },
    }),
    [],
  );

  return <div className="cm-host" ref={host} />;
});
