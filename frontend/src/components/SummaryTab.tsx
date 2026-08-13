import { useState } from "react";
import {
  useChapterSummary,
  useEditSummary,
  useGenerateSummary,
  useRetractSummary,
  useSummaryWindow,
} from "../api/hooks";
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

/** 后端一句话都没写时才轮到的那几句。 */
const GENERATE_FAILED =
  "这一章的总结没能生成，而系统没能说清是为什么。过一会儿再试一次。";
const SAVE_FAILED = "这一段没能保存，而系统没能说清是为什么。过一会儿再试一次。";
const RETRACT_FAILED = "没能撤回这一章的总结，而系统没能说清是为什么。过一会儿再试一次。";
const READ_FAILED =
  "这一章的总结这会儿没读出来。上面空着不代表没有总结 —— 刷新一下再看。";

export function SummaryTab() {
  const { projectId, chapter, setPage } = useCoords();
  const status = useChapterSummary(projectId, chapter);
  const covered = useSummaryWindow(projectId, chapter);
  const generate = useGenerateSummary(projectId ?? "");
  const edit = useEditSummary(projectId ?? "");
  const retract = useRetractSummary(projectId ?? "");

  // 作者正在改的那一段。**`null` = 他没在改**，屏幕跟着服务端那份走。
  // 分成两个状态是因为重取随时会落地（后台整理刚补完一章总结就会），
  // 而**没保存过的字哪儿都找不回来**——跟着重取一起刷掉就是静默吃掉他打的字。
  const [typed, setTyped] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const data = status.data;
  const stored = data?.summary ?? "";
  const shown = typed ?? stored;
  const dirty = typed !== null && typed !== stored;
  const busy = generate.isPending || edit.isPending || retract.isPending;

  const failed = status.isError ? (refusalText(status.error, READ_FAILED) ?? READ_FAILED) : null;
  const refused =
    refusalText(generate.error, GENERATE_FAILED) ??
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

  if (failed) return <div className="err-box">{failed}</div>;
  if (!data) return <div className="empty">正在看这一章有没有总结…</div>;

  // 这一章还没写：**没得总结**，也就没有一颗会花钱的按钮该在这儿亮着。
  if (!data.has_text) {
    return (
      <div className="chsum">
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
        {!confirming && (
          <button disabled={busy} onClick={() => generate.mutate(chapter, { onSuccess: done })}>
            {generate.isPending
              ? "正在生成…"
              : data.summary === null
                ? "生成（要跑一次模型）"
                : "重新生成（要跑一次模型）"}
          </button>
        )}
        <button onClick={jumpToText}>跳到原文</button>
      </div>

      {refused && <div className="err-box">{refused}</div>}
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
