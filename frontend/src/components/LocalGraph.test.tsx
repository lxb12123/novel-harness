import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { LocalGraph, toFlow } from "./LocalGraph";

// ReactFlow 的 canvas/SVG 渲染依赖真实布局测量，jsdom 给不了；把画布换成轻量桩，
// 测的是 LocalGraph 的接线：拉子图 → 转 flow 节点 → 名字进 DOM。
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ nodes, edges }: { nodes: { id: string; data: { label: string } }[]; edges: unknown[] }) => (
    <div data-testid="flow">
      {nodes.map((n) => (
        <span key={n.id} data-testid="flow-node">
          {n.data.label}
        </span>
      ))}
      <span data-testid="flow-edge-count">{edges.length}</span>
    </div>
  ),
  Background: () => null,
  Controls: () => null,
}));

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 2,
    selectedNodeId: "character:ID7",
    cast: "",
  });
  // ReactFlow 需要 ResizeObserver 和尺寸测量；jsdom 里没有，这里给个空实现。
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

describe("局部关系图", () => {
  it("以选中节点为中心渲染子图节点（fixture 真出参）", async () => {
    renderWithApi(<LocalGraph />, [{ match: /\/subgraph/, body: fixtures.subgraph }]);
    const nodes = await screen.findAllByTestId("flow-node");
    expect(nodes.length).toBe(fixtures.subgraph.nodes.length);
    expect((await screen.findAllByText(/萧决/)).length).toBeGreaterThan(0);
  });

  it("toFlow：中心节点加粗，关系使用自然中文和完整章名", () => {
    const g = {
      ...fixtures.subgraph,
      edges: [fixtures.states[0].edges[0]],
    } as unknown as Parameters<typeof toFlow>[0];
    const { nodes, edges } = toFlow(g);
    const center = nodes.find((n) => n.id === g.center.id);
    expect(center?.style?.borderWidth).toBe(2);
    const others = nodes.filter((n) => n.id !== g.center.id);
    const distances = others.map((n) => Math.hypot(n.position.x, n.position.y));
    expect(Math.max(...distances) - Math.min(...distances)).toBeLessThan(10);
    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe("在 · 第 1 章起");
  });
});
