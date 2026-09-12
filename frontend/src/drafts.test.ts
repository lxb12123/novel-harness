import { describe, expect, it } from "vitest";
import fixtures from "./__fixtures__/api.json";
import type { DraftCandidateView } from "./api/types";
import {
  COLUMN_MIN_PX,
  COMPARE_OPEN_MAX,
  chaptersOf,
  comparePath,
  draftLabel,
  draftsHeading,
  landedNote,
  openOnCompare,
  sideBySide,
  unitsLabel,
} from "./drafts";

// 「桌上摆着的那几稿」那块屏幕的纯逻辑（ADR 0022）。
//
// **这里的每一条都是「界面替作者做了什么判断」**，所以它们必须能脱离渲染验证：
// 默认摊开哪一版、什么时候并排、屏幕上把它叫什么。

/** 真 dump 里那一稿（`tests/test_frontend_contract.py` 冻的），**一个字段都没改**。 */
const REAL = fixtures.drafts.drafts[0] as DraftCandidateView;

/** 同一稿的变体。**这不是手写夹具**：形状原样来自上面那一份，改的只有「第几稿 /
 *  落没落盘 / 有没有自述」——而**一批三稿、其中一稿进了书**恰恰是这块屏幕要画的
 *  常态，真 dump 里那一轮只写了一稿（`landed:false`），拿不到这个形态。 */
const variant = (over: Partial<DraftCandidateView>): DraftCandidateView => ({ ...REAL, ...over });

describe("并排还是摞着：`sideBySide`", () => {
  it("量不到宽度（首帧 / jsdom 里 `clientWidth` 恒为 0）就回窄档", () => {
    // 猜错的代价不对称：少并排一次是少一次方便；反过来是三章正文挤在三条缝里。
    expect(sideBySide(0, 3)).toBe(false);
    expect(sideBySide(Number.NaN, 3)).toBe(false);
  });

  it("只有一稿的时候永远不并排 —— 一列跟摞着长得一样，白多一层壳", () => {
    expect(sideBySide(4000, 1)).toBe(false);
  });

  it("**判据是「每一列都读得下去」，不是一个写死的断点**", () => {
    // 三稿要 3 × 260；差一个像素就还是窄档（那一档下每列 259px，一行塞不下十个字）。
    expect(sideBySide(3 * COLUMN_MIN_PX - 1, 3)).toBe(false);
    expect(sideBySide(3 * COLUMN_MIN_PX, 3)).toBe(true);
    // 两稿在同样的宽度下当然并排得了 —— 列数越少门槛越低。
    expect(sideBySide(2 * COLUMN_MIN_PX, 2)).toBe(true);
  });
});

describe("并排比那一页默认摊开哪几列", () => {
  it("**摊开最近的几列**，更早的收着", () => {
    // 那一页是作者专门点开来并排读的，他要的就是摊开——判据和入口那一档有意不同。
    const many = [1, 2, 3, 4, 5].map((n) => variant({ id: `draft:ID${n}`, ordinal: n }));
    expect(openOnCompare(many)).toEqual(["draft:ID1", "draft:ID2", "draft:ID3"]);
    expect(openOnCompare(many)).toHaveLength(COMPARE_OPEN_MAX);
  });
});

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
  it("**说得出怎么退** —— 落盘不问作者，那就欠他这一半；哪几稿进了书由每一稿自己那一行说", () => {
    expect(REAL.landed).toBe(true); // 探针：真 dump 那一稿就写进去了（ADR 0048）
    const note = landedNote([REAL, variant({ id: "draft:ID44", ordinal: 2, landed: true })], "zh");
    expect(note).toContain("历史"); // 退路：正文那边那颗按钮
    expect(note).not.toContain("第 2 稿");
    const noteEn = landedNote([REAL], "en");
    expect(noteEn).toContain("History");
  });

  it("一个都没进书就一个字都不写（零不写）", () => {
    expect(landedNote([variant({ landed: false })], "zh")).toBeNull();
    expect(landedNote([], "zh")).toBeNull();
  });
});

describe("并排比那一页的地址", () => {
  it("**地址里只有章号**：书的内部标识不进地址栏（地址栏也是屏幕）", () => {
    expect(comparePath(12)).toBe("#/compare/12");
    expect(comparePath(2)).not.toContain(":");
  });
});
