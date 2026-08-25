import { useMemo, useState } from "react";
import { useDeleteNode, useProjects, useRenameNode, useRoster } from "../api/hooks";
import { LABEL_ZH, type RosterEntry } from "../api/types";
import { readCorrectionError } from "../correctionError";
import { useCoords } from "../store";
import { CharacterBasicInfo } from "./CharacterBasicInfo";
import { RosterDrawer } from "./RosterDrawer";

// 花名册：右栏的第一格，也是默认那一格。
//
// 它原先在左栏，和章目录挤在同一条 210px 里；书架进来之后那一栏是「书 → 章」的纵深，
// 而花名册问的是「这本书里有谁」——和右栏其余几格（这个人在哪 / 和谁有关系）是同一个
// 问题的不同切面，所以它属于右边。
//
// **点一个人 = 看他的局部关系图**（`focusNode` 会把面板切到关系那一格）。这条没变。
//
// ── 2026-08-25 这一格多了三样，三样都是同一条裁定的产物 ────────────────────
//
// 抽取从这一天起**认不出就建**（ADR 0020 补记）。于是：
//
// 1. **出场章数 + 组内按它降序** —— 自动建出来的一次性称呼（「服务员」）会大量涌进来，
//    按名字排的话主角和路人混在一起。作者要的是「谁重要」，那个序就是出场频率。
// 2. **倒序切换** —— 反过来看正是「哪些是垃圾」：出场 0～1 章的那一批。
// 3. **每行能删** —— 模型会认错（真书上的「袭人」，满篇「寒气袭人」）。
//    **自动建 + 不能删 = 单向阀**，那个错会永远往上下文里塞噪声。

/** 后端那句话原样摆出来，**一个字不改**。
 *
 *  删除被拒时后端写的是「「北荒」还被引用着（关系 1 / 情节 0），先把那几条改掉再删他」
 *  ——那是给作者的话，不是给代码的码（`api/characters.py` 那两条路由）。
 *  在这儿写一句「删不掉这个条目」把它盖掉，就是又造出第二个措辞源，
 *  而那正是 `correctionError.ts` 顶上那段注释在讲的事。 */
function Refusal({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="err-box">{readCorrectionError(error).message}</div>;
}

/** 这一格的排序方向。**存在组件里不进 URL**：它是「我现在想怎么看」，不是坐标。 */
type Order = "desc" | "asc";

function byCount(rows: RosterEntry[], order: Order): RosterEntry[] {
  const sign = order === "desc" ? -1 : 1;
  // 次数相同的按名字排 —— 没有这一项，同为 0 的那一大批每次渲染的顺序都不一样。
  return [...rows].sort(
    (a, b) =>
      sign * (a.appearance_chapters - b.appearance_chapters) ||
      a.name.localeCompare(b.name, "zh"),
  );
}

export function RosterTab() {
  const { projectId, selectedNodeId, focusNode } = useCoords();
  const roster = useRoster(projectId);
  const projects = useProjects();
  const [adding, setAdding] = useState(false);
  const [order, setOrder] = useState<Order>("desc");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const rename = useRenameNode(projectId ?? "");
  const remove = useDeleteNode(projectId ?? "");

  const rows = useMemo(() => (roster.data ?? []) as RosterEntry[], [roster.data]);
  const groups: Record<string, RosterEntry[]> = {};
  rows.forEach((n) => (groups[n.label] ??= []).push(n));
  const empty = Object.keys(groups).length === 0;

  // 选中的这个人如果是人物，就在花名册顶部给他一行「本名 + 别名 chips」——
  // 这是人物基础信息的唯一形态（Task 15 / §4.5）：点名字本人聚焦关系图不变，
  // 别名编辑不另占一颗按钮。
  //
  // ⚠️ 这一行 2026-08-25 之前比的是 `=== "character"`（小写），而 `NodeLabel` 是
  // `"Character"`——**它恒为 false，那一格一次都没画出来过**。花名册出参从
  // `{id,label,name}` 收窄成 `RosterEntry` 的那一刻 `tsc` 就把它指出来了
  // （从前 label 是 `string`，两个字符串比大小写不同不是类型错误）。
  const selected = rows.find((n) => n.id === selectedNodeId);
  const isCharacter = selected?.label === "Character";

  // 版本从**它正在渲染的那份项目出参**上取（同 `CanonEventCast` 那条注释）：
  // 作者一保存，后台整理那一章、干净结果直接升 CANON，版本就涨了一格。
  const version = projects.data?.find((p) => p.id === projectId)?.canon_version;

  function submitRename(id: string) {
    const name = draft.trim();
    if (!name || version === undefined) return;
    rename.mutate(
      { id, name, expected_canon_version: version },
      { onSuccess: () => setRenaming(null) },
    );
  }

  return (
    <div>
      <h2>
        花名册
        {!empty && (
          <button
            className="add"
            onClick={() => setOrder(order === "desc" ? "asc" : "desc")}
            title={order === "desc" ? "改成出场少的排前面" : "改成出场多的排前面"}
          >
            {order === "desc" ? "出场多 → 少" : "出场少 → 多"}
          </button>
        )}
        {projectId && (
          <button className="add" onClick={() => setAdding(true)} title="建人物 / 地点 / 势力…">
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
                {byCount(groups[lab], order).map((n) => (
                  <div
                    className={"item" + (n.id === selectedNodeId ? " on" : "")}
                    key={n.id}
                  >
                    {renaming === n.id ? (
                      <input
                        autoFocus
                        value={draft}
                        placeholder="输入新的名称"
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") submitRename(n.id);
                          if (e.key === "Escape") setRenaming(null);
                        }}
                        onBlur={() => setRenaming(null)}
                      />
                    ) : (
                      <>
                        <span
                          className="nm"
                          onClick={() => focusNode(n.id)}
                          title="看它的局部关系图"
                        >
                          {n.name}
                        </span>
                        {/* 「出现在 N 章的总结里」而不是「出场 N 章」：这一层数的是总结，
                            不是正文。一本还没生成总结的书这一列全是 0，把它写成
                            「没出场」就是拿一个空表当结论（§10 约束 8）。 */}
                        <span className="dim" title="出现在几章的总结里">
                          {n.appearance_chapters} 章
                        </span>
                        <button
                          className="add"
                          title="改名"
                          onClick={() => {
                            setDraft(n.name);
                            setRenaming(n.id);
                          }}
                        >
                          改名
                        </button>
                        <button
                          className="add"
                          title="从花名册里删掉"
                          onClick={() => setConfirming(n.id)}
                        >
                          删
                        </button>
                      </>
                    )}

                    {confirming === n.id && (
                      <div className="row">
                        {/* **删是不可逆的**，所以问一句。措辞里说清删掉之后没了什么：
                            这个条目和它的称呼。它参与过的关系和情节不会被删——后端
                            那一侧根本不允许（`NodeUsage` 非零就拒绝）。 */}
                        <span>把「{n.name}」和它的所有称呼一起删掉？删了拿不回来。</span>
                        <button
                          disabled={remove.isPending || version === undefined}
                          onClick={() =>
                            version !== undefined &&
                            remove.mutate(
                              { id: n.id, expected_canon_version: version },
                              { onSuccess: () => setConfirming(null) },
                            )
                          }
                        >
                          {remove.isPending ? "删除中…" : "删掉"}
                        </button>
                        <button onClick={() => setConfirming(null)}>算了</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          <Refusal error={remove.error} />
          <Refusal error={rename.error} />
        </>
      )}

      {adding && projectId && <RosterDrawer pid={projectId} onClose={() => setAdding(false)} />}
    </div>
  );
}
