import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { CharacterEvidence } from "./CharacterEvidence";

// 「依据」那一格：把落定的事实还原成当年那句原文。
//
// **2026-09-04 起它只列「谁和谁」那一类**：所在地和状态维度在「状态」那一格已经摆过
// 一遍，在这儿再列一遍只会让人以为那是另一件事（作者裁定，见组件顶注）。
// 这一格之前没有组件测试，而这条规矩**坏掉时不会有任何别的东西红**——状态那几行悄悄
// 爬回来，屏幕上只是多了两行看着挺合理的东西。

const PID = "project:ID1";
const HERO = "character:ID9";

const EVIDENCE = {
  id: "evidence:ID14",
  chapter_number: 156,
  quote_text: "他缓缓抬起头，目光如电。",
  anchor: { para_index: 0, quote_text: "他缓缓抬起头，目光如电。", occurrence_k: 1 },
};

const edge = (id: string, dst: string, type: string) => ({
  id,
  src: HERO,
  dst,
  type,
  valid_from_chapter: 156,
  valid_to_chapter: null,
  evidence_id: EVIDENCE.id,
  props: { value: null },
});

const render = (edges: unknown[], states: unknown[] = []) =>
  renderWithApi(<CharacterEvidence characterId={HERO} />, [
    {
      match: /\/characters\/[^/]+\/state/,
      body: { ...fixtures.characterState, edges, states },
    },
    { match: /\/evidence\//, body: EVIDENCE },
  ]);

describe("CharacterEvidence", () => {
  it("状态类整类不列：所在地和状态维度都不进这一格", async () => {
    useCoords.setState({ projectId: PID, chapter: 156 });
    const place = fixtures.rosterWithCounts.find((n) => n.label === "Location")!;
    const dim = { id: "statedim:1", label: "StateDim", name: "装备" };
    render(
      [edge("edge:loc1", place.id, "LOCATED_AT"), edge("edge:s1", dim.id, "HAS_STATE")],
      [{ dim, dim_key: null, value: "持沧浪剑", value_key: null, since_chapter: 156 }],
    );

    // 两条边都带引语，但两条都是状态类 ⇒ 这一格是空态那句话，不是两行事实。
    expect(await screen.findByText(/尚无原文依据/)).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(place.name))).toBeNull();
    expect(screen.queryByText(/装备/)).toBeNull();
  });

  it("关系那一行照旧：角色册里的名字 + 当年那句原文", async () => {
    useCoords.setState({ projectId: PID, chapter: 156 });
    const peer = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    render([edge("edge:r1", peer.id, "RELATED_TO")]);

    expect(await screen.findByText(new RegExp(peer.name))).toBeInTheDocument();
    expect(await screen.findByText(/依据 第 156 章/)).toHaveTextContent(EVIDENCE.quote_text);
  });
});
