import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fixtures } from "../test/harness";
import type { KnowledgeMatrix, SceneConstraints } from "../api/types";
import { MatrixView } from "./KnowledgeMatrix";

// 头牌（README 第一行那个框）。喂的是**真后端 dump 出来的 matrix**，不是手写的形状。
//
// JSON import 会把 `state` 宽化成 string，所以这里必须 cast。那个 cast 不是白洞：
// 形状真变了的话 `tests/test_frontend_contract.py` 先红（它逐字节比对 dump），
// 这边红的是「变了之后组件还渲不渲得出来」。两头各管一半。
const matrix = fixtures.matrix as unknown as KnowledgeMatrix;
const constraints = fixtures.constraints as unknown as SceneConstraints;

describe("认知矩阵", () => {
  it("三态必须长得完全不一样 —— 这是整个产品的那句话", () => {
    render(<MatrixView matrix={matrix} constraints={constraints} />);

    // 断言的是**产品契约**（这三种写法），不是 fixture 里碰巧有什么：
    // 「✗ 不知道」写成「未知」都算破坏承诺，那不该跟着 fixture 漂。
    expect(screen.getByText("✓ 知道")).toBeInTheDocument();
    expect(screen.getAllByText("✗ 不知道").length).toBeGreaterThan(0);
  });

  it("KNOWS 一定带着 since_chapter —— 「他从第几章起知道」才是这个产品在卖的东西", () => {
    const knows = matrix.cells.find((c) => c.state === "KNOWS");
    expect(knows, "fixture 里没有 KNOWS 单元格，这个测试就测不到东西了").toBeDefined();

    render(<MatrixView matrix={matrix} constraints={constraints} />);
    expect(screen.getByText(`ch${knows!.since_chapter}`)).toBeInTheDocument();
  });

  it("行是人、列是秘密，一个都不能少", () => {
    render(<MatrixView matrix={matrix} constraints={constraints} />);
    const table = screen.getByRole("table");
    for (const c of matrix.characters) {
      expect(within(table).getByText(c.name)).toBeInTheDocument();
    }
    for (const s of matrix.secrets) {
      expect(within(table).getByText(s.name)).toBeInTheDocument();
    }
  });

  it("must_not_reveal 渲染的是显示名标签 —— 秘密的**内容**一个字都不该到前端", () => {
    render(<MatrixView matrix={matrix} constraints={constraints} />);
    expect(screen.getByText("本场景 must_not_reveal")).toBeInTheDocument();

    // §1.2 的收窄纪律在前端这一侧的兑现：出参里就不该有 props/description，
    // 所以渲染出来的 DOM 里也不可能有。这条在 fixture 层已经被后端测试钉了一遍，
    // 这里再钉一遍是因为**它是这个项目的核心主张，值得两处都拦**。
    expect(document.body.textContent).not.toContain("在北荒");
    expect(document.body.textContent).not.toContain("萧决是魔尊之子");
  });

  it("没有在场角色时说人话，不画一张空表", () => {
    render(<MatrixView matrix={{ ...matrix, characters: [], cells: [] }} />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText(/没有可显示的在场角色或秘密/)).toBeInTheDocument();
  });

  it("解析不出的称呼要说出来 —— 面板对他们是退化值，不说等于骗人", () => {
    render(<MatrixView matrix={{ ...matrix, unresolved_cast: ["师兄"] }} />);
    expect(screen.getByText(/师兄/)).toBeInTheDocument();
    expect(screen.getByText(/无法解析/)).toBeInTheDocument();
  });
});
