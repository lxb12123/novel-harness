import { describe, expect, it } from "vitest";
import fixtures from "./__fixtures__/api.json";
import type { DraftCandidateView } from "./api/types";
import { chaptersOf, draftLabel, draftsHeading, landedNote, unitsLabel } from "./drafts";

// 「桌上摆着的那几稿」那块屏幕的纯逻辑（ADR 0022）。
//
// **这里的每一条都是「界面替作者做了什么判断」**，所以它们必须能脱离渲染验证：
// 屏幕上把它叫什么、上面那句怎么说。

/** 真 dump 里那一稿（`tests/test_frontend_contract.py` 冻的），**一个字段都没改**。 */
const REAL = fixtures.chatTurn.drafts[0] as DraftCandidateView;

/** 同一稿的变体。**这不是手写夹具**：形状原样来自上面那一份，改的只有「第几稿 /
 *  落没落盘 / 有没有自述」——而**一批三稿、其中一稿进了书**恰恰是这块屏幕要画的
 *  常态，真 dump 里那一轮只写了一稿（`landed:false`），拿不到这个形态。 */
const variant = (over: Partial<DraftCandidateView>): DraftCandidateView => ({ ...REAL, ...over });

describe("屏幕上把它叫什么", () => {
  it("「第 N 稿」用的是 `ordinal`，**那一串内部标识一个字符都不在里面**", () => {
    expect(draftLabel(REAL, "zh")).toBe("第 1 稿");
    expect(REAL.id).toMatch(/:/); // 探针：夹具里真有那么个形状
    expect(draftLabel(REAL, "zh")).not.toContain(REAL.id);
    expect(draftLabel(REAL, "en")).toBe("Draft 1");
  });

  it("字数带千位分隔，**而且是手算的**（`toLocaleString` 跟着环境的区域设置变）", () => {
    expect(unitsLabel(900, "zh")).toBe("约 900 字");
    expect(unitsLabel(2800, "zh")).toBe("约 2,800 字");
    expect(unitsLabel(12000, "zh")).toBe("约 12,000 字");
    expect(unitsLabel(0, "zh")).toBe("约 0 字");
    expect(unitsLabel(2800, "en")).toBe("about 2,800 characters");
  });

  it("上面那句话只报事实：写了几稿、哪一章", () => {
    expect(draftsHeading([REAL], "zh")).toBe("本轮 1 稿 · 第 2 章");
    expect(draftsHeading([REAL, variant({ id: "draft:ID9", chapter: 3 })], "zh")).toBe(
      "本轮 2 稿 · 第 2、3 章",
    );
    expect(chaptersOf([REAL, variant({ chapter: 3 }), variant({ chapter: 3 })])).toEqual([2, 3]);
    // 单复数：英文那半不是拼片段，稿数变化时 "draft"/"drafts" 要跟着变。
    expect(draftsHeading([REAL], "en")).toBe("Wrote 1 draft this round · chapter 2");
    expect(draftsHeading([REAL, variant({ id: "draft:ID9", chapter: 3 })], "en")).toBe(
      "Wrote 2 drafts this round · chapters 2, 3",
    );
  });
});

describe("有稿子进了书", () => {
  it("**说得出怎么退** —— 进了书的那几稿：退路是版本历史；哪几稿进了书由每一稿自己那一行说", () => {
    expect(REAL.landed).toBe(false); // 探针：真 dump 那一轮跑完稿子还在桌上（ADR 0048：作者按保存才进书）
    const note = landedNote([REAL, variant({ id: "draft:ID44", ordinal: 2, landed: true })], "zh");
    expect(note).toContain("历史"); // 退路：正文那边那颗按钮
    expect(note).not.toContain("第 2 稿");
    // 这一次打开工作台以来作者保存过的那几稿（`liveDraft.ts::saved`）也算进了书。
    expect(landedNote([REAL], "en", [REAL.id])).toContain("History");
  });

  it("一个都没进书就一个字都不写（零不写）", () => {
    expect(landedNote([REAL], "zh")).toBeNull();
    expect(landedNote([], "zh")).toBeNull();
  });
});
