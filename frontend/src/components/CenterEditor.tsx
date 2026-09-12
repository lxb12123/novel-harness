import { useEffect, useRef, useState } from "react";
import {
  useAiSettings,
  useChapters,
  useChapterText,
  useContinuation,
  useSaveChapter,
} from "../api/hooks";
import { useCoords } from "../store";
import { ApiError } from "../api/client";
import { saidToTheAuthor } from "../correctionError";
import { useLanguage } from "../language";
import { HistoryDrawer } from "./HistoryDrawer";
import { ChapterTitle } from "./ChapterTitle";
import { DropletIcon, PocketWatchIcon, SnowflakeIcon } from "./icons";
import { CodeEditor, type CodeEditorHandle } from "./CodeEditor";
import { locate } from "../anchor";
import { splitHeading, titleOf, withTitle } from "../chapterTitle";
import { cleanSuggestion, shouldSuggest } from "../continuation";
import { diskChange } from "../editorDoc";

// 中栏正文编辑器（CodeMirror 6，§2.4——不是 TipTap）。
// CM6 只是磁盘 chapters/NNNN.md 的便利视图：读 = GET text，存 = PUT → sync，DB 永不是
// 正文真相源（ADR 0007）。CM6 停在平铺文本心智，doc 位置 == JS 字符串下标，和 anchor.locate()
// 直接对齐，不需要 pos↔锚 映射层（那是 ProseMirror 才会买来的 offset 地狱，ADR 0006）——
// 唯一的例外是章标那一行不进 CM6（下面 `head`/`body`），对齐前要先扣掉一个常量长度。
export function CenterEditor() {
  const { projectId, chapter, highlight, setHighlight, chatOpen } = useCoords();
  const language = useLanguage((s) => s.language);
  const [open, setOpen] = useState(false); // 这一章是否已打开进编辑器
  const chapters = useChapters(projectId);
  const { data } = useChapterText(projectId, chapter, open);
  const save = useSaveChapter(projectId ?? "", chapter);
  const continuation = useContinuation(projectId ?? "", chapter);
  // 续写能带多少上文**是后端按模型窗口算的**，搭在设置那条返回上过来
  // （`continuation_tail_limit`）。这儿一个上限的字面量都没有，也不许有：
  // 前端写死一个数会把后端整套伸缩设计架空，而症状是「模型忽然变笨」，没有一处会红。
  const settings = useAiSettings();
  // 每次请求发出前 +1。回来时对不上 = 作者在这期间又敲了字，这一条作废。
  // **这就是「取消」**：续写不需要增量失效，只需要过期的那次别落地（ADR 0015）。
  const askRef = useRef(0);
  // 写作助手开着（novel-agent 模式）时续写**默认不跑**——作者 2026-09-10 定的，起因是
  // 助手开着、正文里还在往下冒灰字。「模式」在代码里只是一块布局（`App.tsx` 按 `chatOpen`
  // 挂不挂 `ChatPanel`），这个编辑器本来不知道面板开没开，所以这儿得自己认它。
  // 放行的开关在设置「系统功能」那一栏（`continuation_in_agent_mode`）。
  const continuationMuted = chatOpen && !settings.data?.continuation_in_agent_mode;

  const [doc, setDoc] = useState("");
  const [dirty, setDirty] = useState(false);
  /** `doc` 里那份是**第几章**的。换章那一瞬间它还是上一章的字（新的还在路上），
   *  而顶上那行标题念的就是它的第一行——不认这一下，换章时会闪一下上一章的标题，
   *  更糟的是那时双击改标题会把新标题写进**上一章**的正文里。 */
  const [docFor, setDocFor] = useState<number | null>(null);
  /** 编辑器手上这份的**出处**（上一次采纳的磁盘正文）。判「别处改过没有」拿它比，
   *  拿 `doc` 比会把作者自己敲的每一个字都算成别人改的（`editorDoc.ts` 第三条）。 */
  const loadedRef = useRef<string | null>(null);
  /** `loadedRef` 那份的 sha256，随它同步更新。保存时当 `expected_text_sha256` 送回去
   *  （ADR 0021 的乐观闸）——**不能现算**：后端比对的是它自己发出的那份哈希，前端另算
   *  一份等于自己发明一个「服务端 hash」。 */
  const loadedShaRef = useRef<string | null>(null);
  // 下面那个 effect 只在 `data` 变时跑，闭包里的 `dirty` 会是旧的 —— 用 ref 兜住最新值。
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  /** 磁盘上这一章被别处改过，而作者手上有没保存的字。 */
  const [diskAhead, setDiskAhead] = useState(false);
  const [history, setHistory] = useState(false);
  const [locateMiss, setLocateMiss] = useState(false);
  const editorRef = useRef<CodeEditorHandle>(null);

  // 章标那一行不进编辑器（`chapterTitle.ts::splitHeading`）：CM6 手上只有 `body`，
  // `doc` 仍是整份正文（存盘、`titleOf`/`withTitle` 都还认它）。`head` 因此是一段
  // 坐标偏移——下面 `locate()` 在整份 doc 上算出来的下标，要减掉它才对得上 CM6
  // 那份短了一截的文档（`chapterTitle.test.ts` 那条「差一个 head.length」钉的就是这个）。
  const { head, body } = splitHeading(doc);

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
    // hit 是整份 doc 的下标，CM6 手上只有 body——减掉 head.length 才是它认的坐标系。
    editorRef.current?.select(hit.start - head.length, hit.end - head.length);
  }, [highlight, doc, head, setHighlight]);

  // 切进 novel-agent 模式那一下：屏幕上挂着的灰字丢掉，在飞的那次作废。
  // 不做的话有两条漏：① 灰字要等作者再碰一下编辑器才消失（`ghostText` 的 field 只在
  // 编辑/移动光标时清）；② 停手时发出去的那次几秒后回来，`onSuccess` 照样把它挂上——
  // 对作者看来就是「说了模式二没有续写，它还是冒出来了」。
  useEffect(() => {
    if (!continuationMuted) return;
    askRef.current += 1;
    editorRef.current?.clearSuggestion();
  }, [continuationMuted]);

  // 换章 = 重新打开：让 useChapterText 重取，并清脏态。
  useEffect(() => {
    setOpen(true);
    setDirty(false);
    loadedRef.current = null;
    loadedShaRef.current = null;
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
    loadedShaRef.current = data.text_sha256;
    setDoc(data.markdown);
    setDiskAhead(false);
  }, [data]);

  const saveErr = save.error instanceof ApiError ? save.error : null;
  // 「保存」按钮角上的水滴／雪花：徽标图标、按钮该不该用窄边距、悬浮说明文字，
  // 三处都从同一份判定派生，别各自重新判一遍 `dirty`/`save.isSuccess`。
  const saveBadge = dirty ? "unsaved" : save.isSuccess ? "synced" : null;

  // 一章都没有的书（刚建的空书、或稿子被从文件夹里删光了）：不进编辑器。
  // 不拦的话这儿会渲染一个**空白但完全正常**的编辑器——作者会以为自己打开了第 1 章，
  // 打上字、按保存，然后撞上一个 404。空状态说清楚比一张漂亮的空表安全。
  if (chapters.data?.length === 0) {
    return (
      <section className="pane editor">
        <div className="edbar">
          <span className="who">{language === "zh" ? "还没有章节" : "No chapters yet"}</span>
        </div>
        <div className="empty" style={{ padding: 16 }}>
          {language === "zh" ? (
            <>
              本书尚无内容。使用左侧「＋ 新书 / 导入」导入 TXT 文件，导入后在此打开最后一章。
            </>
          ) : (
            <>
              This book has no content yet. Use "+ New Book / Import" on the left to import a TXT
              file; the last chapter opens here once imported.
            </>
          )}
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
        {/* 「读回改动」那颗按钮 2026-08-15 删了。它要求作者先理解一件他不该知道的事
            ——**屏幕上的正文来自磁盘，而库里那份快照来自这颗按钮**。它今天的活由两处
            自动接管：保存时把这一章读回库并触发刷新（`api/app.py::_trigger_refresh`），以及回到这个
            标签页时把整本书对一遍（`reconcile.ts`，只 stat，722 章 ~4ms）。 */}
        {/* 「未保存」/「已保存并同步」这两态 2026-08-30 从文字改成了保存按钮角上的徽标
            （水滴＝手上这份还没落盘，雪花＝落盘了、跟远端也对齐了——「定形」那个隐喻）。
            这儿只留**说不清楚该配哪个图标的两态**：拒绝理由是变长的自由文本，
            保存中是一个瞬间的过程，都不该被压成一个图形。 */}
        {(saveErr || save.isPending) && (
          <span className={"status" + (saveErr ? " err" : "")}>
            {saveErr
              ? (language === "zh" ? "保存被拒：" : "Save was refused: ") +
                (saidToTheAuthor(saveErr) ?? saveErr.message)
              : language === "zh" ? "保存中…" : "Saving…"}
          </span>
        )}
        {/* 两颗都**只剩图标**（作者 2026-09-09）。图标自己 `aria-hidden`，所以
            名字一律由 `aria-label` 给——少了它就是一颗读屏念不出名字的按钮，而这件事
            在屏幕上完全看不出来（`icons.tsx` 开头第 3 条）。
            `aria-label` 用的还是原来那两个字，可及名字一个字节没变：
            `getByRole("button", { name: "保存" })` 那批断言不用跟着改。
            说明走 `data-tip` 不走原生 `title`：后者要等约一秒，作者反馈过「以为没有」。 */}
        <button
          className="icon-btn"
          onClick={() => setHistory(true)}
          aria-label={language === "zh" ? "历史" : "History"}
          data-tip={language === "zh" ? "查看历史版本" : "View version history"}
        >
          <PocketWatchIcon />
        </button>
        <button
          className="save-btn icon-btn"
          disabled={!dirty || save.isPending}
          aria-label={language === "zh" ? "保存" : "Save"}
          // 三态各有一句话。**第三态（还没改过、也没存过）以前没有**——那时按钮上写着
          // 「保存」两个字，图标只是补充；现在字没了，不给它一句话就成了一颗哑按钮。
          data-tip={
            saveBadge === "unsaved"
              ? (language === "zh" ? "未保存" : "Unsaved")
              : saveBadge === "synced"
              ? (language === "zh" ? "已保存" : "Saved")
              : (language === "zh" ? "保存" : "Save")
          }
          onClick={() =>
            save.mutate(
              { markdown: doc, expected_text_sha256: loadedShaRef.current ?? "" },
              { onSuccess: () => setDirty(false) },
            )
          }
        >
          {/* 雪花只在「存过且已同步」那一态出现；**其余两态都是水滴**——它是这颗按钮
              本来的样子，不是一句「你有东西没存」。会不会误读由**按不按得动**分开：
              真有东西没存时按钮是活的、水滴是深色，没有时它禁用、水滴是 `--dim`。 */}
          {saveBadge === "synced" ? <SnowflakeIcon /> : <DropletIcon />}
        </button>
      </div>

      {/* 别处改过、而作者手上有没保存的字。**不替他挑**：盖掉他没保存的那半段是找不回来的，
          盖掉磁盘上那一版是找得回来的（历史里那一条），所以这儿只说一句、不动他的字。 */}
      {diskAhead && (
        <div className="selbar" style={{ borderTop: 0, color: "var(--warn)" }}>
          {language === "zh" ? (
            <>
              本章已在别处修改（写作助手起草或另一窗口保存均会写入本章）。当前编辑内容尚未保存；
              此时保存会覆盖该版本，被覆盖的版本可在「历史」中找回。
            </>
          ) : (
            <>
              This chapter was changed elsewhere (drafts from the writing assistant and saves from
              another window both write into it). The current edits are unsaved; saving now
              overwrites that version, which remains available in "History".
            </>
          )}
        </div>
      )}

      {locateMiss && (
        <div className="selbar" style={{ borderTop: 0, color: "var(--warn)" }}>
          {language === "zh" ? (
            <>无法定位该句：正文可能已修改（保存后将重新检查），或该句位于其他章节。</>
          ) : (
            <>
              That sentence could not be located: the text may have changed (checks run again
              after saving), or it belongs to a different chapter.
            </>
          )}
        </div>
      )}

      <div className="cm-wrap">
        <CodeEditor
          ref={editorRef}
          value={body}
          tailLimit={settings.data?.continuation_tail_limit ?? null}
          onChange={(v) => {
            setDoc(head + v);
            setDirty(true);
          }}
          onIdle={({ before, after, pos, hasSelection }) => {
            if (!projectId) return;
            // **不因为「还有一口没吃完」就拦下重新问**（这一档试过，作者要的不是这个）：
            // 逐口接受时，`before` 每按一次 → 就更精确一分，作者要的是「一直跟着最新的
            // 上文给建议」，不是「锁死在第一次算出来的那条，吃完/丢掉才肯换」。
            // 真正管「问得太勤」的只有 `IDLE_MS`。
            if (
              !shouldSuggest({
                before,
                hasSelection,
                hasSuggestion: false,
                loading: !data,
                assistantOpen: chatOpen,
                continuationInAgentMode: settings.data?.continuation_in_agent_mode,
              })
            ) {
              return;
            }
            const ask = ++askRef.current;
            // `after` 是光标后面那截已经写好的正文（改旧章时才有东西）。
            // **给不给模型看由后端定**：那要知道这一章后面还有没有已经写完的章，
            // 前端不知道全书写到第几章，也不该猜（同上文那个额度）。
            continuation.mutate(
              { before, after },
              {
                onSuccess: (r) => {
                  // 作者在等待期间又动过 → 这条是对着旧文本算的，丢掉。
                  if (ask !== askRef.current) return;
                  const text = cleanSuggestion(r.text);
                  if (text) editorRef.current?.showSuggestion(text, pos);
                },
              },
            );
          }}
        />
      </div>

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
