import { useCharacterState, useEvidence, useRoster } from "../api/hooks";
import { useCoords } from "../store";
import { edgeName, type Edge } from "../api/types";

// Tab3 确定性证据 —— 把「✓知道 ch88」还原成当年那句原文。
// **明确不给分数**（v1 没有向量，出任何 score = 编的，§1.2）。声明产生的边带 evidence_id，
// 逐条取回「来源章 + 原文片段」。作者看到的不是「系统觉得」，是「你自己写过的那句」。

// 关系类型 → 中文在 `api/types.ts::EDGE_ZH`（全前端一份，9 类全列，兜底是中文）。
// 这儿原来那份拷贝只有 7 行且 `?? edge.type` 原样回吐。

export function EvidenceTab() {
  const { projectId, chapter, selectedNodeId } = useCoords();
  const state = useCharacterState(projectId, selectedNodeId, chapter);
  const roster = useRoster(projectId);

  if (!selectedNodeId)
    return <div className="empty">从左侧选择一个条目，这里会显示与它相关的原文依据。</div>;

  // 查不到就说「—」，**绝不 `?? id`**（同 `BottomBar`：两条独立缓存差一拍）。
  const nameOf = (id: string) => roster.data?.find((n) => n.id === id)?.name ?? "—";
  const evidenced = (state.data?.edges ?? []).filter((e) => e.evidence_id);

  if (state.isFetching && !state.data) return <div className="empty">加载中…</div>;
  if (evidenced.length === 0)
    return (
      <div className="empty">
        {state.data ? state.data.node.name : "这个条目"} 在第 {chapter} 章还没有原文依据。
        在正文里选中一句话并选择“记录这句”即可添加。
      </div>
    );

  return (
    <div>
      <div className="row" style={{ color: "var(--dim)", fontSize: 12, marginBottom: 8 }}>
        {state.data?.node.name} · 第 {chapter} 章 · {evidenced.length} 条原文依据
      </div>
      {evidenced.map((e) => (
        <EvidenceRow key={e.id} pid={projectId!} edge={e} dstName={nameOf(e.dst)} />
      ))}
    </div>
  );
}

function EvidenceRow({ pid, edge, dstName }: { pid: string; edge: Edge; dstName: string }) {
  const { data, isFetching } = useEvidence(pid, edge.evidence_id ?? null);
  const believed = edge.type === "BELIEVES" ? edge.props.believed_value : null;
  return (
    <div className="statecard">
      <div className="nm">
        {edgeName(edge.type)} {dstName}
        {believed && <span className="pc"> —「{believed}」</span>}
        <span style={{ color: "var(--dim)", fontWeight: 400 }}> · 第 {edge.valid_from_chapter} 章起</span>
      </div>
      {isFetching && <div className="row">取原文中…</div>}
      {data && (
        <div className="row">
          依据 第 {data.chapter_number} 章：「{data.quote_text}」
        </div>
      )}
    </div>
  );
}
