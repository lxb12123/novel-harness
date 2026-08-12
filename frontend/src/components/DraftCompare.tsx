import { useEffect, useMemo, useState } from "react";
import { useDrafts, useProjects } from "../api/hooks";
import { COMPARE_OPEN_MAX, openOnCompare } from "../drafts";
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
  const { book, name, pending, offline } = useCompareBook(chapter);
  const drafts = useDrafts(book, chapter);
  const rows = drafts.data?.drafts ?? [];
  const [open, setOpen] = useState<string[]>([]);

  // 拿到那一批才知道摊开哪几列（`openOnCompare`）。**依赖的是那一份响应本身**，
  // 不是 `rows`（后者每次渲染都是新数组，会把作者手动展开过的那几列一直收回去）。
  useEffect(() => setOpen(openOnCompare(drafts.data?.drafts ?? [])), [drafts.data]);

  const title = name ? `${name} · 第 ${chapter} 章` : `第 ${chapter} 章`;

  return (
    <div className="compare-page">
      <header className="compare-head">
        <b>{title} · 并排比几稿</b>
        <span className="spacer" />
        {/* 这一页只读。**说出来**：不说的话，作者会在这儿找那颗「就用这一版」的按钮。 */}
        <span className="compare-note">
          这一页只能读。要用哪一版，回工作台跟写作助手说。
        </span>
      </header>

      {(pending || (book && drafts.isPending)) && <p className="compare-say">加载中…</p>}

      {offline && (
        <p className="compare-say">
          这会儿连不上工作台的服务。回工作台那个标签页看看它还开着没有，再刷新这一页。
        </p>
      )}

      {!pending && !offline && !book && (
        <p className="compare-say">
          这个链接没说清是哪本书的第 {chapter} 章。回工作台，在写作助手那儿再点一次
          「并排比」——那一下会把书一起带过来。
        </p>
      )}

      {book && drafts.isError && (
        <p className="compare-say">这一章的稿子没读出来。刷新一下再看，它们还在。</p>
      )}

      {book && drafts.data && rows.length === 0 && (
        <p className="compare-say">
          第 {chapter} 章这会儿桌上没有稿子。让写作助手写一稿，这一页就有东西了。
        </p>
      )}

      {rows.length > COMPARE_OPEN_MAX && (
        <p className="compare-say dim">
          这一章桌上一共 {rows.length} 稿，先摊开最近的 {COMPARE_OPEN_MAX} 份；
          更早的那几份收着，点「展开」就读得到。
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
