import { useCharacterState, useEvidence, useRoster } from "../api/hooks";
import type { Edge } from "../api/types";
import { edgeLabelText } from "../backendMessages";
import { useLanguage } from "../language";
import { useCoords } from "../store";

// Tab3 确定性证据 —— 把「✓知道 ch88」还原成当年那句原文。
// **明确不给分数**（v1 没有向量，出任何 score = 编的，§1.2）。声明产生的边带 evidence_id，
// 逐条取回「来源章 + 原文片段」。作者看到的不是「系统觉得」，是「你自己写过的那句」。

// 关系类型 → 作者的说法在 `backendMessages.ts::EDGE_LABEL`（全前端一份，兜底是
// 翻译过的话）。这儿原来那份拷贝只有 7 行且 `?? edge.type` 原样回吐。

export function EvidenceTab() {
  const { projectId, chapter, selectedNodeId } = useCoords();
  const language = useLanguage((s) => s.language);
  const state = useCharacterState(projectId, selectedNodeId, chapter);
  const roster = useRoster(projectId);

  if (!selectedNodeId)
    return (
      <div className="empty">
        {language === "zh"
          ? "从左侧选择一个条目，这里会显示与它相关的原文依据。"
          : "Select an entry on the left to see the passages that back it up."}
      </div>
    );

  // 查不到就说「—」，**绝不 `?? id`**（同 `BottomBar`：两条独立缓存差一拍）。
  const nameOf = (id: string) => roster.data?.find((n) => n.id === id)?.name ?? "—";
  const evidenced = (state.data?.edges ?? []).filter((e) => e.evidence_id);

  if (state.isFetching && !state.data)
    return <div className="empty">{language === "zh" ? "加载中…" : "Loading…"}</div>;
  if (evidenced.length === 0) {
    const name = state.data ? state.data.node.name : language === "zh" ? "这个条目" : "This entry";
    return (
      <div className="empty">
        {/* **不许再指向「记录这句」**（2026-08-14 那颗按钮和整条选区工具条一起删了）：
            指着一个不存在的按钮，比不给下一步更糟——作者会以为是自己没找到。
            现在这一格的填充路径只有后台整理那一条（换章触发 → 右栏「待确认」）。 */}
        {language === "zh" ? (
          <>
            {name} 在第 {chapter} 章还没有原文依据。
            写完一章翻到下一章时系统会自己去读它，读出来的会摆在右栏「待确认」里等你点头。
          </>
        ) : (
          <>
            {name} has no supporting passages in chapter {chapter} yet. Once you finish this
            chapter and move to the next one, the system will read through it on its own —
            anything it finds will wait in the "Pending" panel on the right for you to confirm.
          </>
        )}
      </div>
    );
  }

  return (
    <div>
      <div className="row" style={{ color: "var(--dim)", fontSize: 12, marginBottom: 8 }}>
        {language === "zh"
          ? `${state.data?.node.name} · 第 ${chapter} 章 · ${evidenced.length} 条原文依据`
          : `${state.data?.node.name} · Chapter ${chapter} · ${evidenced.length} supporting ` +
            (evidenced.length === 1 ? "passage" : "passages")}
      </div>
      {evidenced.map((e) => (
        <EvidenceRow key={e.id} pid={projectId!} edge={e} dstName={nameOf(e.dst)} />
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
        <span style={{ color: "var(--dim)", fontWeight: 400 }}>
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
