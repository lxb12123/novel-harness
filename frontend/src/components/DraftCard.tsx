import type { DraftCandidateView } from "../api/types";
import { useDraftText } from "../api/hooks";
import { draftLabel, unitsLabel } from "../drafts";
import { useLanguage } from "../language";

// 一稿摊开来读的那张卡——**只在并排比那一页上**（`DraftCompare`，新标签页）。
//
// 对话面板里 2026-09-12 起不再有它（作者：「一定要在左边写」「不要在这个里面留这种东西」）：
// 那儿每一稿只有一行（`DraftCandidates`），读稿子的地方是左边的正文编辑器。并排读几稿
// 是作者专门点开来的另一页，这张卡只在那儿。
//
// ── 这张卡必须自己做对的两件事 ────────────────────────────────────────────
//
// 1. **不许挑。** 后端不排名、不打分（ADR 0005），这一层更不许——它手上只有一段
//    120 字的开头。收着的卡**全部收着**，作者自己点。
// 2. **入口不许三章硬摊。** 一批三稿 ≈ 9,000 字，全摊开等于要作者读完三章才做一个决定
//    （ADR 0022 的「代价」第三条）。所以收着的卡只给自述 + 开头，全文点开才取——
//    `useDraftText` 的 `open` 为假时一个字节都不发。

/** 一稿的全文。**只在摊开的时候才挂载**，所以「取全文」和「作者要看」是同一件事。 */
function DraftBody({ pid, draft }: { pid: string; draft: DraftCandidateView }) {
  const language = useLanguage((s) => s.language);
  const detail = useDraftText(pid, draft.id, true);
  if (detail.data) return <p className="draft-text">{detail.data.text}</p>;
  // 两句都不解释「为什么」——这一层不知道（§10 约束 8）。
  if (detail.isError) {
    return (
      <p className="draft-loading">
        {language === "zh"
          ? "稿件读取失败，请刷新后重试"
          : "This draft could not be loaded; refresh to try again"}
      </p>
    );
  }
  return (
    <p className="draft-loading">{language === "zh" ? "正在读取稿件…" : "Loading this draft…"}</p>
  );
}

/** 一稿。摊开（`open`）就取全文，收着只有自述 + 开头几句 + 「展开」。 */
export function DraftCard({
  pid,
  draft,
  open,
  onToggle,
}: {
  pid: string;
  draft: DraftCandidateView;
  open: boolean;
  onToggle: () => void;
}) {
  const language = useLanguage((s) => s.language);
  return (
    <article className={open ? "draft-card open" : "draft-card"}>
      <div className="draft-card-head">
        <b>{draftLabel(draft, language)}</b>
        <span className="draft-meta">
          {language === "zh" ? `第 ${draft.chapter} 章` : `Chapter ${draft.chapter}`} ·{" "}
          {unitsLabel(draft.units, language)}
        </span>
        {/* 「已写入」就是它 —— 一个动作（作者按过保存），不是一句评价（ADR 0022）。 */}
        {draft.landed && (
          <span className="draft-badge">
            {language === "zh" ? "已写入本章" : "Written into this chapter"}
          </span>
        )}
      </div>
      {/* **这一稿被砍断过。** 画在自述和正文**前面**，因为它改变的是后面那两样该怎么读：
          底下那段字断在半句上是**它没写完**，不是写作模型的写法。
          措辞照抄后端那一句（`stopped_reason`），这一层不翻译、不缩写。 */}
      {draft.stopped_reason.trim() !== "" && (
        <p className="draft-stopped">{draft.stopped_reason}</p>
      )}
      {/* **自述可能是空的**（写它的那个模型这次没说）。空的时候这儿什么都不画——
          替它编一句「这一版更冷」正是 ADR 0005 拦的那种事。 */}
      {draft.note.trim() !== "" && <p className="draft-note">{draft.note}</p>}
      {/* 它喂了写手什么（ADR 0047 守着的第二条线）：这一稿的要求 + 补的资料。
          默认收着——作者先看稿子，挑错了才翻这儿。**空的不画**：旧机制写的稿这两格是空串，
          摆一个空标题等于说「它什么都没要求」。 */}
      {draft.brief.trim() !== "" && (
        <details className="draft-brief">
          <summary>{language === "zh" ? "起草要求" : "Brief for this draft"}</summary>
          <p className="draft-brief-text">{draft.brief}</p>
        </details>
      )}
      {draft.materials.length > 0 && (
        <details className="draft-brief">
          <summary>
            {language === "zh"
              ? `补充资料 · ${draft.materials.length} 段`
              : `Supporting material · ${draft.materials.length}`}
          </summary>
          <ul className="draft-materials">
            {draft.materials.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </details>
      )}
      {open ? (
        <DraftBody pid={pid} draft={draft} />
      ) : (
        <p className="draft-preview">{draft.preview}</p>
      )}
      <button
        className="draft-toggle"
        aria-expanded={open}
        aria-label={
          language === "zh"
            ? `${open ? "收起" : "展开"}${draftLabel(draft, language)}`
            : `${open ? "Collapse" : "Expand"} ${draftLabel(draft, language)}`
        }
        onClick={onToggle}
      >
        {language === "zh" ? (open ? "收起" : "展开") : open ? "Collapse" : "Expand"}
      </button>
    </article>
  );
}
