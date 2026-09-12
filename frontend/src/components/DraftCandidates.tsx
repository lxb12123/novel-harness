import { useState } from "react";
import { api, proj } from "../api/client";
import type { DraftCandidateDetail, DraftCandidateView } from "../api/types";
import { useLiveDraft } from "../liveDraft";
import { draftLabel, draftsHeading, landedNote, unitsLabel } from "../drafts";
import { useLanguage } from "../language";

// 这一轮写出来的那几稿（[ADR 0022](docs/adr/0022-drafting-is-a-proposal-not-a-write.md)，
// 入口 2026-09-12 起按 [ADR 0048](docs/adr/0048-drafting-writes-the-chapter.md)）。
//
// **稿子的字一个都不在这儿。** 作者 2026-09-12 反复说的是「一定要在左边写」「不要在这个
// 里面留这种东西」：正在写的那一稿流进左边的编辑器（`liveDraft.ts`），写完留在那儿等他按保存。
// 所以这儿每一稿只有**一行**：第几稿 · 字数 · 它在哪儿，加写手那句自述。三种「在哪儿」：
//
// | | 那一行说的 |
// |---|---|
// | 在编辑器里（未保存） | 「已放入编辑器，按『保存』写入本章」 |
// | 已经保存进书 | 「已写入第 N 章」 |
// | 还在桌上（一批几稿的第二稿起 / 上一稿被替下来的 / 按停砍断的） | 一颗「放入编辑器」 |
//
// 「放入编辑器」走的是和流一样的路（`liveDraft.ts::present`）：读它的地方也是左边的正文。
// （并排比几稿那一页和面板头上「本章 N 稿」的入口 2026-09-12 随作者一句「这块就不要了」撤了。）
//
// ── 这块屏幕必须自己做对的两件事 ──────────────────────────────────────────
//
// 1. **不许挑。** 后端不排名、不打分（ADR 0005），这一层更不许——几稿并列，作者自己点。
// 2. **那一串内部标识一个字符都不上屏。** 屏幕上说「第 2 稿」（`ordinal`）。

type Where = "editor" | "unchanged" | "saved" | "desk";

/** 一稿一行。 */
function DraftRow({
  draft,
  where,
  failed,
  onPlace,
}: {
  draft: DraftCandidateView;
  where: Where;
  /** 「放入编辑器」那一下没取到全文。 */
  failed: boolean;
  onPlace: () => void;
}) {
  const language = useLanguage((s) => s.language);
  const said =
    where === "saved"
      ? language === "zh"
        ? `已写入第 ${draft.chapter} 章`
        : `Written into chapter ${draft.chapter}`
      : where === "editor"
        ? language === "zh"
          ? "已放入编辑器，按「保存」写入本章"
          : "In the editor; click Save to write it into the chapter"
        : where === "unchanged"
          ? language === "zh"
            ? "已放入编辑器，与本章正文相同"
            : "In the editor; identical to the chapter text"
          : null;
  return (
    <p className="draft-row">
      <b>{draftLabel(draft, language)}</b>
      <span className="draft-meta">
        {" · "}
        {unitsLabel(draft.units, language)}
        {said !== null && (
          <>
            {" · "}
            {said}
          </>
        )}
      </span>
      {where === "desk" && (
        <button className="link draft-place" onClick={onPlace}>
          {language === "zh" ? "放入编辑器" : "Put in the editor"}
        </button>
      )}
      {/* 不解释「为什么」——这一层不知道（§10 约束 8）。 */}
      {failed && (
        <span className="draft-row-failed">
          {language === "zh"
            ? "稿件读取失败，请刷新后重试"
            : "This draft could not be loaded; refresh to try again"}
        </span>
      )}
      {/* **这一稿被砍断过。** 画在自述前面，因为它改变的是这一稿该怎么读：断在半句上是
          **它没写完**，不是写作模型的写法。措辞照抄后端那一句（`stopped_reason`）。 */}
      {draft.stopped_reason.trim() !== "" && (
        <span className="draft-row-stopped">{draft.stopped_reason}</span>
      )}
      {/* 写手那句自述——它是这一稿的一句说明，不是评价（引擎不打分，ADR 0005）。
          **空的不画**：替它编一句「这一版更冷」正是 ADR 0005 拦的那种事。 */}
      {draft.note.trim() !== "" && <span className="draft-row-note">{draft.note}</span>}
    </p>
  );
}

export function DraftCandidates({
  pid,
  drafts,
}: {
  pid: string;
  drafts: readonly DraftCandidateView[];
}) {
  const language = useLanguage((s) => s.language);
  // 每一稿现在在哪儿：编辑器里（未保存）/ 已经保存进书 / 还在桌上。
  const placed = useLiveDraft((s) => s.placed);
  const saved = useLiveDraft((s) => s.saved);
  const present = useLiveDraft((s) => s.present);
  const whereOf = (d: DraftCandidateView): Where =>
    d.landed || saved.includes(d.id)
      ? "saved"
      : placed?.draftId === d.id
        ? placed.unchanged
          ? "unchanged"
          : "editor"
        : "desk";
  /** 「放入编辑器」没取到全文的那几稿（那一行下面说一句）。 */
  const [failed, setFailed] = useState<string[]>([]);
  /** 作者点「放入编辑器」：取那一稿的全文，走和流一样的路进编辑器（`liveDraft.ts::present`）。 */
  const place = async (d: DraftCandidateView) => {
    setFailed((prev) => prev.filter((id) => id !== d.id));
    try {
      const detail = await api.get<DraftCandidateDetail>(
        proj(pid, `/drafts/${encodeURIComponent(d.id)}`),
      );
      present({ chapter: d.chapter, draftId: d.id, text: detail.text });
    } catch {
      setFailed((prev) => [...prev, d.id]);
    }
  };

  if (drafts.length === 0) return null;

  const note = landedNote(drafts, language, saved);

  return (
    <section
      className="drafts"
      aria-label={language === "zh" ? "本轮稿件" : "Drafts from this round"}
    >
      <div className="drafts-head">
        <b>{draftsHeading(drafts, language)}</b>
      </div>
      {/* 顺序照后端给的（`(chapter, ordinal)`）：并发跑的稿子谁先回来是随机的，
          在这儿重排一次，作者每次刷新看到的次序就会变。 */}
      {drafts.map((draft) => (
        <DraftRow
          key={draft.id}
          draft={draft}
          where={whereOf(draft)}
          failed={failed.includes(draft.id)}
          onPlace={() => void place(draft)}
        />
      ))}
      {note && <p className="chat-receipt-note">{note}</p>}
    </section>
  );
}
