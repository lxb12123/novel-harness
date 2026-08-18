import { useState } from "react";
import { useRoster } from "../api/hooks";
import { LABEL_ZH } from "../api/types";
import { useCoords } from "../store";
import { CharacterBasicInfo } from "./CharacterBasicInfo";
import { RosterDrawer } from "./RosterDrawer";

// 花名册：右栏的第一格，也是默认那一格。
//
// 它原先在左栏，和章目录挤在同一条 210px 里；书架进来之后那一栏是「书 → 章」的纵深，
// 而花名册问的是「这本书里有谁」——和右栏其余几格（这个人知道什么 / 在哪 / 和谁有关系）
// 是同一个问题的不同切面，所以它属于右边。
//
// **点一个人 = 看他的局部关系图**（`focusNode` 会把面板切到关系那一格）。这条没变。

export function RosterTab() {
  const { projectId, selectedNodeId, focusNode } = useCoords();
  const roster = useRoster(projectId);
  const [adding, setAdding] = useState(false);

  const groups: Record<string, { id: string; name: string }[]> = {};
  (roster.data ?? []).forEach((n) => (groups[n.label] ??= []).push(n));
  const empty = Object.keys(groups).length === 0;
  // 选中的这个人如果是人物，就在花名册顶部给他一行「本名 + 别名 chips」——
  // 这是人物基础信息的唯一形态（Task 15 / §4.5）：点名字本人聚焦关系图不变，
  // 别名编辑不另占一颗按钮。
  const selected = (roster.data ?? []).find((n) => n.id === selectedNodeId);
  const isCharacter = selected?.label === "character";

  return (
    <div>
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
        <>
          {isCharacter && selectedNodeId && (
            <CharacterBasicInfo characterId={selectedNodeId} />
          )}
          {Object.keys(groups)
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
            ))}
        </>
      )}

      {adding && projectId && <RosterDrawer pid={projectId} onClose={() => setAdding(false)} />}
    </div>
  );
}
