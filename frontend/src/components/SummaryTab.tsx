import { useState } from "react";
import {
  useBookSummaryStatus,
  useChapterSummary,
  useEditSummary,
  useNodeSummaryMentions,
  useRetractSummary,
  useSummaryMentions,
  useSummaryWindow,
} from "../api/hooks";
import { LABEL_ZH } from "../api/types";
import type {
  BookChapterStatusRow,
  BookSummaryStatus,
  NodeSummaryMentions,
  SummaryMention,
} from "../api/types";
import { refusalText } from "../chat";
import { useCoords } from "../store";

// 右栏「章节总结」这一格。**跟着左栏选中的那一章走**（同「人物认知」「检查」那几格）。
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
// 1. **「机器压缩的背景」那句免责，贴到作者自己写的那一段上。** 判据是后端给的
//    `author_written`，不是「这一段看起来像谁写的」。
// 2. **「还没生成」贴到他刚撤回的那一章上。** 两种「没有」在起草那边完全同义（都不进
//    prompt），可下一步动作正好相反：一种要去生成，一种是他半秒钟前自己做的。
//    催他去补一件刚做完的事，是这块面板最容易说出口的那句假话。
// 3. **第二份措辞。** 拒绝的话由后端写（`refusalText`），这儿只在后端一个字都没说的
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
const SAVE_FAILED = "这一段没能保存，而系统没能说清是为什么。过一会儿再试一次。";
const RETRACT_FAILED = "没能撤回这一章的总结，而系统没能说清是为什么。过一会儿再试一次。";
const READ_FAILED =
  "这一章的总结这会儿没读出来。上面空着不代表没有总结 —— 刷新一下再看。";

const MENTIONS_FAILED =
  "这一段提到了什么，这会儿没查出来。下面空着不代表它谁也没提到 —— 刷新一下再看。";
const TRAIL_FAILED = "别的章有没有提到它，这会儿没查出来。过一会儿再试一次。";

export function SummaryTab() {
  const { projectId, chapter, setChapter, setPage } = useCoords();
  const status = useChapterSummary(projectId, chapter);
  const covered = useSummaryWindow(projectId, chapter);
  const book = useBookSummaryStatus(projectId);
  const edit = useEditSummary(projectId ?? "");
  const retract = useRetractSummary(projectId ?? "");

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
  const busy = edit.isPending || retract.isPending;

  const failed = status.isError ? (refusalText(status.error, READ_FAILED) ?? READ_FAILED) : null;
  const refused =
    refusalText(edit.error, SAVE_FAILED) ??
    refusalText(retract.error, RETRACT_FAILED);

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
  if (!data) return <div className="empty">正在看这一章有没有总结…</div>;

  // 这一章还没写：**没得总结**，也就没有一颗会花钱的按钮该在这儿亮着。
  if (!data.has_text) {
    return (
      <div className="chsum">
        {bookStatusEl}
        <p className="empty">
          第 {chapter} 章还没有正文，所以没有总结可写 —— 总结是从这一章的正文压出来的。
          写完这一章、存一次，这里就有得生成了。
        </p>
        <div className="chsum-actions">
          <button onClick={jumpToText}>跳到原文</button>
        </div>
      </div>
    );
  }

  return (
    <div className="chsum">
      {bookStatusEl}
      <p className="chsum-scope">{coverageLine(chapter, covered.data)}</p>

      <textarea
        className="chsum-text"
        aria-label={`第 ${chapter} 章的总结`}
        placeholder="这一章讲了什么。写一段，或者点「生成」让模型压一段出来。"
        rows={6}
        value={shown}
        disabled={busy}
        onChange={(e) => setTyped(e.target.value)}
      />

      {data.summary === null ? (
        // **零带着理由。** 两种零的下一步动作相反，所以它们说两句不一样的话。
        <p className="empty chsum-why">
          {data.retracted
            ? "这一章的总结被你撤回了 —— 写这一章的时候不会带上它。想要一份新的，点「重新生成」（要跑一次模型）；也可以自己写一段，那不花钱。"
            : "这一章还没有总结。生成要跑一次模型（花钱），所以得你自己点；不想花这个钱，就自己写一段。"}
        </p>
      ) : data.author_written ? (
        <p className="chsum-why">这一段是你自己写的。</p>
      ) : (
        <p className="chsum-why">
          这一段是模型压出来的背景，没经过你确认 —— 起草时只当线索用。不对就直接改。
        </p>
      )}

      {confirming && (
        <div className="warn">
          撤回之后，写这一章时就不带这一段了。你写过的正文一个字都不动；想再要一份总结，
          得再跑一次模型（花钱），或者自己写一段。
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
              保存这一段
            </button>
            <button disabled={busy} onClick={() => setTyped(null)}>
              放弃修改
            </button>
          </>
        )}
        {!dirty && data.summary !== null && !confirming && (
          <button className="danger" disabled={busy} onClick={() => setConfirming(true)}>
            撤回
          </button>
        )}
        {confirming && (
          <>
            <button
              className="danger"
              disabled={busy}
              onClick={() => retract.mutate(chapter, { onSuccess: done })}
            >
              撤回它
            </button>
            <button disabled={busy} onClick={() => setConfirming(false)}>
              算了
            </button>
          </>
        )}
        <button onClick={jumpToText}>跳到原文</button>
      </div>

      {refused && <div className="err-box">{refused}</div>}

      {/* **这一章根本没有总结时，下面这一整层不出现。** 后端那时回的是空表，
          而「这一段里没出现花名册上的任何人」在没有「这一段」的时候是一句假话——
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

const BOOK_STATUS_FAILED =
  "全书总结状态这会儿没读出来。上面空着不代表没总结 —— 刷新一下再看。";

/** 芯片上那句短状态。`paired` 绿 / `missing` 橙 / `stale` 棕 / `empty` 灰。 */
const STATUS_LABEL: Record<BookChapterStatusRow["state"], string> = {
  paired: "有",
  missing: "缺",
  stale: "不对齐",
  empty: "空",
};

function BookStatus(props: {
  data: BookSummaryStatus | undefined;
  failed: boolean;
  current: number;
  onGo: (chapter: number) => void;
}) {
  if (props.failed) return <div className="err-box">{BOOK_STATUS_FAILED}</div>;
  if (!props.data) return null;

  const data = props.data;
  const rows = data.chapters;
  const paired = rows.filter((r) => r.state === "paired").length;
  const missing = rows.filter((r) => r.state === "missing").length;
  const stale = rows.filter((r) => r.state === "stale").length;
  const anomaly = rows.filter((r) => r.anomaly).length;
  return (
    <div className="chsum-book">
      <p className="chsum-scope">全书总结 —— 缺章 / 不对齐每 30 分钟自动补，正在写的章不碰：</p>
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
      <p className="chsum-why">{bookStatusLine(data, paired, missing, stale, anomaly)}</p>
    </div>
  );
}

function StatusChip(props: {
  row: BookChapterStatusRow;
  isCurrent: boolean;
  focused: boolean;
  onGo: (chapter: number) => void;
}) {
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
  const label = r.anomaly ? "异常" : STATUS_LABEL[r.state];
  return (
    <button
      className={cls}
      title={statusChipTitle(r, props.isCurrent, props.focused)}
      aria-label={`第 ${r.chapter_number} 章，${r.anomaly ? "异常" : label}`}
      onClick={() => props.onGo(r.chapter_number)}
    >
      {r.chapter_number}
      <span className="chip-kind">{label}</span>
    </button>
  );
}

/** 芯片悬停时那句「为什么」。**reason 只写查得到的事实**，一句解释都没有（约束 8）。 */
function statusChipTitle(
  r: BookChapterStatusRow,
  isCurrent: boolean,
  focused: boolean,
): string {
  const head = `第 ${r.chapter_number} 章`;
  if (isCurrent) return `${head} —— 你现在正看着它。`;
  if (focused) return `${head} —— 作者正在写的那一章，先不碰。`;
  if (r.anomaly) return `${head}的总结生成时出了岔子（不阻塞别的章，会照常重试）。`;
  const weight =
    r.weight === 0 ? "这一轮不看" : `这一轮权重 ${r.weight}（越近越必补）`;
  if (r.state === "empty") return `${head}还没有正文，没得总结。`;
  if (r.state === "paired") return `${head}的总结和正文对得上。`;
  if (r.state === "missing") return `${head}还没有总结 —— ${weight}。`;
  return `${head}的正文动过，总结还没跟着覆写 —— ${weight}。`;
}

/** 那一行总结。**数字全来自后端视图**（前端不数第二遍会漂的账）。 */
function bookStatusLine(
  data: BookSummaryStatus,
  paired: number,
  missing: number,
  stale: number,
  anomaly: number,
): string {
  const head = `全书 ${data.chapters.length} 章：${paired} 章有总结`;
  const extras: string[] = [];
  if (missing) extras.push(`${missing} 章缺`);
  if (stale) extras.push(`${stale} 章不对齐`);
  if (anomaly) extras.push(`${anomaly} 章生成异常`);
  const tail = extras.length ? `、${extras.join("、")}。` : "，都跟正文对得上。";
  const origin =
    data.focused_chapter != null
      ? `作者正写在第 ${data.focused_chapter} 章，那一章不碰。`
      : "没有作者在位信号，全都够资格排进自动补全。";
  return `${head}${tail}${origin}`;
}

/** 这一段总结提到了什么 —— **一排可点的记忆点**。
 *
 *  芯片上写的是**显示名**，不是 id，也不是 label 的英文值：`LABEL_ZH` 是一张
 *  「类型上全列」的表（同 `ActivityLog.ACTOR_ZH`），少一项 `tsc` 就红。 */
function Memories(props: {
  hits: SummaryMention[] | undefined;
  failed: boolean;
  opened: string | null;
  onOpen: (id: string) => void;
}) {
  if (props.failed) return <div className="err-box">{MENTIONS_FAILED}</div>;
  if (props.hits === undefined) return null;
  if (props.hits.length === 0) {
    // **零带着理由**（§10 约束 8）：一排空白会被读成「引擎没在干活」。
    return (
      <p className="empty chsum-why">
        这一段里没出现花名册上的任何人或东西 —— 所以没有可以顺过去的地方。
        写进去一个名字（或者去花名册把那个称呼建上），这里就有得点了。
      </p>
    );
  }
  return (
    <div className="chsum-mentions">
      <p className="chsum-why">这一段提到了 —— 点一个，看还有哪几章的总结也提到它：</p>
      <div className="chip-row">
        {props.hits.map((hit) => (
          <button
            key={hit.node.id}
            className={"chip" + (hit.node.id === props.opened ? " on" : "")}
            // 命中的称呼原文摆在 title 上：屏幕上显示的是正式名，而这一段里写的
            // 可能是「魔尊」——两者不一样时作者有权知道（ADR 0004）。
            title={`这一段里写的是「${hit.surfaces.join("」「")}」`}
            onClick={() => props.onOpen(hit.node.id)}
          >
            {hit.node.name}
            <span className="chip-kind">{LABEL_ZH[hit.node.label]}</span>
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
  if (props.failed) return <div className="err-box">{TRAIL_FAILED}</div>;
  if (props.loading || !props.data) return <p className="empty chsum-why">正在翻…</p>;

  const others = props.data.chapters.filter((row) => row.chapter_number !== props.here);
  const name = props.data.node.name;
  if (others.length === 0) {
    return (
      <p className="empty chsum-why">
        全书只有这一章的总结提到了{name}。别的章可能写到过，只是那几章还没有总结 ——
        这里翻的是总结，不是正文。
      </p>
    );
  }
  return (
    <div className="chsum-trail">
      <p className="chsum-why">
        {name}还出现在这 {others.length} 章的总结里（按顺序）：
      </p>
      {others.map((row) => (
        <div className="trail-row" key={row.chapter_number}>
          <button className="trail-go" onClick={() => props.onGo(row.chapter_number)}>
            第 {row.chapter_number} 章
          </button>
          <p className="trail-text">{row.summary}</p>
          {!row.author_written && (
            <p className="trail-why">这一段是模型压出来的，没经过你确认。</p>
          )}
        </div>
      ))}
    </div>
  );
}

/** 「写这一章的时候带得上几段」。**这一句只读后端算好的窗口**，
 *  不在前端按「近八章」之类的常量推一份——那是第二个会漂的边界。 */
function coverageLine(
  chapter: number,
  window: { summarized: number; missing: number[]; chapters: unknown[] } | undefined,
): string {
  if (!window) return "　";
  if (window.chapters.length === 0) {
    return `第 ${chapter} 章前面没有别的章，起草这一章时不带旧章节的总结。`;
  }
  const missing = window.missing.length;
  const head = `写第 ${chapter} 章时，前面那些章里有 ${window.summarized} 段总结带得上。`;
  return missing === 0
    ? head
    : `${head}还有 ${missing} 章有正文却没有总结 —— 那几章的内容进不了这一稿。`;
}
