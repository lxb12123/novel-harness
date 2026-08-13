import { useEffect, useRef, useState } from "react";
import {
  useChapters,
  useChapterText,
  useContinuation,
  useResolve,
  useSaveChapter,
} from "../api/hooks";
import { useCoords } from "../store";
import { ApiError } from "../api/client";
import { DeclareDrawer } from "./DeclareDrawer";
import { SceneBar } from "./SceneBar";
import { HistoryDrawer } from "./HistoryDrawer";
import { ChapterTitle } from "./ChapterTitle";
import { CodeEditor, type CodeEditorHandle } from "./CodeEditor";
import { SyncButton } from "./SyncButton";
import { locate } from "../anchor";
import { titleOf, withTitle } from "../chapterTitle";
import { cleanSuggestion, shouldSuggest } from "../continuation";
import { diskChange } from "../editorDoc";

// 中栏正文编辑器（CodeMirror 6，§2.4——不是 TipTap）。
// CM6 只是磁盘 chapters/NNNN.md 的便利视图：读 = GET text，存 = PUT → sync，DB 永不是
// 正文真相源（ADR 0007）。CM6 停在平铺文本心智，doc 位置 == JS 字符串下标，和 anchor.locate()
// 直接对齐，不需要 pos↔锚 映射层（那是 ProseMirror 才会买来的 offset 地狱，ADR 0006）。
export function CenterEditor() {
  const { projectId, chapter, setSelection, selection, focusNode, highlight, setHighlight } =
    useCoords();
  const [open, setOpen] = useState(false); // 这一章是否已打开进编辑器
  const chapters = useChapters(projectId);
  const { data } = useChapterText(projectId, chapter, open);
  const save = useSaveChapter(projectId ?? "", chapter);
  const resolve = useResolve(projectId ?? "");
  const continuation = useContinuation(projectId ?? "", chapter);
  // 每次请求发出前 +1。回来时对不上 = 作者在这期间又敲了字，这一条作废。
  // **这就是「取消」**：续写不需要增量失效，只需要过期的那次别落地（ADR 0015）。
  const askRef = useRef(0);

  const [doc, setDoc] = useState("");
  const [dirty, setDirty] = useState(false);
  /** `doc` 里那份是**第几章**的。换章那一瞬间它还是上一章的字（新的还在路上），
   *  而顶上那行标题念的就是它的第一行——不认这一下，换章时会闪一下上一章的标题，
   *  更糟的是那时双击改标题会把新标题写进**上一章**的正文里。 */
  const [docFor, setDocFor] = useState<number | null>(null);
  /** 编辑器手上这份的**出处**（上一次采纳的磁盘正文）。判「别处改过没有」拿它比，
   *  拿 `doc` 比会把作者自己敲的每一个字都算成别人改的（`editorDoc.ts` 第三条）。 */
  const loadedRef = useRef<string | null>(null);
  // 下面那个 effect 只在 `data` 变时跑，闭包里的 `dirty` 会是旧的 —— 用 ref 兜住最新值。
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  /** 磁盘上这一章被别处改过，而作者手上有没保存的字。 */
  const [diskAhead, setDiskAhead] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [history, setHistory] = useState(false);
  const [locateMiss, setLocateMiss] = useState(false);
  const editorRef = useRef<CodeEditorHandle>(null);

  // R4 冲突回跳（§2.6 方向二）：点 issue 设 highlight → 按 quote 重寻 → 命令 CM6 选中并滚进视野。
  useEffect(() => {
    if (!highlight) return;
    const hit = locate(doc, highlight);
    setHighlight(null);
    if (!hit) {
      setLocateMiss(true);
      return;
    }
    setLocateMiss(false);
    editorRef.current?.select(hit.start, hit.end); // CM6 位置 == 字符串下标，无需换算
  }, [highlight, doc, setHighlight]);

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
    loadedRef.current = null;
    setDocFor(null);
    setDiskAhead(false);
  }, [chapter, projectId]);

  // **重取到什么就装进去**这条老规矩，在写作助手会直接往这一章写字之后不再安全
  // （ADR 0021；判断本身在 `editorDoc.ts`，那儿写着两种错的代价为什么不对称）。
  useEffect(() => {
    if (!data) return;
    // 这一章的正文到手了 —— 不管下面三条走哪一条，编辑器手上那份都是**这一章**的
    //（"warn" 那条是作者自己没保存的那半段，也是这一章的）。
    setDocFor(data.number);
    const what = diskChange(data.markdown, loadedRef.current, dirtyRef.current);
    if (what === "same") return;
    if (what === "warn") return setDiskAhead(true);
    loadedRef.current = data.markdown;
    setDoc(data.markdown);
    setDiskAhead(false);
  }, [data]);

  const saveErr = save.error instanceof ApiError ? save.error : null;

  // 一章都没有的书（刚建的空书、或稿子被从文件夹里删光了）：不进编辑器。
  // 不拦的话这儿会渲染一个**空白但完全正常**的编辑器——作者会以为自己打开了第 1 章，
  // 打上字、按保存，然后撞上一个 404。空状态说清楚比一张漂亮的空表安全。
  if (chapters.data?.length === 0) {
    return (
      <section className="pane editor">
        <div className="edbar">
          <span className="who">还没有章节</span>
        </div>
        <div className="empty" style={{ padding: 16 }}>
          这本书还是空的。用左边的「＋ 新书 / 导入」导入一份 TXT，
          导入完这里就会打开最后一章。
        </div>
      </section>
    );
  }

  return (
    <section className="pane editor">
      <div className="edbar">
        {/* 章标题 = 挑章 + 改标题，都在这一行上（原先它是顶栏右上角那个 <select>）。
            **改标题改的就是正文第一行**（`chapterTitle.ts` 写着为什么只能是它），
            所以这儿走的是和作者自己在正文里改第一行**完全同一条**路：标脏 → 按保存。
            没有 rename 端点，也没有第二份标题。 */}
        <ChapterTitle
          line={docFor === chapter ? titleOf(doc) : null}
          onRename={(title) => {
            setDoc(withTitle(doc, title));
            setDirty(true);
          }}
        />
        <span className="spacer" />
        {/* 「读回改动」（`SyncButton.tsx` 写着为什么是按钮不是 file-watch）。
            **它在这一行上，因为这一行讲的就是「这份稿子」**：正文他看得见（这块屏幕
            直接读磁盘），可「记录这句」搜的是库里的快照——那半条回路此前在浏览器里
            根本没有入口。同一颗按钮还挂在声明抽屉「找不到」那一档上，
            那是他撞见这件事的地方。 */}
        {projectId && <SyncButton pid={projectId} />}
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
        <button onClick={() => setHistory(true)} title="这一章改过什么">
          历史
        </button>
        <button
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate(doc, { onSuccess: () => setDirty(false) })}
        >
          保存
        </button>
      </div>

      <SceneBar />

      {/* 别处改过、而作者手上有没保存的字。**不替他挑**：盖掉他没保存的那半段是找不回来的，
          盖掉磁盘上那一版是找得回来的（历史里那一条），所以这儿只说一句、不动他的字。 */}
      {diskAhead && (
        <div className="selbar" style={{ borderTop: 0, color: "var(--warn)" }}>
          这一章在别处变过了（写作助手起草会直接写进这一章，另一个窗口保存也会）。
          你手上这份还没保存——现在按保存会盖过它，被盖的那一版在「历史」里找得回来。
        </div>
      )}

      {locateMiss && (
        <div className="selbar" style={{ borderTop: 0, color: "var(--warn)" }}>
          定位不到那句话——正文可能改过了（存盘后重跑检查），或它锚在别的章。
        </div>
      )}

      <div className="cm-wrap">
        <CodeEditor
          ref={editorRef}
          value={doc}
          onChange={(v) => {
            setDoc(v);
            setDirty(true);
          }}
          onSelectionText={setSelection}
          onIdle={({ before, pos, hasSelection }) => {
            if (!projectId) return;
            if (!shouldSuggest({ before, hasSelection, hasSuggestion: false, loading: !data })) {
              return;
            }
            const ask = ++askRef.current;
            continuation.mutate(before, {
              onSuccess: (r) => {
                // 作者在等待期间又动过 → 这条是对着旧文本算的，丢掉。
                if (ask !== askRef.current) return;
                const text = cleanSuggestion(r.text);
                if (text) editorRef.current?.showSuggestion(text, pos);
              },
            });
          }}
        />
      </div>

      <div className="selbar">
        <span className="q">
          {selection
            ? "选中：" + selection
            : "在正文里选中一句话，可以记录人物知道什么、身处何处，或查看相关内容"}
        </span>
        <button disabled={!selection || !projectId || resolve.isPending} onClick={lookup}>
          查看相关内容
        </button>
        <button disabled={!selection || !projectId} onClick={() => setDrawer(true)}>
          记录这句
        </button>
      </div>

      {candidates && (
        <div className="selbar" style={{ borderTop: 0, flexWrap: "wrap" }}>
          <span className="q" style={{ flex: "0 0 auto", color: "var(--warn)" }}>
            这个称呼对应多个条目，请选择：
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
          花名册中没有找到「{resolve.data.surface}」。
        </div>
      )}

      {drawer && projectId && (
        <DeclareDrawer pid={projectId} quote={selection} onClose={() => setDrawer(false)} />
      )}
      {history && projectId && (
        <HistoryDrawer
          pid={projectId}
          chapter={chapter}
          dirty={dirty}
          // 还原写的是磁盘，编辑器里那份要跟着回到「和磁盘一致」——不清脏态，
          // 顶栏会一直挂着「未保存」，而作者其实什么都没改。
          onRestored={() => setDirty(false)}
          onClose={() => setHistory(false)}
        />
      )}
    </section>
  );
}
