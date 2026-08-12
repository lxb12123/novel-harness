import { describe, expect, it } from "vitest";
import { parseRoute, readCompareHandoff, writeCompareHandoff } from "./route";

// 工作台唯一那条路由（`#/compare/{章号}`，ADR 0022）。
//
// 两条纪律各一节：**认不出的地址回工作台**（不是空白页），
// **不知道是哪本书就说不知道**（不猜）。

describe("地址 → 路由", () => {
  it("并排比那一页认得出来，章号是数字", () => {
    expect(parseRoute("#/compare/12")).toEqual({ name: "compare", chapter: 12 });
  });

  it("**认不出的一律回工作台** —— 一个坏地址不该让作者看见空白页", () => {
    for (const hash of ["", "#", "#/", "#/compare", "#/compare/", "#/compare/abc", "#/别的"]) {
      expect(parseRoute(hash)).toEqual({ name: "workbench" });
    }
  });

  it("第 0 章不存在（章号从 1 起），第 -1 章更不存在", () => {
    expect(parseRoute("#/compare/0")).toEqual({ name: "workbench" });
    expect(parseRoute("#/compare/-1")).toEqual({ name: "workbench" });
  });
});

describe("「这条链接是哪本书的第几章」", () => {
  it("存得进、读得回", () => {
    writeCompareHandoff({ book: "project:ID1", chapter: 12 });
    expect(readCompareHandoff()).toEqual({ book: "project:ID1", chapter: 12 });
  });

  it("没存过就是没存过 —— **不许回一个看起来正常的默认值**", () => {
    expect(readCompareHandoff()).toBeNull();
  });

  it("存坏了（一行坏 JSON / 缺一半）也当没存过，不抛", () => {
    localStorage.setItem("nh.compare.v1", "{不是 JSON");
    expect(readCompareHandoff()).toBeNull();
    localStorage.setItem("nh.compare.v1", JSON.stringify({ book: "project:ID1" }));
    expect(readCompareHandoff()).toBeNull();
    localStorage.setItem("nh.compare.v1", JSON.stringify({ chapter: 2 }));
    expect(readCompareHandoff()).toBeNull();
  });
});
