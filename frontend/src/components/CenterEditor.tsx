import { useEffect, useRef, useState } from "react";
import { useChapterText, useResolve, useSaveChapter } from "../api/hooks";
import { useCoords } from "../store";
import { ApiError } from "../api/client";
import { DeclareDrawer } from "./DeclareDrawer";
import { SceneBar } from "./SceneBar";

// 中栏正文编辑器。
// **骨架阶段用 textarea**：CM6（§2.4）是文档定的 P1 升级，它换来的是富文本 + 段落级
// 高亮，但写闭环不需要它——declare 只要一段选中的引语当 quote，后端 locate 自己算章号。
// 正文仍是磁盘的（ADR 0007）：读 = GET text，存 = PUT → sync，DB 永不是正文真相源。
export function CenterEditor() {
  const { projectId, chapter, setSelection, selection, focusNode } = useCoords();
  const [open, setOpen] = useState(false); // 这一章是否已打开进编辑器
  const { data } = useChapterText(projectId, chapter, open);
  const save = useSaveChapter(projectId ?? "", chapter);
  const resolve = useResolve(projectId ?? "");

  const [doc, setDoc] = useState("");
  const [dirty, setDirty] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  // 方向一（§2.6）：选区 → resolve → 唯一直接 focus 图谱；歧义弹候选让作者挑，不猜。
  function lookup() {
    resolve.mutate(selection, {
      onSuccess: (r) => {
        if (r.unique_id) focusNode(r.unique_id);
      },
    });
  }
  const candidates = resolve.data && !resolve.data.unique_id ? resolve.data.hits : null;

  // 换章 = 重新打开：让 useChapterText 重取，并清脏态。
  useEffect(() => {
    setOpen(true);
    setDirty(false);
  }, [chapter, projectId]);

  useEffect(() => {
    if (data) setDoc(data.markdown);
  }, [data]);

  function captureSelection() {
    const el = ref.current;
    if (!el) return;
    const sel = el.value.slice(el.selectionStart, el.selectionEnd).trim();
    setSelection(sel);
  }

  const saveErr = save.error instanceof ApiError ? save.error : null;

  return (
    <section className="pane editor">
      <div className="edbar">
        <span className="who">第 {chapter} 章</span>
        <span className="spacer" />
        <span className={"status" + (saveErr ? " err" : save.isSuccess && !dirty ? " ok" : "")}>
          {saveErr
            ? "保存被拒：" + saveErr.message
            : save.isPending
              ? "保存中…"
              : dirty
                ? "未保存"
                : save.isSuccess
                  ? "已保存并同步"
                  : ""}
        </span>
        <button
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate(doc, { onSuccess: () => setDirty(false) })}
        >
          保存
        </button>
      </div>

      <SceneBar />

      <textarea
        ref={ref}
        value={doc}
        spellCheck={false}
        placeholder="从左边点一章打开正文…"
        onChange={(e) => {
          setDoc(e.target.value);
          setDirty(true);
        }}
        onSelect={captureSelection}
      />

      <div className="selbar">
        <span className="q">
          {selection ? "选中：" + selection : "在正文里选一句话 → 声明「谁知道 / 在哪」，或查它是谁"}
        </span>
        <button disabled={!selection || !projectId || resolve.isPending} onClick={lookup}>
          查图谱
        </button>
        <button disabled={!selection || !projectId} onClick={() => setDrawer(true)}>
          用这句声明
        </button>
      </div>

      {candidates && (
        <div className="selbar" style={{ borderTop: 0, flexWrap: "wrap" }}>
          <span className="q" style={{ flex: "0 0 auto", color: "var(--warn)" }}>
            「{resolve.data!.surface}」指向多个，挑一个：
          </span>
          {candidates.map((h) => (
            <button key={h.node.id} onClick={() => focusNode(h.node.id)}>
              {h.node.name}（{h.node.label}）
            </button>
          ))}
        </div>
      )}
      {resolve.data && resolve.data.hits.length === 0 && (
        <div className="selbar" style={{ borderTop: 0, color: "var(--dim)" }}>
          「{resolve.data.surface}」不是花名册里的任何一个称呼。
        </div>
      )}

      {drawer && projectId && (
        <DeclareDrawer pid={projectId} quote={selection} onClose={() => setDrawer(false)} />
      )}
    </section>
  );
}
