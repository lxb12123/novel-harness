import { describe, expect, it } from "vitest";
import { CHANGED_THRESHOLD, similarity, tintRanges } from "./tint";

// 写作助手放进编辑器的那一稿，哪些段是新增（绿）、哪些段是改过（浅红）（`tint.ts`）。

const OLD = "雨歇了。\n\n贾环站在废墟之中，久久没有动弹。\n\n“走吧。”他转身。";

describe("两段字像不像", () => {
  it("一样 = 1，完全不同 = 0，改了几个字还是像", () => {
    expect(similarity("贾环站在废墟之中", "贾环站在废墟之中")).toBe(1);
    expect(similarity("贾环站在废墟之中", "夜雨初歇京城肃杀")).toBe(0);
    expect(similarity("贾环站在废墟之中，久久没有动弹。", "贾环站在废墟之中，许久没有动弹。")).toBeGreaterThan(
      CHANGED_THRESHOLD,
    );
  });
});

describe("该涂哪几段", () => {
  it("原样的段不涂；旧稿里没有的段是新增", () => {
    const next = OLD + "\n\n周迅挣扎着爬起来。";
    const ranges = tintRanges(OLD, next);
    expect(ranges).toEqual([{ from: next.indexOf("周迅"), to: next.length, kind: "added" }]);
  });

  it("换掉旧稿里的一段、改了几个字 = 修改（浅红），不是新增", () => {
    const next = OLD.replace("久久没有动弹", "许久没有动弹");
    const ranges = tintRanges(OLD, next);
    expect(ranges).toHaveLength(1);
    expect(ranges[0].kind).toBe("changed");
    expect(next.slice(ranges[0].from, ranges[0].to)).toBe("贾环站在废墟之中，许久没有动弹。");
  });

  it("整章重写：每一段都涂，和旧段不像的全是新增", () => {
    const next = "夜雨初歇，京城处处弥漫着劫后余生的肃杀气息。\n\n贾环被人带出城南破庙时，浑身衣衫已被雨水浸透。";
    const ranges = tintRanges(OLD, next);
    expect(ranges.map((r) => r.kind)).toEqual(["added"]);
    expect(ranges[0]).toEqual({ from: 0, to: next.length, kind: "added" });
  });

  it("空稿起草（新章）：整篇都是新增，坐标从 0 到底", () => {
    const next = "第一段。\n\n第二段。";
    expect(tintRanges("", next)).toEqual([{ from: 0, to: next.length, kind: "added" }]);
  });

  it("空行不涂；只隔着空行的同色段并成一段，异色的分开", () => {
    const next = OLD + "\n\n新的一段。\n\n又新的一段。";
    const ranges = tintRanges(OLD, next);
    expect(ranges).toHaveLength(1);
    const mixed = OLD.replace("久久没有动弹", "许久没有动弹") + "\n\n新的一段。";
    const kinds = tintRanges(OLD, mixed).map((r) => r.kind);
    expect(kinds).toEqual(["changed", "added"]);
  });

  it("一个字都没改：一段都不涂", () => {
    expect(tintRanges(OLD, OLD)).toEqual([]);
  });
});
