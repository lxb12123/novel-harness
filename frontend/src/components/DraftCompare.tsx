import { useEffect, useMemo, useState } from "react";
import { useDrafts, useProjects } from "../api/hooks";
import { COMPARE_OPEN_MAX, openOnCompare } from "../drafts";
import { useLanguage } from "../language";
import { readCompareHandoff } from "../route";
import { DraftCard } from "./DraftCandidates";

// 并排比几稿（第三档，[ADR 0022](docs/adr/0022-drafting-is-a-proposal-not-a-write.md)）。
//
// **它是同一个应用的另一条路由，不是另一个东西**：工作台本来就是本地浏览器应用
// （`nh serve` 开的就是 localhost），所以「在新标签页里并排读三稿」= `#/compare/12`。
// 零新基础设施——没有新服务、没有新端口、服务端一个路径都不用多认（见 `route.ts`）。
//
// ── 这一页不做的两件事 ────────────────────────────────────────────────────
//
// 1. **不挑、不排名、不打分**（ADR 0005）。列的顺序是后端给的（最近的在前），
//    「已经写进这一章」那个徽标是助手做过的一个动作，不是这一页的评价。
// 2. **不改任何东西。** 这儿只读：要用哪一版，回工作台跟写作助手说——
//    一个功能不留两个入口，而「把哪一稿写进书」是助手的动作（ADR 0021 / 0022）。

/** 这一页读的是哪本书。
 *
 *  **地址里只有章号**（内部标识不进地址栏，同「不上屏」那条），所以书是开链接那一下
 *  留在本地存储里的。留不下 / 过期 / 换了一章时**不猜**——除非这个库里只有一本书，
 *  那时没有可猜的（工作台自己也是这么开的：只有一本就打开它）。
 *
 *  猜错的代价不对称：猜对了省作者一次点击，猜错了他会对着**另一本书**的稿子做决定。 */
function useCompareBook(chapter: number): {
  book: string | null;
  name: string;
  pending: boolean;
  offline: boolean;
} {
  const projects = useProjects();
  // 挂载时读一次就够：本地存储不会自己变，而每次渲染都读一遍会让「另一个标签页刚点了
  // 另一章」在这一页上半途换书。
  const handoff = useMemo(() => readCompareHandoff(), []);
  const list = projects.data ?? [];
  const only = list.length === 1 ? list[0].id : null;
  const book = handoff && handoff.chapter === chapter ? handoff.book : only;
  return {
    book,
    name: list.find((p) => p.id === book)?.name ?? "",
    pending: projects.isPending,
    // **「连不上」和「不知道是哪本书」不是同一句话**：前者作者该去看工作台那个
    // 标签页还开着没有，后者他该回去重新点一次那条链接。合成一句就有一半的人
    // 会去做那件没用的事。
    offline: projects.isError,
  };
}

export function DraftCompare({ chapter }: { chapter: number }) {
  const language = useLanguage((s) => s.language);
  const { book, name, pending, offline } = useCompareBook(chapter);
  const drafts = useDrafts(book, chapter);
  const rows = drafts.data?.drafts ?? [];
  const [open, setOpen] = useState<string[]>([]);

  // 拿到那一批才知道摊开哪几列（`openOnCompare`）。**依赖的是那一份响应本身**，
  // 不是 `rows`（后者每次渲染都是新数组，会把作者手动展开过的那几列一直收回去）。
  useEffect(() => setOpen(openOnCompare(drafts.data?.drafts ?? [])), [drafts.data]);

  const chapterLabel = language === "zh" ? `第 ${chapter} 章` : `Chapter ${chapter}`;
  const title = name ? `${name} · ${chapterLabel}` : chapterLabel;

  return (
    <div className="compare-page">
      <header className="compare-head">
        <b>
          {title} · {language === "zh" ? "稿件并排对比" : "Compare drafts side by side"}
        </b>
        <span className="spacer" />
        {/* 这一页只读。**说出来**：不说的话，作者会在这儿找那颗「就用这一版」的按钮。 */}
        <span className="compare-note">
          {language === "zh"
            ? "本页仅供阅读。选用某一版，请回到工作台告知写作助手。"
            : "This page is read-only. To use a version, return to the workbench and tell the writing assistant."}
        </span>
      </header>

      {(pending || (book && drafts.isPending)) && (
        <p className="compare-say">{language === "zh" ? "加载中…" : "Loading…"}</p>
      )}

      {offline && (
        <p className="compare-say">
          {language === "zh"
            ? "无法连接工作台服务。请确认工作台仍在运行，再刷新本页。"
            : "The workbench service cannot be reached. Check that the workbench is still running, then refresh this page."}
        </p>
      )}

      {!pending && !offline && !book && (
        <p className="compare-say">
          {language === "zh" ? (
            <>
              此链接未指明第 {chapter} 章属于哪本书。请回到工作台，在写作助手处重新点击
              「并排查看」。
            </>
          ) : (
            <>
              This link does not say which book chapter {chapter} belongs to. Return to the
              workbench and click “Compare side by side” again from the writing assistant.
            </>
          )}
        </p>
      )}

      {book && drafts.isError && (
        <p className="compare-say">
          {language === "zh"
            ? "稿件读取失败，请刷新后重试。"
            : "This chapter’s drafts could not be loaded; refresh to try again."}
        </p>
      )}

      {book && drafts.data && rows.length === 0 && (
        <p className="compare-say">
          {language === "zh" ? (
            <>第 {chapter} 章尚无稿件。由写作助手起草后，可在此对比。</>
          ) : (
            <>
              Chapter {chapter} has no drafts yet. Once the writing assistant drafts one, it can be
              compared here.
            </>
          )}
        </p>
      )}

      {rows.length > COMPARE_OPEN_MAX && (
        <p className="compare-say dim">
          {language === "zh" ? (
            <>
              本章共 {rows.length} 稿，默认展开最近的 {COMPARE_OPEN_MAX} 份；更早的稿件已收起，
              点击「展开」查看。
            </>
          ) : (
            <>
              This chapter has {rows.length} draft{rows.length === 1 ? "" : "s"}; the most recent{" "}
              {COMPARE_OPEN_MAX} are expanded. Older ones are collapsed — click “Expand” to read
              them.
            </>
          )}
        </p>
      )}

      {rows.length > 0 && book && (
        <div className="compare-cols">
          {rows.map((draft) => (
            <DraftCard
              key={draft.id}
              pid={book}
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
          ))}
        </div>
      )}
    </div>
  );
}
