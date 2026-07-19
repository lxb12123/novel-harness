import { useChapters, useRoster } from "../api/hooks";
import { useCoords } from "../store";

const LABEL_ZH: Record<string, string> = {
  Character: "人物",
  Location: "地点",
  Faction: "势力",
  Object: "物品",
  Secret: "秘密",
  Foreshadow: "伏笔",
};

export function LeftRail({ onOpenChapter }: { onOpenChapter: (n: number) => void }) {
  const { projectId, chapter } = useCoords();
  const chapters = useChapters(projectId);
  const roster = useRoster(projectId);

  const groups: Record<string, { id: string; name: string }[]> = {};
  (roster.data ?? []).forEach((n) => (groups[n.label] ??= []).push(n));

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
        <div className="empty">还没有章节（先 nh import 一本书）</div>
      )}

      <h2>花名册</h2>
      {Object.keys(groups).length ? (
        Object.keys(groups)
          .sort()
          .map((lab) => (
            <div className="grp" key={lab}>
              <div className="lab">{LABEL_ZH[lab] ?? lab}</div>
              {groups[lab].map((n) => (
                <div className="item" key={n.id}>
                  {n.name}
                </div>
              ))}
            </div>
          ))
      ) : (
        <div className="empty">空</div>
      )}
    </section>
  );
}
