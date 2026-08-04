import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StateSnapshot } from "../api/types";
import { fixtures, renderWithApi } from "../test/harness";
import { StateCards } from "./StateCards";

describe("当前状态卡", () => {
  it("渲染在场角色、所在地与状态维度", () => {
    const states = fixtures.states as unknown as StateSnapshot[];
    renderWithApi(<StateCards states={states} />);
    for (const s of states) {
      const card = screen.getByText(new RegExp(s.node.name)).closest(".statecard") as HTMLElement;
      expect(
        within(card).getByText(new RegExp(s.location ? s.location.name : "未记录")),
      ).toBeInTheDocument();
    }
  });

  it("空列表给空态而不是假装有角色", () => {
    renderWithApi(<StateCards states={[]} />);
    expect(screen.getByText(/无在场角色/)).toBeInTheDocument();
  });
});
