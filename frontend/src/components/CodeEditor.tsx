import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { EditorView, keymap, drawSelection, placeholder as cmPlaceholder } from "@codemirror/view";
import { history, defaultKeymap, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { Annotation } from "@codemirror/state";

// 标记「外部灌入」的事务（换章时替换整篇 doc）。用它把外部替换和用户输入分开——
// 否则换章那次 docChanged 会 onChange 回去，把新打开的章误标成「未保存」。
const External = Annotation.define<boolean>();

// CodeMirror 6 编辑器（§2.4）——**不是 TipTap**。
// 守 ADR 0006 的方式：CM6 停在纯文本/markdown 心智，doc 位置就是 JS 字符串的 code unit
// 偏移，和 anchor.locate() 返回的下标**直接对齐**，不需要 pos↔(para,quote,k) 映射层
// （那正是 ProseMirror 会买来的 offset 地狱）。段落 = 空行分隔的文本块，锚靠重寻 quote 定位。
//
// 它只是磁盘 chapters/NNNN.md 的便利视图：打开=读盘（value 换），保存=写回同一个 md。

export interface CodeEditorHandle {
  /** 选中 [from, to) 并滚进视野（R4 冲突回跳、证据回跳用）。位置是字符串 code unit 下标。 */
  select: (from: number, to: number) => void;
}

const theme = EditorView.theme({
  "&": { backgroundColor: "var(--bg)", color: "var(--ink)", height: "100%" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    overflow: "auto",
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontSize: "14px",
    lineHeight: "1.85",
  },
  ".cm-content": { padding: "14px 16px", caretColor: "var(--ink)" },
  ".cm-cursor": { borderLeftColor: "var(--ink)" },
  // 程序化选区（R4 高亮）必须看得见——drawSelection 画的是这个类，两种主题都给足对比。
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--sel)",
  },
  ".cm-placeholder": { color: "var(--dim)" },
});

export const CodeEditor = forwardRef<
  CodeEditorHandle,
  { value: string; onChange: (v: string) => void; onSelectionText: (t: string) => void }
>(function CodeEditor({ value, onChange, onSelectionText }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  // 回调放 ref，避免把它们进 mount 的 deps（否则每次 render 重建整个编辑器）。
  const cb = useRef({ onChange, onSelectionText });
  cb.current = { onChange, onSelectionText };

  useEffect(() => {
    if (!host.current) return;
    const view = new EditorView({
      doc: value,
      parent: host.current,
      extensions: [
        history(),
        drawSelection(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        markdown(),
        EditorView.lineWrapping,
        cmPlaceholder("从左边点一章打开正文…"),
        theme,
        EditorView.updateListener.of((u) => {
          // 外部替换（换章）不回调 onChange——那不是用户编辑，不该标脏。
          const external = u.transactions.some((tr) => tr.annotation(External));
          if (u.docChanged && !external) cb.current.onChange(u.state.doc.toString());
          if (u.selectionSet) {
            const { from, to } = u.state.selection.main;
            cb.current.onSelectionText(u.state.sliceDoc(from, to).trim());
          }
        }),
      ],
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // 只挂载一次；外部 value 变化由下面那个 effect 增量灌进去。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 换章 = 外部 value 变了 → 替换 doc。与当前一致时不 dispatch（避免用户输入触发的回灌抖动）。
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const cur = view.state.doc.toString();
    if (value !== cur) {
      view.dispatch({
        changes: { from: 0, to: cur.length, insert: value },
        annotations: External.of(true),
      });
    }
  }, [value]);

  useImperativeHandle(
    ref,
    () => ({
      select(from, to) {
        const view = viewRef.current;
        if (!view) return;
        const len = view.state.doc.length;
        view.dispatch({
          selection: { anchor: Math.min(from, len), head: Math.min(to, len) },
          scrollIntoView: true,
        });
        view.focus();
      },
    }),
    [],
  );

  return <div className="cm-host" ref={host} />;
});
