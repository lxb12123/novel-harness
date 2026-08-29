import { useMemo } from "react";
import { ReactFlow, Background, Controls, type Edge as RFEdge, type Node as RFNode } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useSubgraph } from "../api/hooks";
import { nodeLabelText, edgeLabelText } from "../backendMessages";
import { type Language } from "../language";
import { useCoords } from "../store";
import { type NodeLabel, type Subgraph } from "../api/types";

// 局部关系图（Tab2）—— React Flow（§2.5：DOM/SVG 原生，十几个节点的小图，和编辑器同一套
// React 心智）。中心 = selectedNodeId，hops≤2 硬上限（3 跳数学上坏，引擎直接拒）。
// 布局：中心居中，邻居摊一个环。fcose/Cytoscape 的连续漫游是 P3 全屏图的事，这里够看。

const HOPS = 2;

const LABEL_COLOR: Record<string, string> = {
  Character: "var(--accent)",
  Location: "var(--k)",
  Faction: "#7a5cc0",
  Object: "#5c8ac0",
  Foreshadow: "#c05c93",
};
// 类别 / 关系 → 作者的说法，两张表都在 `backendMessages.ts`（`NODE_LABEL` /
// `EDGE_LABEL`，全前端各一份，覆盖率由 `tests/test_wording_guard.py` 拿 Python
// 枚举逐个比）。这儿原来有一份 7 行的 `EDGE_ZH` 拷贝——`PLANTED_IN` / `RESOLVED_IN`
// 一律显示成「关联」，而伏笔的「埋在 / 回应于」正是那两类边存在的全部理由。

export function toFlow(
  g: Subgraph,
  language: Language,
): { nodes: RFNode[]; edges: RFEdge[] } {
  const others = g.nodes.filter((n) => n.id !== g.center.id);
  const R = 190;
  const nodes: RFNode[] = g.nodes.map((n) => {
    const isCenter = n.id === g.center.id;
    let x = 0,
      y = 0;
    if (!isCenter) {
      const i = others.findIndex((o) => o.id === n.id);
      const a = (2 * Math.PI * i) / Math.max(1, others.length);
      x = R * Math.cos(a);
      y = R * Math.sin(a);
    }
    return {
      id: n.id,
      position: { x, y },
      data: { label: `${n.name}\n${nodeLabelText(n.label as NodeLabel, language)}` },
      style: {
        border: `1px solid ${LABEL_COLOR[n.label] ?? "var(--line)"}`,
        borderWidth: isCenter ? 2 : 1,
        borderRadius: 8,
        padding: "6px 10px",
        background: "var(--bg)",
        color: "var(--ink)",
        fontSize: 12,
        whiteSpace: "pre-line",
        textAlign: "center",
        boxShadow: isCenter ? "0 0 0 3px var(--sel)" : "none",
      },
    };
  });
  const edges: RFEdge[] = g.edges.map((e) => ({
    id: e.id,
    source: e.src,
    target: e.dst,
    label: `${edgeLabelText(e.type, language)} · 第 ${e.valid_from_chapter} 章起`,
    labelStyle: { fill: "var(--dim)", fontSize: 10 },
    style: { stroke: "var(--line)" },
  }));
  return { nodes, edges };
}

export function LocalGraph() {
  const { projectId, chapter, selectedNodeId, focusNode } = useCoords();
  const { data, isFetching, error } = useSubgraph(projectId, selectedNodeId, chapter, HOPS);
  const flow = useMemo(() => (data ? toFlow(data, "zh") : null), [data]);

  if (!selectedNodeId)
    return (
      <div className="empty">
        点左栏花名册里的一个人，或在正文里选中一句话按「查图谱」→ 这里画出他第 {chapter} 章的
        相关人物和设定。
      </div>
    );
  if (error) return <div className="err-box">{(error as Error).message}</div>;
  if (!flow) return <div className="empty">{isFetching ? "加载中…" : "—"}</div>;

  return (
    <div>
      <div style={{ height: 420, border: "1px solid var(--line)", borderRadius: 8 }}>
        <ReactFlow
          nodes={flow.nodes}
          edges={flow.edges}
          fitView
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_, n) => focusNode(n.id)} // 点邻居 = 换中心，连续漫游
        >
          <Background gap={16} color="var(--line)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="row" style={{ color: "var(--dim)", fontSize: 12, marginTop: 6 }}>
        当前：{data!.center.name} · {data!.nodes.length} 个相关条目 · {data!.edges.length} 条关系
        {data!.truncated && " · 部分内容已折叠"} · 点击其他条目继续查看
      </div>
    </div>
  );
}
