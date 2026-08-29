import { useMemo, useState } from "react";
import {
  useCharacterEvents,
  useDeleteNode,
  useProjects,
  useRenameNode,
  useRoster,
} from "../api/hooks";
import type { RosterEntry } from "../api/types";
import { nodeLabelText } from "../backendMessages";
import { readCorrectionError } from "../correctionError";
import { useLanguage } from "../language";
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

/** 组内排序：**累计信息量优先，出场章数兜底，名字保底。**
 *
 *  ── 为什么是三级，而不是给作者一个「按哪个排」的下拉 ────────────────────
 *
 *  两个数各自会在一整类书上恒为 0：
 *
 *  - `information_score` 要等**带画像的抽取**跑过（刚导进来的书全 0）；
 *  - `appearance_chapters` 要等**总结**落地（没生成过总结的书全 0）。
 *
 *  给一个下拉的话，作者会撞上「换了个排法，一列全是 0，看起来像坏了」。
 *  三级排序自己就退化得对：分数分不出高下时按出场章数，两个都分不出时按名字。
 *  **多一颗下拉不如少一种「看起来坏了」的样子。**
 *
 *  名字那一级不是装饰：没有它，同为 0 的那一大批每次渲染的顺序都不一样。 */
function bySignal(rows: RosterEntry[], order: Order): RosterEntry[] {
  const sign = order === "desc" ? -1 : 1;
  return [...rows].sort(
    (a, b) =>
      sign * (a.information_score - b.information_score) ||
      sign * (a.appearance_chapters - b.appearance_chapters) ||
      a.name.localeCompare(b.name, "zh"),
  );
}

/** 这个人的事件时间线 —— **事件是比较小的一条总结，挂在跟它相关的每个人下面。**
 *
 *  一件事跟三个人相关，这三个人的线上各出现一次（存储那一侧本来就是多对多）。
 *  后端全给 + 每条带章号，**切片是这一层的事**——今天不切，整条摊开。
 *
 *  ── 空态说清楚是哪一种空 ────────────────────────────────────────────────
 *
 *  这一格今天在真书上**必然是空的**：作者那本 158 章的书里事件 0 条，
 *  第一次真抽取跑完才会有。所以空态不许写「暂无数据」——那句话既不告诉作者
 *  发生了什么，也不告诉他下一步。这里分两种说：整本书还没整理过 vs
 *  整理过但这个人身上没落下事。 */
function CharacterTimeline({ characterId, name }: { characterId: string; name: string }) {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const events = useCharacterEvents(projectId, characterId);
  const rows = events.data ?? [];

  return (
    <div className="grp">
      <div className="lab">{language === "zh" ? `${name}的事件` : `${name}'s events`}</div>
      {events.isLoading && (
        <span className="empty">{language === "zh" ? "读取中…" : "Loading…"}</span>
      )}
      {!events.isLoading && rows.length === 0 && (
        <span className="empty">
          {language === "zh" ? (
            <>
              还没有跟{name}有关的事件。事件是系统整理正文时记下的一条条小结 ——
              这一章还没整理过、或者整理了但没有落到{name}身上。
            </>
          ) : (
            <>
              No events involving {name} yet. Events are short notes the system records while it
              reads through the text — either this chapter hasn’t been processed yet, or it has
              been but nothing landed on {name}.
            </>
          )}
        </span>
      )}
      {rows.map((row) => {
        // 「还有：…」= 这件事上**除他之外**的人。名单去重（一个人可能既在场又知情），
        // 顺序按后端给的来（这一层不发明第二个序）。
        const others = [...row.participants, ...row.knowers]
          .filter((n) => n.id !== characterId)
          .filter((n, i, all) => all.findIndex((m) => m.id === n.id) === i);
        return (
          <div className="item" key={row.event_id}>
            <span className="nm">
              {language === "zh" ? `第 ${row.chapter_number} 章` : `Chapter ${row.chapter_number}`} · {row.summary}
            </span>
            {others.length > 0 && (
              <span className="dim">
                {language === "zh" ? "还有：" : "Also: "}
                {others.map((n) => n.name).join(language === "zh" ? "、" : ", ")}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function RosterTab() {
  const { projectId, selectedNodeId, focusNode } = useCoords();
  const language = useLanguage((s) => s.language);
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
        {language === "zh" ? "花名册" : "Roster"}
        {!empty && (
          <button
            className="add"
            onClick={() => setOrder(order === "desc" ? "asc" : "desc")}
            title={
              language === "zh"
                ? order === "desc" ? "改成写得少的排前面" : "改成写得多的排前面"
                : order === "desc" ? "Sort least-written first" : "Sort most-written first"
            }
          >
            {language === "zh"
              ? order === "desc" ? "写得多 → 少" : "写得少 → 多"
              : order === "desc" ? "Most → Least" : "Least → Most"}
          </button>
        )}
        {projectId && (
          <button
            className="add"
            onClick={() => setAdding(true)}
            title={language === "zh" ? "建人物 / 地点 / 势力…" : "Add a character / location / faction…"}
          >
            ＋
          </button>
        )}
      </h2>

      {empty ? (
        <div className="empty">
          {language === "zh" ? "还没有人物或设定。" : "No characters or settings yet. "}
          <a onClick={() => projectId && setAdding(true)}>
            {language === "zh" ? "添加第一个条目" : "Add the first entry"}
          </a>
        </div>
      ) : (
        <>
          {isCharacter && selectedNodeId && (
            <>
              <CharacterBasicInfo characterId={selectedNodeId} />
              <CharacterTimeline characterId={selectedNodeId} name={selected!.name} />
            </>
          )}
          {Object.keys(groups)
            .sort()
            .map((lab) => (
              <div className="grp" key={lab}>
                <div className="lab">{nodeLabelText(lab, language)}</div>
                {bySignal(groups[lab], order).map((n) => (
                  <div
                    className={"item" + (n.id === selectedNodeId ? " on" : "")}
                    key={n.id}
                  >
                    {renaming === n.id ? (
                      <input
                        autoFocus
                        value={draft}
                        placeholder={language === "zh" ? "输入新的名称" : "Enter a new name"}
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
                          title={language === "zh" ? "看它的局部关系图" : "View their local relationship graph"}
                        >
                          {n.name}
                        </span>
                        {/* 「出现在 N 章的总结里」而不是「出场 N 章」：这一层数的是总结，
                            不是正文。一本还没生成总结的书这一列全是 0，把它写成
                            「没出场」就是拿一个空表当结论（§10 约束 8）。 */}
                        <span
                          className="dim"
                          title={language === "zh" ? "出现在几章的总结里" : "Number of chapter summaries mentioning this"}
                        >
                          {language === "zh"
                            ? `${n.appearance_chapters} 章`
                            : `${n.appearance_chapters} ${n.appearance_chapters === 1 ? "chapter" : "chapters"}`}
                        </span>
                        <button
                          className="add"
                          title={language === "zh" ? "改名" : "Rename"}
                          onClick={() => {
                            setDraft(n.name);
                            setRenaming(n.id);
                          }}
                        >
                          {language === "zh" ? "改名" : "Rename"}
                        </button>
                        <button
                          className="add"
                          title={language === "zh" ? "从花名册里删掉" : "Remove from the roster"}
                          onClick={() => setConfirming(n.id)}
                        >
                          {language === "zh" ? "删" : "Delete"}
                        </button>
                      </>
                    )}

                    {confirming === n.id && (
                      <div className="row">
                        {/* **删是不可逆的**，所以问一句。2026-08-28 起删除不再因为
                            挂着关系/情节而拒绝——它参与过的事件会跟着一起消失，
                            剩下的人的事件时间线上会冒出红点（`cast_changed`），
                            那才是「事后可见可改」的落点，不在这句确认话里预警。 */}
                        <span>
                          {language === "zh" ? (
                            <>把「{n.name}」和它的所有称呼一起删掉？删了拿不回来。</>
                          ) : (
                            <>Delete "{n.name}" and all its aliases? This can’t be undone.</>
                          )}
                        </span>
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
                          {language === "zh"
                            ? remove.isPending ? "删除中…" : "删掉"
                            : remove.isPending ? "Deleting…" : "Delete"}
                        </button>
                        <button onClick={() => setConfirming(null)}>
                          {language === "zh" ? "算了" : "Cancel"}
                        </button>
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
