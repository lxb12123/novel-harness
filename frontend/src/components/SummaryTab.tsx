import { useEffect, useRef, useState } from "react";
import {
  useAiSettings,
  useBookSummaryStatus,
  useChapterSummary,
  useEditSummary,
  useGenerateSummary,
  useNodeSummaryMentions,
  useRetractSummary,
  useSummaryMentions,
} from "../api/hooks";
import type {
  BookChapterStatusRow,
  BookSummaryStatus,
  NodeSummaryMentions,
  SummaryMention,
} from "../api/types";
import { nodeLabelText } from "../backendMessages";
import { refusalText } from "../chat";
import { useLanguage, type Language } from "../language";
import { useCoords } from "../store";
import { ModelGuide } from "./ModelGuide";

// 右栏「章节总结」这一格。**跟着左栏选中的那一章走**（同「检查」那几格）。
//
// ── 它为什么必须存在 ──────────────────────────────────────────────────────
//
// 滚动总结**真的在花作者的钱、真的在影响每一稿**：起草这一章时，前面几章的总结一行行
// 拼进 prompt（后端的记忆前言），写作助手也能主动去查它。而在这一格之前，作者在整个
// 工作台里**看不见它、改不了它、删不掉它**——表在、路由在、hook 在，界面上零调用方。
// 这个仓库管这种病叫「最后一厘米没接」，这是第六次。
//
// ── 这块屏幕上不许出现的东西 ──────────────────────────────────────────────
//
// （2026-08-26 之前这里还有一条「『机器压缩的背景』那句免责，贴到作者自己写的那一段
// 上」——「作者写的/机器写的」这条区分整个去掉了，那条规矩没有对象了。见 ADR 0017 补记。）
//
// 1. **「还没生成」贴到他刚撤回的那一章上。** 两种「没有」在起草那边完全同义（都不进
//    prompt），可下一步动作正好相反：一种要去生成，一种是他半秒钟前自己做的。
//    催他去补一件刚做完的事，是这块面板最容易说出口的那句假话。
// 1b. **说这一格有一颗它没有的按钮。** 这条是实测出来的：手动生成入口
//    （`POST …/summary`）2026-08-25 删了，而这块屏幕上三句话（占位符 + 两种空态 +
//    撤回确认）继续指着「点『生成』/『重新生成』」，**骗了作者十天**——直到他
//    2026-09-05 问「撤回了想重新生成怎么办」。当时的答案是「不能」。
//    那颗按钮和那条路由同日加回来了（维护者裁定），所以这三句话今天是真的。
//    **下一个动这颗按钮的人：三句话跟着一起改。** 删按钮不改文案 = 又骗十天。
// 2. **第二份措辞。** 拒绝的话由后端写（`refusalText`），这儿只在后端一个字都没说的
//    时候才补一句，而那一句不解释「为什么」（§10 约束 8：不知道就说不知道）。
//
// ── 2026-08-13 下半：每一段总结变成一个**能反查的记忆点**（T6）────────────────
//
// 作者的原话：「每一个总结就相当于一本书的一个记忆点。我想迅速找到需要的内容或相关章节的
// 总结，然后引用、对比、调研，再顺下去看全文。**我不想用 RAG。**」
//
// 所以这一格下面多了两层：**这一段提到了什么**（一排芯片）→ 点一个 →
// **还有哪几章的总结提到它**（按章号排，带原文，点章号跳过去）。
//
// 这块屏幕上同样不许出现的东西：
//
// 4. **任何「相关度 / 匹配度 / 相似」的说法。** 后端一个语义判断都没做（判据只有
//    「这个称呼出现了没有」）。写一个百分比等于向作者承诺引擎读懂了剧情——它在数字符串。
// 5. **任何会花钱的按钮。** 反查是一次 SQL，所以这一层可以随便点，而「随便点」这件事
//    本身要靠「这儿一颗付费按钮都没有」保证，不是靠一句提示。

/** 后端一句话都没写时才轮到的那几句。 */
const saveFailed = (language: Language): string =>
  language === "zh"
    ? "总结未能保存，未返回原因。请稍后重试。"
    : "The summary could not be saved and no reason was returned. Try again shortly.";
const retractFailed = (language: Language): string =>
  language === "zh"
    ? "总结未能撤回，未返回原因。请稍后重试。"
    : "The summary could not be retracted and no reason was returned. Try again shortly.";
const generateFailed = (language: Language): string =>
  language === "zh"
    ? "总结未能生成，未返回原因。请稍后重试。"
    : "The summary could not be generated and no reason was returned. Try again shortly.";
const readFailed = (language: Language): string =>
  language === "zh"
    ? "总结读取失败；上方为空不代表没有总结。请刷新后查看。"
    : "The summary could not be loaded; an empty box above does not mean there is none. Refresh to check.";

const mentionsFailed = (language: Language): string =>
  language === "zh"
    ? "总结提及的人物未能查出；下方为空不代表没有提及。请刷新后查看。"
    : "The mentions in this summary could not be checked; an empty box below does not mean there are none. Refresh to check.";
const trailFailed = (language: Language): string =>
  language === "zh"
    ? "其他章节的提及情况未能查出。请稍后重试。"
    : "Mentions in other chapters could not be checked. Try again shortly.";

export function SummaryTab() {
  const { projectId, chapter, setChapter, setPage } = useCoords();
  const language = useLanguage((s) => s.language);
  const status = useChapterSummary(projectId, chapter);
  const book = useBookSummaryStatus(projectId);
  const edit = useEditSummary(projectId ?? "");
  const retract = useRetractSummary(projectId ?? "");
  const generate = useGenerateSummary(projectId ?? "");
  const ai = useAiSettings(); // 「尚无总结」那一句要知道模型服务配好了没有

  // 作者正在改的那一段。**`null` = 他没在改**，屏幕跟着服务端那份走。
  // 分成两个状态是因为重取随时会落地（后台整理刚补完一章总结就会），
  // 而**没保存过的字哪儿都找不回来**——跟着重取一起刷掉就是静默吃掉他打的字。
  //
  // **三样都按章号记**（2026-09-13）：这一格不再随章号重挂（原来那句「由 `key` 兜着」
  // 说的 key 早就不在了），换章时只认「是这一章的」那一份——否则在第 5 章打了一半的字
  // 会顶着第 6 章的标题出现。换章不再重挂，是为了让上面那张全书的格子**留在原地**：
  // 作者在格子里点一章，整格卸掉重来，滚动条就回到最顶上（作者 2026-09-13 指出来的）。
  const [typedFor, setTypedFor] = useState<{ chapter: number; text: string } | null>(null);
  const typed = typedFor?.chapter === chapter ? typedFor.text : null;
  const setTyped = (text: string | null) =>
    setTypedFor(text === null ? null : { chapter, text });
  const [confirmingFor, setConfirmingFor] = useState<number | null>(null);
  const confirming = confirmingFor === chapter;
  const setConfirming = (on: boolean) => setConfirmingFor(on ? chapter : null);
  // 他点开的那个记忆点。
  const [openedFor, setOpenedFor] = useState<{ chapter: number; id: string } | null>(null);
  const opened = openedFor?.chapter === chapter ? openedFor.id : null;
  const setOpened = (id: string | null) => setOpenedFor(id === null ? null : { chapter, id });
  /** 从上面那张格子点过来的那一下：这一章的总结读到了就把它滚进视野（`block: "nearest"`
   *  ——已经看得见就不动，在底下就只滚到刚好露出来）。左栏换章不滚：那时他看的是正文。
   *
   *  ── 滚的是**整段**（标题 + 框 + 按钮），不是标题那一行（2026-09-14）──────────
   *  作者：「从绿色的点到另外一个绿色的，就会从当前总结的往上边跳，我还需要往下滑才能看到」。
   *  两件事叠在一起：① 读取中那一档整段塌成一行，栏的 `scrollTop` 被夹着往上跳一截
   *  （内容矮了，滚动条只能跟着缩）；② 读到之后 Chrome 有 scroll anchoring 会把位置还回来，
   *  **WKWebView（桌面壳）没有**——于是标题正好停在栏底那一线，「滚到标题露出来」判它已经
   *  露出来了，一动不动，框在底下。现在 ① 由读取中那一档保持同样的形状挡住（见下），
   *  ② 由 ref 挂在整段上挡住：`nearest` 对一段「底边在栏底之下」的东西是把底边对齐栏底，
   *  标题、框、按钮一起露出来，上面的格子留得最多。 */
  const cameFromGrid = useRef(false);
  const sectionRef = useRef<HTMLDivElement>(null);

  const mentions = useSummaryMentions(projectId, chapter);
  const trail = useNodeSummaryMentions(projectId, opened);

  const data = status.data;
  const stored = data?.summary ?? "";
  const shown = typed ?? stored;
  const dirty = typed !== null && typed !== stored;
  const busy = edit.isPending || retract.isPending || generate.isPending;

  const failed = status.isError
    ? (refusalText(status.error, readFailed(language)) ?? readFailed(language))
    : null;
  const refused =
    refusalText(edit.error, saveFailed(language)) ??
    refusalText(retract.error, retractFailed(language)) ??
    refusalText(generate.error, generateFailed(language));

  /** 回到中栏的正文。中栏这会儿可能摊着别的东西（活动记录整块换掉它），
   *  那时正文压根没挂上，光滚是滚不到的。 */
  function jumpToText() {
    setPage("workbench");
    requestAnimationFrame(() => {
      document.querySelector(".pane.editor")?.scrollIntoView?.({ block: "nearest" });
    });
  }

  function done() {
    setTyped(null);
    setConfirming(false);
  }

  function goChapter(n: number) {
    cameFromGrid.current = true;
    setChapter(n);
    setPage("workbench");
  }

  useEffect(() => {
    if (!cameFromGrid.current || !status.data) return;
    cameFromGrid.current = false;
    sectionRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [status.data]);

  // 全书总结状态一直挂在这一格最上面：**它跟着书走，不跟着当前章走**——
  // 作者在这一格里既看「当前章有没有」也看「全书缺哪些/哪章不对齐/哪章异常」。
  const bookStatusEl = (
    <BookStatus
      data={book.data}
      failed={book.isError}
      current={chapter}
      onGo={goChapter}
    />
  );

  // **全书那张格子在四档里都常驻**（读取中 / 读取失败 / 没正文 / 正常）。以前读取中那一档
  // 整格换成一行「正在读取总结…」：作者在格子里点一章，格子卸掉、这一栏塌成一行，滚动条
  // 回到顶上，总结读回来之后他得从第 1 章一路划到底才看得见（作者 2026-09-13）。
  if (failed) {
    return (
      <div className="chsum">
        {bookStatusEl}
        <div className="err-box">{failed}</div>
      </div>
    );
  }
  if (!data) {
    // **读取中那一档和读到之后同一个形状**：标题（已经知道是第几章）+ 同样高的一个框。
    // 塌成一行的话，栏的内容忽然矮了一截，滚动条被夹着往上跳（作者 2026-09-14：
    // 「从当前总结的往上边跳」）；Chrome 靠 scroll anchoring 事后还回来，桌面壳的
    // WKWebView 不会。框里那句话就是读取中那一档该说的全部。
    return (
      <div className="chsum">
        {bookStatusEl}
        <div className="chsum-section">
          <p className="chsum-scope">
            {language === "zh" ? `第 ${chapter} 章 章节总结` : `Chapter ${chapter} summary`}
          </p>
          <textarea
            className="chsum-text"
            aria-label={language === "zh" ? `第 ${chapter} 章的总结` : `Summary for chapter ${chapter}`}
            placeholder={language === "zh" ? "正在读取总结…" : "Loading the summary…"}
            rows={6}
            value=""
            disabled
            readOnly
          />
        </div>
      </div>
    );
  }

  // 这一章还没写：**没得总结**，也就没有一颗会花钱的按钮该在这儿亮着。
  if (!data.has_text) {
    return (
      <div className="chsum">
        {bookStatusEl}
        <div className="chsum-section" ref={sectionRef}>
          <p className="empty">
            {language === "zh" ? (
              <>
                第 {chapter} 章尚无正文，无法生成总结。写入正文并保存后，可在此生成。
              </>
            ) : (
              <>
                Chapter {chapter} has no text yet, so there is nothing to summarize. Write and
                save the chapter, then generate a summary here.
              </>
            )}
          </p>
          <div className="chsum-actions">
            <button onClick={jumpToText}>{language === "zh" ? "跳到原文" : "Jump to the text"}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="chsum">
      {bookStatusEl}
      {/* 标题、框、说明、按钮是**一段**（`sectionRef` 挂在这儿）：从格子点过来时整段滚进视野。 */}
      <div className="chsum-section" ref={sectionRef}>
      <p className="chsum-scope">
        {language === "zh" ? `第 ${chapter} 章 章节总结` : `Chapter ${chapter} summary`}
      </p>

      <textarea
        className="chsum-text"
        aria-label={language === "zh" ? `第 ${chapter} 章的总结` : `Summary for chapter ${chapter}`}
        placeholder={
          language === "zh"
            ? "本章内容概要。保存正文后自动生成，也可手动填写。"
            : "What happens in this chapter. Generated automatically after the text is saved; can also be written by hand."
        }
        rows={6}
        value={shown}
        disabled={busy}
        onChange={(e) => setTyped(e.target.value)}
      />

      {/* 模型服务没配好：「保存后自动生成 / 点「生成」」两条路都走不通，先说怎么连模型。 */}
      {data.summary === null && ai.data && !ai.data.model_configured && (
        <div className="empty chsum-why">
          <ModelGuide what="summary" />
        </div>
      )}
      {data.summary === null && !(ai.data && !ai.data.model_configured) && (
        // **零带着理由。** 两种零的下一步动作相反，所以它们说两句不一样的话。
        <p className="empty chsum-why">
          {language === "zh"
            ? data.retracted
              ? "本章总结已撤回：起草本章时不再使用，系统也不会自动补写。如需新的总结，点击下方「重新生成」（调用一次模型），或手动填写（不产生费用）。"
              : "本章尚无总结。保存正文后将自动生成（调用一次模型）；也可点击下方「生成」，或手动填写（不产生费用）。"
            : data.retracted
              ? "This chapter’s summary was retracted: it is no longer used when drafting this chapter, and the system will not rewrite it on its own. For a new one, click “Regenerate” below (one model call), or write one by hand (no cost)."
              : "This chapter has no summary yet. One is generated automatically after the text is saved (one model call); click “Generate” below, or write one by hand (no cost)."}
        </p>
      )}

      {confirming && (
        <div className="warn">
          {language === "zh" ? (
            <>
              撤回后，起草本章时不再使用此总结；正文不受影响。系统不会自动补写，可随时点击
              「重新生成」（调用一次模型），或手动填写（不产生费用）。
            </>
          ) : (
            <>
              Once retracted, this summary is no longer used when drafting this chapter; the text
              itself is unaffected. The system will not rewrite it on its own; “Regenerate” (one
              model call) and writing one by hand (no cost) remain available.
            </>
          )}
        </div>
      )}

      <div className="chsum-actions">
        {dirty && (
          <>
            <button
              disabled={busy || !shown.trim()}
              onClick={() =>
                edit.mutate({ chapter, summary: shown }, { onSuccess: done })
              }
            >
              {language === "zh" ? "保存这一段" : "Save this paragraph"}
            </button>
            <button disabled={busy} onClick={() => setTyped(null)}>
              {language === "zh" ? "放弃修改" : "Discard changes"}
            </button>
          </>
        )}
        {!dirty && data.summary === null && (
          // **撤回之后唯一能拿回机器总结的那颗按钮。** 系统自己永远不补撤回过的章
          // （`chapter_refresh._head_missing`），所以这里不摆它，那一章就死在那儿了
          // ——2026-08-25 到 2026-09-05 之间正是这个样子。
          // 只在「现在没有一份在用的总结」时出现：有总结时按下去，后端的幂等判重会
          // 让它一声不响地什么都不做，而一颗按下去没反应的按钮比没有按钮更糟。
          <button disabled={busy} onClick={() => generate.mutate(chapter)}>
            {generate.isPending
              ? language === "zh"
                ? "正在生成…"
                : "Generating…"
              : data.retracted
                ? language === "zh"
                  ? "重新生成"
                  : "Regenerate"
                : language === "zh"
                  ? "生成"
                  : "Generate"}
          </button>
        )}
        {!dirty && data.summary !== null && !confirming && (
          <button className="danger" disabled={busy} onClick={() => setConfirming(true)}>
            {language === "zh" ? "撤回" : "Retract"}
          </button>
        )}
        {confirming && (
          <>
            <button
              className="danger"
              disabled={busy}
              onClick={() => retract.mutate(chapter, { onSuccess: done })}
            >
              {language === "zh" ? "确认撤回" : "Retract"}
            </button>
            <button disabled={busy} onClick={() => setConfirming(false)}>
              {language === "zh" ? "取消" : "Cancel"}
            </button>
          </>
        )}
        <button onClick={jumpToText}>{language === "zh" ? "跳到原文" : "Jump to the text"}</button>
      </div>

      {refused && <div className="err-box">{refused}</div>}
      </div>

      {/* **这一章根本没有总结时，下面这一整层不出现。** 后端那时回的是空表，
          而「这一段里没出现角色册上的任何人」在没有「这一段」的时候是一句假话——
          它会让作者以为他写的那一章里一个人都没有。 */}
      {data.summary !== null && (
        <Memories
          hits={mentions.data?.mentions}
          failed={mentions.isError}
          opened={opened}
          onOpen={(id) => setOpened(id === opened ? null : id)}
        />
      )}

      {opened && data.summary !== null && (
        <Trail
          here={chapter}
          data={trail.data}
          loading={trail.isLoading}
          failed={trail.isError}
          onGo={(n) => {
            setChapter(n);
            setPage("workbench");
          }}
        />
      )}
    </div>
  );
}

// ── 全书总结状态（2026-08-18 文档 §6 / Step 4）──────────────────────────────
//
// 一口气看清「这本书现在长什么样」：哪章配对、哪章缺、哪章不对齐（正文动过、
// 总结该覆写）、哪章生成时出了岔子。**全部确定性查库**（指纹配对 + attempt 终态），
// 后端一个语义判断都没有，所以这里也不许出现「相关度 / 相似」那类说法。
//
// ● 不许出现的东西：
// 1. **把 `empty` 和 `missing` 并成一类。** 下一步动作相反：一种去写正文，
//    一种去生成总结（要花预算）。
// 2. **指着异常章给一颗会花钱的按钮。** 异常由自治轮自己重试（§6 不阻塞别的章），
//    界面只把它标出来，不催作者手动付一次费。
// 3. **手动算一遍「近几章优先」。** 权重是后端确定性公式给的（§4），前端照抄一个
//    就是第二个会漂的常量。

const bookStatusFailed = (language: Language): string =>
  language === "zh"
    ? "全书总结状态读取失败；上方为空不代表没有总结。请刷新后查看。"
    : "The whole-book summary status could not be loaded; an empty box above does not mean there are no summaries. Refresh to check.";

/** 芯片上那句短状态。`paired` 绿 / `missing` 橙 / `stale` 棕 / `empty` 灰。 */
const STATUS_LABEL: Record<BookChapterStatusRow["state"], { zh: string; en: string }> = {
  paired: { zh: "有", en: "Paired" },
  missing: { zh: "缺", en: "Missing" },
  stale: { zh: "不对齐", en: "Stale" },
  empty: { zh: "空", en: "Empty" },
};

function BookStatus(props: {
  data: BookSummaryStatus | undefined;
  failed: boolean;
  current: number;
  onGo: (chapter: number) => void;
}) {
  const language = useLanguage((s) => s.language);
  if (props.failed) return <div className="err-box">{bookStatusFailed(language)}</div>;
  if (!props.data) return null;

  const data = props.data;
  const rows = data.chapters;
  return (
    <div className="chsum-book">
      <p className="chsum-scope">
        {language === "zh"
          ? "全书总结：缺失或不对齐的章节会自动补写，正在写的章除外"
          : "Whole-book summaries: missing or stale chapters are filled in automatically, except the chapter being written"}
      </p>
      <div className="chip-row">
        {rows.map((row) => (
          <StatusChip
            key={row.chapter_number}
            row={row}
            isCurrent={row.chapter_number === props.current}
            focused={row.chapter_number === data.focused_chapter}
            onGo={props.onGo}
          />
        ))}
      </div>
    </div>
  );
}

function StatusChip(props: {
  row: BookChapterStatusRow;
  isCurrent: boolean;
  focused: boolean;
  onGo: (chapter: number) => void;
}) {
  const language = useLanguage((s) => s.language);
  const r = props.row;
  const cls =
    "chip status-chip" +
    (r.anomaly
      ? " status-chip-anomaly"
      : r.state === "paired"
        ? " status-chip-paired"
        : r.state === "missing"
          ? " status-chip-missing"
          : r.state === "stale"
            ? " status-chip-stale"
            : " status-chip-empty") +
    (r.weight === 0 && !r.anomaly ? " status-chip-skip" : "");
  const label = r.anomaly ? (language === "zh" ? "异常" : "Anomaly") : STATUS_LABEL[r.state][language];
  return (
    <button
      className={cls}
      title={statusChipTitle(r, props.isCurrent, props.focused, language)}
      aria-label={language === "zh" ? `第 ${r.chapter_number} 章，${label}` : `Chapter ${r.chapter_number}, ${label}`}
      onClick={() => props.onGo(r.chapter_number)}
    >
      {r.chapter_number}
      <span className="chip-kind">{label}</span>
    </button>
  );
}

/** 芯片悬停时那句「为什么」。**reason 只写查得到的事实**，一句解释都没有（约束 8）。
 *
 *  **整句模板，不是拼片段**：中英文里「权重」那半句嵌进主句的位置不一样，
 *  两种语言各写一遍完整的句子，不共用一份「先拼 A 再拼 B」的顺序。 */
function statusChipTitle(
  r: BookChapterStatusRow,
  isCurrent: boolean,
  focused: boolean,
  language: Language,
): string {
  if (language === "zh") {
    const head = `第 ${r.chapter_number} 章`;
    if (isCurrent) return `${head}：当前打开的章`;
    if (focused) return `${head}：正在写的章，不自动补写`;
    if (r.anomaly) return `${head}：总结生成出错（不影响其他章，将自动重试）`;
    if (r.state === "empty") return `${head}：尚无正文，无总结可生成`;
    if (r.state === "paired") return `${head}：总结与正文一致`;
    if (r.state === "missing") return `${head}：尚无总结，将自动补写`;
    return `${head}：正文已修改，总结尚未更新，将自动补写`;
  }
  const head = `Chapter ${r.chapter_number}`;
  if (isCurrent) return `${head}: the chapter currently open`;
  if (focused) return `${head}: the chapter being written; not filled in automatically`;
  if (r.anomaly) return `${head}: summary generation failed (other chapters unaffected; retried automatically)`;
  if (r.state === "empty") return `${head}: no text yet, nothing to summarize`;
  if (r.state === "paired") return `${head}: summary matches the text`;
  if (r.state === "missing") return `${head}: no summary yet; filled in automatically`;
  return `${head}: text changed, summary not yet updated; filled in automatically`;
}

/** 这一段总结提到了什么 —— **一排可点的记忆点**。
 *
 *  芯片上写的是**显示名**，不是 id，也不是 label 的英文值：`nodeLabelText` 查的是
 *  `backendMessages.ts::NODE_LABEL`，覆盖率由 `tests/test_wording_guard.py` 拿
 *  Python 的 `NodeLabel` 枚举核对，少一项就红。 */
function Memories(props: {
  hits: SummaryMention[] | undefined;
  failed: boolean;
  opened: string | null;
  onOpen: (id: string) => void;
}) {
  const language = useLanguage((s) => s.language);
  if (props.failed) return <div className="err-box">{mentionsFailed(language)}</div>;
  if (props.hits === undefined) return null;
  if (props.hits.length === 0) {
    // **零带着理由**（§10 约束 8）：一排空白会被读成「引擎没在干活」。
    return (
      <p className="empty chsum-why">
        {language === "zh" ? (
          <>
            此总结未提及角色册中的任何条目。在总结中写入名称，或在角色册中添加该称呼后，可在此追溯。
          </>
        ) : (
            <>
            This summary mentions nothing from the roster. Add a name to the summary, or add that
            name to the roster, to follow it from here.
          </>
        )}
      </p>
    );
  }
  return (
    <div className="chsum-mentions">
      <p className="chsum-why">
        {language === "zh"
          ? "此总结提及：点击查看其他章节的总结是否也提及"
          : "Mentioned in this summary — click one to see which other chapters’ summaries mention it"}
      </p>
      <div className="chip-row">
        {props.hits.map((hit) => (
          <button
            key={hit.node.id}
            className={"chip" + (hit.node.id === props.opened ? " on" : "")}
            // 命中的称呼原文摆在 title 上：屏幕上显示的是正式名，而这一段里写的
            // 可能是「魔尊」——两者不一样时作者有权知道（ADR 0004）。
            title={
              language === "zh"
                ? `这一段里写的是「${hit.surfaces.join("」「")}」`
                : `This paragraph says "${hit.surfaces.join('", "')}"`
            }
            onClick={() => props.onOpen(hit.node.id)}
          >
            {hit.node.name}
            <span className="chip-kind">{nodeLabelText(hit.node.label, language)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** 还有哪几章的总结提到它。**按章号排**，带那一段原文，点章号跳过去。 */
function Trail(props: {
  here: number;
  data: NodeSummaryMentions | undefined;
  loading: boolean;
  failed: boolean;
  onGo: (chapter: number) => void;
}) {
  const language = useLanguage((s) => s.language);
  if (props.failed) return <div className="err-box">{trailFailed(language)}</div>;
  if (props.loading || !props.data)
    return <p className="empty chsum-why">{language === "zh" ? "查找中…" : "Searching…"}</p>;

  const others = props.data.chapters.filter((row) => row.chapter_number !== props.here);
  const name = props.data.node.name;
  if (others.length === 0) {
    return (
      <p className="empty chsum-why">
        {language === "zh" ? (
          <>
            全书仅本章总结提及{name}。此处查找的是总结而非正文，尚无总结的章节不在其中。
          </>
        ) : (
          <>
            Only this chapter’s summary mentions {name}. This searches summaries, not the text;
            chapters without a summary are not included.
          </>
        )}
      </p>
    );
  }
  return (
    <div className="chsum-trail">
      <p className="chsum-why">
        {language === "zh" ? (
          <>
            {name}还出现在以下 {others.length} 章的总结中（按章序）：
          </>
        ) : (
          <>
            {name} also appears in {others.length} other chapter{others.length === 1 ? "" : "s"}’
            summaries (in order):
          </>
        )}
      </p>
      {others.map((row) => (
        <div className="trail-row" key={row.chapter_number}>
          <button className="trail-go" onClick={() => props.onGo(row.chapter_number)}>
            {language === "zh" ? `第 ${row.chapter_number} 章` : `Chapter ${row.chapter_number}`}
          </button>
          <p className="trail-text">{row.summary}</p>
        </div>
      ))}
    </div>
  );
}
