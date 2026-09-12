import { useCharacterState, useEvidence, useRoster } from "../api/hooks";
import type { Edge } from "../api/types";
import { edgeLabelText } from "../backendMessages";
import { useLanguage } from "../language";
import { useCoords } from "../store";

// 角色卡里的「依据」——原「原文依据」tab（`EvidenceTab`）并进来（2026-08-31，
// 跟「人物状态」「人物关系」同一批）。**把「✓知道 ch88」还原成当年那句原文**，
// 明确不给分数（v1 没有向量，出任何 score 都是编的）。
//
// **不再单独请求**：`useCharacterState` 这一格已经因为状态/关系两块在拉了
// （同一个 queryKey，react-query 只发一次网）。这是并进来之后唯一真正省下的一次往返，
// 原来那个独立 tab 是自己拉一遍同一条 `/characters/{id}/state`。
//
// ── 2026-09-04：**只列「谁和谁」那一类，状态类整个不进来** ────────────────────
//
// 作者裁定（原话：「我现在上面都去掉那些地址了，就是不需要这个地址了，你下面应该
// 跟一下」，随后确认装备那类也撤）：所在地和状态维度在「状态」那一格已经摆过一遍，
// 在这儿再列一遍只会让人以为那是另一件事——关系图那边同一天撤掉地点节点是同一条理由。
//
// ⚠️ **代价：状态和所在地的引语从此在界面上查不到了。** 「状态」那一格照旧显示
// 「所在地：观音寺」「装备：持沧浪剑」，但它们是从哪句话读出来的没有入口。
// 真要核对，把 `STATE_LIKE` 那个集合改空就回来了——**别用别的方式重造一遍**。
//
// 另一件顺手记下的事实：`HAS_STATE` 的对端是状态维度节点，而维度**有意不进角色册**
// （`CANONICAL_ALIAS_LABELS` 排除 `StateDim`：维度名是「情绪」「境界」这类高频词，
// 进了角色册 `mentions.py` 拿它做字面匹配会把 mentions 冲垮）。所以哪天真要把状态
// 那一行放回来，**名字不能拿 `/roster` 查**（查出来恒为「—」，那一行为此印了很久的
// 「状态 —」），得从同一份出参的 `states[]` 里取维度名和值。
/** 状态类的边：所在地 + 状态维度。**这一格整类不列**（见文件顶注）。 */
const STATE_LIKE: ReadonlySet<string> = new Set(["LOCATED_AT", "HAS_STATE"]);

export function CharacterEvidence({ characterId }: { characterId: string }) {
  const { projectId, chapter } = useCoords();
  const language = useLanguage((s) => s.language);
  const state = useCharacterState(projectId, characterId, chapter);
  const roster = useRoster(projectId);

  // 查不到就说「—」，**绝不 `?? id`**：角色册和这份快照是两条独立缓存，后台整理刚
  // 建出来的节点会在前者里缺席一拍，那一拍上 `?? id` 摆出来的是一串内部编号。
  const rosterName = (id: string) => roster.data?.find((n) => n.id === id)?.name ?? "—";
  const evidenced = (state.data?.edges ?? []).filter(
    (e) => e.evidence_id && !STATE_LIKE.has(e.type),
  );

  return (
    <div className="grp">
      <div className="lab">{language === "zh" ? "依据" : "Evidence"}</div>
      {state.isFetching && !state.data && (
        <span className="empty">{language === "zh" ? "读取中…" : "Loading…"}</span>
      )}
      {state.data && evidenced.length === 0 && (
        <span className="empty">
          {/* **不许再指向「记录这句」**（2026-08-14 那颗按钮和整条选区工具条一起删了）：
              指着一个不存在的按钮，比不给下一步更糟——作者会以为是自己没找到。
              现在这一格的填充路径只有后台整理那一条（换章触发 → 右栏「待确认」）。 */}
          {language === "zh" ? (
            <>
              第 {chapter} 章尚无原文依据。翻到下一章时系统会整理本章，整理结果列入右栏「待确认」。
            </>
          ) : (
            <>
              No supporting passages in chapter {chapter} yet. Moving on to the next chapter
              triggers processing of this one; results appear in the "Pending" panel on the right.
            </>
          )}
        </span>
      )}
      {evidenced.map((e) => (
        <EvidenceRow key={e.id} pid={projectId!} edge={e} dstName={rosterName(e.dst)} />
      ))}
    </div>
  );
}

function EvidenceRow({ pid, edge, dstName }: { pid: string; edge: Edge; dstName: string }) {
  const { data, isFetching } = useEvidence(pid, edge.evidence_id ?? null);
  const language = useLanguage((s) => s.language);
  return (
    <div className="statecard">
      <div className="nm">
        {edgeLabelText(edge.type, language)} {dstName}
        <span className="dim" style={{ fontWeight: 400 }}>
          {language === "zh" ? ` · 第 ${edge.valid_from_chapter} 章起` : ` · Since chapter ${edge.valid_from_chapter}`}
        </span>
      </div>
      {isFetching && <div className="row">{language === "zh" ? "取原文中…" : "Fetching the passage…"}</div>}
      {data && (
        <div className="row">
          {language === "zh" ? (
            <>依据 第 {data.chapter_number} 章：「{data.quote_text}」</>
          ) : (
            <>From chapter {data.chapter_number}: "{data.quote_text}"</>
          )}
        </div>
      )}
    </div>
  );
}
