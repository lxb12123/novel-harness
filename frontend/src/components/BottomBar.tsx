import { useCharacterState, useRoster, useScenes } from "../api/hooks";
import { useCoords } from "../store";
import type { Edge } from "../api/types";

// 底栏时间线（§2.2）：左半 = 本章场景序（parse_scenes），右半 = 选中节点的边闭开区间。
// 两半都用现成端点，零新引擎。
//
// 说明（诚实边界）：右半画的是**当前有效**的边（valid_to=null → 到「现在这一章」）。
// 被 supersede 的历史区间（valid_to 已写）不在 character_state 的这一章快照里——要画出
// 「青云城[88,150) 然后 北荒[150,∞)」那种收口，得有一个「取节点全历史边」的 reader，
// 那是后续。现在这条时间线回答的是「这些事从第几章起一直成立到现在」。

const TYPE_ZH: Record<string, string> = {
  KNOWS: "知道",
  BELIEVES: "以为",
  LOCATED_AT: "在",
  HAS_STATE: "状态",
  MEMBER_OF: "属于",
  OWNS: "持有",
  RELATED_TO: "关系",
};

function SceneStrip() {
  const { projectId, chapter, cast, setCast } = useCoords();
  const { data: scenes } = useScenes(projectId, chapter);
  if (!scenes || scenes.length === 0)
    return <div className="tl-empty">第 {chapter} 章没有场景块</div>;
  return (
    <div className="tl-scenes">
      {scenes.map((s, i) => (
        <span key={s.number} className="tl-scene-wrap">
          <button
            className={"tl-scene" + (cast === s.cast.join(",") && cast ? " on" : "")}
            onClick={() => setCast(s.cast.join(","))}
            title="用这一场的在场刷新面板"
          >
            <b>场景 {s.number}</b>
            {s.cast.length > 0 && <span> · {s.cast.join(",")}</span>}
            {s.loc && <span className="tl-loc"> @{s.loc}</span>}
          </button>
          {i < scenes.length - 1 && <span className="tl-arrow">→</span>}
        </span>
      ))}
    </div>
  );
}

function IntervalBars() {
  const { projectId, chapter, selectedNodeId } = useCoords();
  const { data } = useCharacterState(projectId, selectedNodeId, chapter);
  const roster = useRoster(projectId);
  if (!selectedNodeId) return <div className="tl-empty">点一个人看他的时态区间</div>;
  const edges = (data?.edges ?? []).filter((e) => e.type !== "RELATED_TO"); // 无向边不画区间
  if (edges.length === 0) return <div className="tl-empty">{data?.node.name ?? ""} 无有效边</div>;

  const nameOf = (id: string) => roster.data?.find((n) => n.id === id)?.name ?? id;
  const minFrom = Math.min(...edges.map((e) => e.valid_from_chapter));
  const span = Math.max(1, chapter - minFrom);

  const bar = (e: Edge) => {
    const end = e.valid_to_chapter ?? chapter;
    const left = ((e.valid_from_chapter - minFrom) / span) * 100;
    const width = Math.max(4, ((end - e.valid_from_chapter) / span) * 100);
    return { left: `${left}%`, width: `${width}%` };
  };

  return (
    <div className="tl-intervals">
      <div className="tl-axis">
        <span>ch{minFrom}</span>
        <span className="tl-node">{data?.node.name} 的时态区间</span>
        <span>ch{chapter}（现在）</span>
      </div>
      {edges.map((e) => (
        <div className="tl-row" key={e.id}>
          <span className="tl-label">
            {TYPE_ZH[e.type] ?? e.type} {nameOf(e.dst)}
          </span>
          <span className="tl-track">
            <span className="tl-bar" style={bar(e)} title={`[${e.valid_from_chapter}, ${e.valid_to_chapter ?? "现在"})`}>
              {e.valid_from_chapter}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

export function BottomBar() {
  const { projectId, chapter, selectedNodeId } = useCoords();
  const { data: scenes } = useScenes(projectId, chapter);
  if (!selectedNodeId && (!scenes || scenes.length === 0)) return null;

  return (
    <footer className="bottombar">
      <div className="tl-col tl-col-scenes">
        <div className="tl-title">场景序</div>
        <SceneStrip />
      </div>
      <div className="tl-col tl-col-intervals">
        <div className="tl-title">时态区间（自某章一直有效到现在）</div>
        <IntervalBars />
      </div>
    </footer>
  );
}
