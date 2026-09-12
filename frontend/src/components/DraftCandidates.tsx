import { useEffect, useRef, useState, type ReactNode } from "react";
import { useDraftText } from "../api/hooks";
import type { DraftCandidateView } from "../api/types";
import {
  chaptersOf,
  comparePath,
  draftLabel,
  draftsHeading,
  landedNote,
  openByDefault,
  sideBySide,
  unitsLabel,
} from "../drafts";
import { useLanguage } from "../language";
import { writeCompareHandoff } from "../route";

// 桌上摆着的那几稿（[ADR 0022](docs/adr/0022-drafting-is-a-proposal-not-a-write.md)）。
//
// **同一份数据的三种排布，不是三套东西**（作者的原话就是这个形状）：
//
// | 档 | 什么时候 | 长什么样 |
// |---|---|---|
// | 窄（默认） | 面板窄，或者只有一稿 | 一稿一张卡：第几稿 + 那一稿的自述 + 开头几句 + 「展开」 |
// | 宽 | 作者把面板拖到并排读得下去 | 自动变成几列并排，各自一个滚轮（`drafts.ts::sideBySide`） |
// | 新标签页 | 点那条链接 | `#/compare/{章号}`，同一个应用的另一条路由（`DraftCompare`） |
//
// ── 这块屏幕必须自己做对的三件事 ──────────────────────────────────────────
//
// 1. **不许挑。** 后端不排名、不打分（ADR 0005），这一层更不许——它手上只有一段
//    120 字的开头。**默认摊开的那一版判据只有 `landed`**（助手把哪一版写进了书，
//    那是一个动作），一个都没落盘时**全部收着**，作者自己点。
// 2. **入口不许三章硬摊。** 一批三稿 ≈ 9,000 字，全摊开等于要作者读完三章才做一个决定
//    （ADR 0022 的「代价」第三条）。所以窄档只给自述 + 开头，全文点开才取——
//    `useDraftText` 的 `open` 为假时一个字节都不发。
// 3. **那一串内部标识一个字符都不上屏。** 屏幕上说「第 2 稿」（`ordinal`）。

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

/**
 * 一稿。窄档是一张卡，宽档是一列——**同一个组件**，区别只有「要不要那颗展开按钮」
 * 和外面那层容器的排布。
 *
 * `onToggle` 不给 = 这一稿摊着不收（并排那两档）。
 */
export function DraftCard({
  pid,
  draft,
  open,
  onToggle,
}: {
  pid: string;
  draft: DraftCandidateView;
  open: boolean;
  onToggle?: () => void;
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
        {/* 「推荐位」就是它 —— 一个动作，不是一句评价（ADR 0022）。 */}
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
      {onToggle && (
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
      )}
    </article>
  );
}

/** 「并排比这一章的稿子」那条链接。**它同时干两件事**：跳新标签页，
 *  以及把「哪本书的第几章」留给那个标签页（地址里只放章号，见 `route.ts`）。 */
export function CompareLink({
  pid,
  chapter,
  children,
}: {
  pid: string;
  chapter: number;
  children: ReactNode;
}) {
  const language = useLanguage((s) => s.language);
  return (
    <a
      className="draft-compare"
      href={comparePath(chapter)}
      target="_blank"
      rel="noopener"
      title={
        language === "zh"
          ? "在新标签页并排查看本章各稿"
          : "Open this chapter’s drafts side by side in a new tab"
      }
      onClick={() => writeCompareHandoff({ book: pid, chapter })}
    >
      {children}
    </a>
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
  const boxRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [open, setOpen] = useState<string[]>(() => openByDefault(drafts));

  // 又跑了一轮 = 另一批稿子 ⇒ 默认摊开哪一版要重算（上一批展开过哪几张跟这一批无关）。
  useEffect(() => setOpen(openByDefault(drafts)), [drafts]);

  // **量的是这一块自己有多宽，不是窗口**：作者拖的是中栏那根分隔条，窗口一动不动。
  // 量不到（jsdom / 首帧）就是 0 ⇒ 窄档，而窄档是默认形态（`drafts.ts::sideBySide`）。
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
    // **依赖里带着稿数**：一稿都没有时这个组件整个返回 `null`，要量的那一格 DOM
    // 根本不存在（`boxRef.current` 是 null，上面第一句就退了）。今天 `ChatPanel`
    // 在跑下一轮时会先把回执清掉、于是这块屏幕整个重挂一次，量得到——
    // **但那是调用方的实现细节，不是这个组件的前提**：它没了，症状是「拖多宽都不并排」
    // 而且不报错。空依赖数组等于把那条实现细节写进这里的正确性。
  }, [drafts.length]);

  if (drafts.length === 0) return null;

  const wide = sideBySide(width, drafts.length);
  const note = landedNote(drafts, language);

  return (
    <section
      className="drafts"
      aria-label={language === "zh" ? "本轮稿件" : "Drafts from this round"}
    >
      <div className="drafts-head">
        <b>{draftsHeading(drafts, language)}</b>
        <span className="spacer" />
        {chaptersOf(drafts).map((chapter) => (
          <CompareLink key={chapter} pid={pid} chapter={chapter}>
            {language === "zh"
              ? `并排查看第 ${chapter} 章各稿 ↗`
              : `Compare chapter ${chapter}’s drafts side by side ↗`}
          </CompareLink>
        ))}
      </div>
      {note && <p className="chat-receipt-note">{note}</p>}
      <div className={wide ? "draft-cards wide" : "draft-cards"} ref={boxRef}>
        {drafts.map((draft) =>
          wide ? (
            // 宽档：每一列自己一个滚轮，全文都摊着 —— 作者拖到这个宽度就是要并排读。
            <DraftCard key={draft.id} pid={pid} draft={draft} open />
          ) : (
            <DraftCard
              key={draft.id}
              pid={pid}
              draft={draft}
              open={open.includes(draft.id)}
              onToggle={() =>
                setOpen((prev) =>
                  prev.includes(draft.id)
                    ? prev.filter((id) => id !== draft.id)
                    : [...prev, draft.id],
                )
              }
            />
          ),
        )}
      </div>
    </section>
  );
}
