import { useMemo } from "react";
import { ReactFlow, Background, Controls, type Edge as RFEdge, type Node as RFNode } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useSubgraph } from "../api/hooks";
import { useCoords } from "../store";
import type { Subgraph } from "../api/types";

// 局部关系图（Tab2）—— React Flow（§2.5：DOM/SVG 原生，十几个节点的小图，和编辑器同一套
// React 心智）。中心 = selectedNodeId，hops≤2 硬上限（3 跳数学上坏，引擎直接拒）。
// 布局：中心居中，邻居摊一个环。fcose/Cytoscape 的连续漫游是 P3 全屏图的事，这里够看。

const HOPS = 2;

const LABEL_COLOR: Record<string, string> = {
  Character: "var(--accent)",
  Location: "var(--k)",
  Secret: "var(--b)",
  Faction: "#7a5cc0",
  Object: "#5c8ac0",
  Foreshadow: "#c05c93",
};
const LABEL_ZH: Record<string, string> = {
  Character: "人物",
  Location: "地点",
  Secret: "秘密",
  Faction: "势力",
  Object: "物品",
  Foreshadow: "伏笔",
  StateDim: "状态维",
  Chapter: "章",
};

export function toFlow(g: Subgraph): { nodes: RFNode[]; edges: RFEdge[] } {
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
      data: { label: `${n.name}\n${LABEL_ZH[n.label] ?? n.label}` },
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
    label: `${e.type}·ch${e.valid_from_chapter}`,
    labelStyle: { fill: "var(--dim)", fontSize: 10 },
    style: { stroke: "var(--line)" },
  }));
  return { nodes, edges };
}

export function LocalGraph() {
  const { projectId, chapter, selectedNodeId, focusNode } = useCoords();
  const { data, isFetching, error } = useSubgraph(projectId, selectedNodeId, chapter, HOPS);
  const flow = useMemo(() => (data ? toFlow(data) : null), [data]);

  if (!selectedNodeId)
    return (
      <div className="empty">
        点左栏花名册里的一个人，或在正文里选中一句话按「查图谱」→ 这里画出他第 {chapter} 章的
        局部关系（≤2 跳）。
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
        中心：{data!.center.name} · {data!.nodes.length} 节点 · {data!.edges.length} 边
        {data!.truncated && " ·（已折叠，超上限）"} · 点邻居换中心
      </div>
    </div>
  );
}
