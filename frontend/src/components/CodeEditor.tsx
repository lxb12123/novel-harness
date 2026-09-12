import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { EditorView, keymap, drawSelection, placeholder as cmPlaceholder } from "@codemirror/view";
import { history, defaultKeymap, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { Annotation, Compartment } from "@codemirror/state";
import { ghostText, setSuggestion, suggestionField } from "./ghostText";
import { IDLE_MS, tailAfter, tailBefore } from "../continuation";

// 标记「外部灌入」的事务（换章时替换整篇 doc）。用它把外部替换和用户输入分开——
// 否则换章那次 docChanged 会 onChange 回去，把新打开的章误标成「未保存」。
const External = Annotation.define<boolean>();

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
     *  开头时不拽：判据是滚动条在不在底上，同 `ChatPanel::useFollowBottom`。 */
    follow?: boolean;
  }
>(function CodeEditor({ value, onChange, onIdle, tailLimit, editable = true, follow = false }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const editableConf = useRef(new Compartment());
  const followRef = useRef(follow);
  followRef.current = follow;
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
        history(),
        drawSelection(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        markdown(),
        // 排在这里（defaultKeymap 之后）纯粹是顺序好读；真正保证 Tab/Esc/→ 先于
        // defaultKeymap 被尝试的是 `ghostText()` 内部的 `Prec.highest`，不是数组位置——
        // CM6 的 keymap facet 同优先级下是「先注册的先试」，光靠挪位置排不对。
        ghostText(),
        EditorView.lineWrapping,
        cmPlaceholder("在左侧选择章节以打开正文"),
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
    return () => {
      if (idleTimer.current) clearTimeout(idleTimer.current);
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
    // 跟着底走：作者没翻上去时，新长出来的字始终在视野里。离底不到 8px 算「贴着底」
    // （亚像素取整的余量，同 `ChatPanel::FOLLOW_SLACK`）；翻上去了就不拽。
    const scroller = view.scrollDOM;
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 8;
    view.dispatch({
      changes: appended
        ? { from: cur.length, insert: value.slice(cur.length) }
        : { from: 0, to: cur.length, insert: value },
      annotations: External.of(true),
      effects:
        followRef.current && appended && atBottom
          ? EditorView.scrollIntoView(value.length)
          : undefined,
    });
  }, [value]);

  // 锁 / 解锁键盘（正在写的那一稿进来 / 写完了）。Compartment 重配置不重建编辑器。
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: editableConf.current.reconfigure(EditorView.editable.of(editable)) });
  }, [editable]);

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
