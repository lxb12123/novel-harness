import { useState } from "react";
import { useChapters, useRoster } from "../api/hooks";
import { LABEL_ZH } from "../api/types";
import { useCoords } from "../store";
import { RosterDrawer } from "./RosterDrawer";

export function LeftRail({ onOpenChapter }: { onOpenChapter: (n: number) => void }) {
  const { projectId, chapter, selectedNodeId, focusNode } = useCoords();
  const chapters = useChapters(projectId);
  const roster = useRoster(projectId);
  const [adding, setAdding] = useState(false);

  const groups: Record<string, { id: string; name: string }[]> = {};
  (roster.data ?? []).forEach((n) => (groups[n.label] ??= []).push(n));
  const empty = Object.keys(groups).length === 0;

  return (
    <section className="pane">
      <h2>章目录</h2>
      {chapters.data?.length ? (
        chapters.data.map((c) => (
          <div
            key={c.number}
            className={"ch" + (c.number === chapter ? " on" : "")}
            onClick={() => onOpenChapter(c.number)}
          >
            <span className="n">{String(c.number).padStart(3, "0")}</span>
            {c.title || "（无题）"}
          </div>
        ))
      ) : (
        <div className="empty">还没有章节（顶栏「导入」选一个 TXT）</div>
      )}

      <h2>
        花名册
        {projectId && (
          <button className="add" onClick={() => setAdding(true)} title="建人物 / 地点 / 秘密…">
            ＋
          </button>
        )}
      </h2>

      {empty ? (
        <div className="empty">
          还没有人物或设定。
          <a onClick={() => projectId && setAdding(true)}>添加第一个条目</a>
        </div>
      ) : (
        Object.keys(groups)
          .sort()
          .map((lab) => (
            <div className="grp" key={lab}>
              <div className="lab">{LABEL_ZH[lab as keyof typeof LABEL_ZH] ?? lab}</div>
              {groups[lab].map((n) => (
                <div
                  className={"item" + (n.id === selectedNodeId ? " on" : "")}
                  key={n.id}
                  onClick={() => focusNode(n.id)}
                  title="看它的局部关系图"
                >
                  {n.name}
                </div>
              ))}
            </div>
          ))
      )}

      {adding && projectId && <RosterDrawer pid={projectId} onClose={() => setAdding(false)} />}
    </section>
  );
}
