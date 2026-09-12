import { useState } from "react";
import {
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
    ? "这一段没能保存，而系统没能说清是为什么。过一会儿再试一次。"
    : "This paragraph couldn't be saved, and the system couldn't say why. Try again in a moment.";
const retractFailed = (language: Language): string =>
  language === "zh"
    ? "没能撤回这一章的总结，而系统没能说清是为什么。过一会儿再试一次。"
    : "Couldn't retract this chapter's summary, and the system couldn't say why. Try again in a moment.";
const generateFailed = (language: Language): string =>
  language === "zh"
    ? "这一章的总结没能生成，而系统没能说清是为什么。过一会儿再试一次。"
    : "Couldn't generate this chapter's summary, and the system couldn't say why. Try again in a moment.";
const readFailed = (language: Language): string =>
  language === "zh"
    ? "这一章的总结这会儿没读出来。上面空着不代表没有总结 —— 刷新一下再看。"
    : "Couldn't load this chapter's summary right now. An empty box above doesn't mean there's no summary — refresh and check again.";

const mentionsFailed = (language: Language): string =>
  language === "zh"
    ? "这一段提到了什么，这会儿没查出来。下面空着不代表它谁也没提到 —— 刷新一下再看。"
    : "Couldn't check what this paragraph mentions right now. An empty box below doesn't mean it mentions nobody — refresh and check again.";
const trailFailed = (language: Language): string =>
  language === "zh"
    ? "别的章有没有提到它，这会儿没查出来。过一会儿再试一次。"
    : "Couldn't check whether other chapters mention this right now. Try again in a moment.";

export function SummaryTab() {
  const { projectId, chapter, setChapter, setPage } = useCoords();
  const language = useLanguage((s) => s.language);
  const status = useChapterSummary(projectId, chapter);
  const book = useBookSummaryStatus(projectId);
  const edit = useEditSummary(projectId ?? "");
  const retract = useRetractSummary(projectId ?? "");
  const generate = useGenerateSummary(projectId ?? "");

  // 作者正在改的那一段。**`null` = 他没在改**，屏幕跟着服务端那份走。
  // 分成两个状态是因为重取随时会落地（后台整理刚补完一章总结就会），
  // 而**没保存过的字哪儿都找不回来**——跟着重取一起刷掉就是静默吃掉他打的字。
  const [typed, setTyped] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  // 他点开的那个记忆点。**换章时不清**由 `key` 兜着（这一格整块跟着章号重挂），
  // 所以这儿不写第二份清理逻辑。
  const [opened, setOpened] = useState<string | null>(null);

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
    setChapter(n);
    setPage("workbench");
  }

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

  if (failed) return <div className="err-box">{failed}</div>;
  if (!data)
    return (
      <div className="empty">
        {language === "zh" ? "正在看这一章有没有总结…" : "Checking whether this chapter has a summary…"}
      </div>
    );

  // 这一章还没写：**没得总结**，也就没有一颗会花钱的按钮该在这儿亮着。
  if (!data.has_text) {
    return (
      <div className="chsum">
        {bookStatusEl}
        <p className="empty">
          {language === "zh" ? (
            <>
              第 {chapter} 章还没有正文，所以没有总结可写 —— 总结是从这一章的正文压出来的。
              写完这一章、存一次，这里就有得生成了。
            </>
          ) : (
            <>
              Chapter {chapter} doesn’t have any text yet, so there’s no summary to write —
              summaries are distilled from a chapter’s own text. Write this chapter and save it,
              and you’ll be able to generate one here.
            </>
          )}
        </p>
        <div className="chsum-actions">
          <button onClick={jumpToText}>{language === "zh" ? "跳到原文" : "Jump to the text"}</button>
        </div>
      </div>
    );
  }

  return (
    <div className="chsum">
      {bookStatusEl}
      <p className="chsum-scope">
        {language === "zh" ? `第 ${chapter} 章 章节总结` : `Chapter ${chapter} summary`}
      </p>

      <textarea
        className="chsum-text"
        aria-label={language === "zh" ? `第 ${chapter} 章的总结` : `Summary for chapter ${chapter}`}
        placeholder={
          language === "zh"
            ? "这一章讲了什么。保存正文后系统会自动写一段，也可以自己写。"
            : "What happens in this chapter. The system writes one automatically after you save the text; you can also write your own."
        }
        rows={6}
        value={shown}
        disabled={busy}
        onChange={(e) => setTyped(e.target.value)}
      />

      {data.summary === null && (
        // **零带着理由。** 两种零的下一步动作相反，所以它们说两句不一样的话。
        <p className="empty chsum-why">
          {language === "zh"
            ? data.retracted
              ? "这一章的总结你撤回了 —— 写这一章时不带它，系统也不会再自动补一份回来。想要一份新的，点下面的「重新生成」（跑一次模型）；自己写一段也行，那不花钱。"
              : "这一章还没有总结。保存正文之后系统会自动写一段（那要跑一次模型）；不想等，点下面的「生成」，或者自己写一段，后者不花钱。"
            : data.retracted
              ? "You retracted this chapter’s summary — it won’t be included when writing this chapter, and the system won’t bring it back on its own. For a new one, click “Regenerate” below (that runs the model); writing one yourself is free."
              : "This chapter doesn’t have a summary yet. The system writes one automatically after you save the text (that runs the model); if you don’t want to wait, click “Generate” below — or write one yourself for free."}
        </p>
      )}

      {confirming && (
        <div className="warn">
          {language === "zh" ? (
            <>
              撤回之后，写这一章时就不带这一段了。你写过的正文一个字都不动。系统不会再自动
              补一份回来，但「重新生成」那颗按钮还在 —— 想再要一份，点它跑一次模型（花钱），
              或者自己写一段（不花钱）。
            </>
          ) : (
            <>
              Once retracted, this summary won’t be included when writing this chapter. Your own
              text won’t be touched at all. The system won’t bring it back on its own, but the
              “Regenerate” button stays — click it to run the model again (which costs money), or
              write one yourself for free.
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
              {language === "zh" ? "撤回它" : "Retract it"}
            </button>
            <button disabled={busy} onClick={() => setConfirming(false)}>
              {language === "zh" ? "算了" : "Cancel"}
            </button>
          </>
        )}
        <button onClick={jumpToText}>{language === "zh" ? "跳到原文" : "Jump to the text"}</button>
      </div>

      {refused && <div className="err-box">{refused}</div>}

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
    ? "全书总结状态这会儿没读出来。上面空着不代表没总结 —— 刷新一下再看。"
    : "Couldn't load the whole-book summary status right now. An empty box above doesn't mean there are no summaries — refresh and check again.";

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
          ? "全书总结 —— 缺章 / 不对齐每 30 分钟自动补，正在写的章不碰："
          : "Whole-book summary status — missing or misaligned chapters are auto-filled every 30 minutes; the chapter you're writing is left alone:"}
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
    if (isCurrent) return `${head} —— 你现在正看着它。`;
    if (focused) return `${head} —— 作者正在写的那一章，先不碰。`;
    if (r.anomaly) return `${head}的总结生成时出了岔子（不阻塞别的章，会照常重试）。`;
    const weight = r.weight === 0 ? "这一轮不看" : `这一轮权重 ${r.weight}（越近越必补）`;
    if (r.state === "empty") return `${head}还没有正文，没得总结。`;
    if (r.state === "paired") return `${head}的总结和正文对得上。`;
    if (r.state === "missing") return `${head}还没有总结 —— ${weight}。`;
    return `${head}的正文动过，总结还没跟着覆写 —— ${weight}。`;
  }
  const head = `Chapter ${r.chapter_number}`;
  if (isCurrent) return `${head} — you're currently looking at it.`;
  if (focused) return `${head} — the chapter you're writing; left alone for now.`;
  if (r.anomaly) return `${head}'s summary hit a snag while generating (doesn't block other chapters; will retry automatically).`;
  const weight =
    r.weight === 0 ? "not considered this round" : `weight ${r.weight} this round (closer chapters are prioritized)`;
  if (r.state === "empty") return `${head} doesn't have any text yet, so there's nothing to summarize.`;
  if (r.state === "paired") return `${head}'s summary matches its text.`;
  if (r.state === "missing") return `${head} doesn't have a summary yet — ${weight}.`;
  return `${head}'s text has changed and the summary hasn't caught up — ${weight}.`;
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
            这一段里没出现角色册上的任何人或东西 —— 所以没有可以顺过去的地方。
            写进去一个名字（或者去角色册把那个称呼建上），这里就有得点了。
          </>
        ) : (
          <>
            Nobody or nothing from the roster appears in this paragraph, so there’s nothing to
            follow from here. Add a name (or add that name as an alias in the roster), and this
            will have something to click.
          </>
        )}
      </p>
    );
  }
  return (
    <div className="chsum-mentions">
      <p className="chsum-why">
        {language === "zh"
          ? "这一段提到了 —— 点一个，看还有哪几章的总结也提到它："
          : "This paragraph mentions — click one to see which other chapters' summaries mention it too:"}
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
    return <p className="empty chsum-why">{language === "zh" ? "正在翻…" : "Searching…"}</p>;

  const others = props.data.chapters.filter((row) => row.chapter_number !== props.here);
  const name = props.data.node.name;
  if (others.length === 0) {
    return (
      <p className="empty chsum-why">
        {language === "zh" ? (
          <>
            全书只有这一章的总结提到了{name}。别的章可能写到过，只是那几章还没有总结 ——
            这里翻的是总结，不是正文。
          </>
        ) : (
          <>
            Only this chapter’s summary mentions {name} in the whole book. Other chapters might
            mention {name} too — they just don’t have summaries yet. This searches summaries, not
            the text itself.
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
            {name}还出现在这 {others.length} 章的总结里（按顺序）：
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
