import { useCharacterState, useRoster } from "../api/hooks";
import type { Edge } from "../api/types";
import { edgeLabelText } from "../backendMessages";
import { useLanguage } from "../language";
import { useCoords } from "../store";

// 底栏时间线（§2.2）：选中节点的边闭开区间。用现成端点，零新引擎。
//
// ⚠️ **左半那一列（「场景顺序」）2026-08-14 删了**，连同场景块、两条路由和 R4
// （[ADR 0027](docs/adr/0027-scene-blocks-cut.md)）：它画的是作者要在正文里手写的
// `## 场景 N`，真书上永远是零条。于是这条底栏现在只有一列。
//
// 说明（诚实边界）：右半画的是**当前有效**的边（valid_to=null → 到「现在这一章」）。
// 被 supersede 的历史区间（valid_to 已写）不在 character_state 的这一章快照里——要画出
// 「青云城[88,150) 然后 北荒[150,∞)」那种收口，得有一个「取节点全历史边」的 reader，
// 那是后续。现在这条时间线回答的是「这些事从第几章起一直成立到现在」。

// 关系类型 → 作者的说法那张表在 `backendMessages.ts::EDGE_LABEL`（全前端一份）。
// 这儿原来有一份 7 行的拷贝，兜底写的是 `?? e.type`——`PLANTED_IN` / `RESOLVED_IN`
// 一旦被写出来，屏幕上就是四个大写字母。

function IntervalBars() {
  const { projectId, chapter, selectedNodeId } = useCoords();
  const language = useLanguage((s) => s.language);
  const { data } = useCharacterState(projectId, selectedNodeId, chapter);
  const roster = useRoster(projectId);
  if (!selectedNodeId)
    return (
      <div className="tl-empty">
        {language === "zh" ? "选择一个人物查看相关变化" : "Select a character to see related changes"}
      </div>
    );
  const edges = (data?.edges ?? []).filter((e) => e.type !== "RELATED_TO"); // 无向边不画区间
  const name = data?.node.name ?? "";
  if (edges.length === 0)
    return (
      <div className="tl-empty">
        {language === "zh" ? `${name} 暂无可显示的变化` : `${name} has no visible changes yet`}
      </div>
    );

  // 角色册里查不到就说「—」，**绝不 `?? id`**：角色册和这份快照是两条独立缓存，
  // 后台整理刚建出来的节点会在前者里缺席一拍，而那一拍上作者看到的会是
  // `location:01J8XK…`。同 `ProposalReviewTab` 上修掉的那条 `id.slice(-6)`。
  const nameOf = (id: string) => roster.data?.find((n) => n.id === id)?.name ?? "—";
  const minFrom = Math.min(...edges.map((e) => e.valid_from_chapter));
  const span = Math.max(1, chapter - minFrom);

  const bar = (e: Edge) => {
    const end = e.valid_to_chapter ?? chapter;
    const left = ((e.valid_from_chapter - minFrom) / span) * 100;
    const width = Math.max(4, ((end - e.valid_from_chapter) / span) * 100);
    return { left: `${left}%`, width: `${width}%` };
  };

  const chapterLabel = (n: number) => (language === "zh" ? `第 ${n} 章` : `Chapter ${n}`);
  const intervalTitle = (from: number, to: number | null) => {
    if (language === "zh") return `从第 ${from} 章${to ? `到第 ${to} 章前` : "起一直有效"}`;
    return to ? `From chapter ${from} to just before chapter ${to}` : `From chapter ${from} onward`;
  };

  return (
    <div className="tl-intervals">
      <div className="tl-axis">
        <span>{chapterLabel(minFrom)}</span>
        <span className="tl-node">
          {language === "zh" ? `${data?.node.name} 的变化` : `${data?.node.name}'s changes`}
        </span>
        <span>{language === "zh" ? `${chapterLabel(chapter)}（当前）` : `${chapterLabel(chapter)} (current)`}</span>
      </div>
      {edges.map((e) => (
        <div className="tl-row" key={e.id}>
          <span className="tl-label">
            {edgeLabelText(e.type, language)} {nameOf(e.dst)}
          </span>
          <span className="tl-track">
            <span
              className="tl-bar"
              style={bar(e)}
              title={intervalTitle(e.valid_from_chapter, e.valid_to_chapter)}
            >
              {chapterLabel(e.valid_from_chapter)}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

export function BottomBar() {
  const { selectedNodeId } = useCoords();
  const language = useLanguage((s) => s.language);
  // 没挑人 = 这条底栏一个像素都不占。它只有一列了，那一列没内容时整条就没有理由存在。
  if (!selectedNodeId) return null;

  return (
    <footer className="bottombar">
      <div className="tl-col tl-col-intervals">
        <div className="tl-title">
          {language === "zh" ? "人物与设定变化" : "Character & Setting Changes"}
        </div>
        <IntervalBars />
      </div>
    </footer>
  );
}
