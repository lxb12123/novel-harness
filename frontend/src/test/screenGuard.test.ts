import { describe, expect, it } from "vitest";
import { devTerms, engineWords, machineWords, rawIds } from "./screenGuard";

// **守卫的自守卫。**
//
// 一张「界面上不许出现研发术语」的网，最危险的失败不是它咬错人，是它**什么都咬不到
// 却一直绿着**——`ActivityLog.test.tsx` 那条词表正则就是这么绿了一整轮的
//（后端正在印 `KNOWS → BELIEVES`，而词表里恰好没有这两个词）。
//
// 所以这里钉的不是假想的坏字符串，是**真的上过这个产品的屏幕的**违规。写法参照
// `tests/test_doc_numbers.py` 里那个「五条历史违规」探针：探针过期了要么删要么换，
// 但不许悄悄变成空集。

/** 五个探针，每一个都在某一轮里真的被小说作者看到过（或者当场就会被看到）。 */
const HISTORICAL: ReadonlyArray<{ shown: string; term: string; why: string }> = [
  {
    shown: "萧决 对「血脉秘密」：KNOWS → BELIEVES",
    term: "KNOWS",
    why: "activity._decision_subtitle 直接印边类型；日志页那条词表断言没收这两个词",
  },
  {
    shown: "萧决 BELIEVES 血脉秘密",
    term: "BELIEVES",
    why: "同上，knows_declare 那一档",
  },
  {
    shown: "这本书在别处刚被改过 stale_base_version",
    term: "stale_base_version",
    why: "改一次名单就推高 canon 版本 → 紧接着确认必 409，而那个 409 只有码没有话",
  },
  {
    shown: "project_not_found",
    term: "project_not_found",
    why: "load_project 的 404 body 里只有 error + project_id，ApiError 拿它当 message",
  },
  {
    shown: "当前：萧决 在 n:ID22",
    term: "n:ID22",
    why: "ProposalReviewTab 的 `rosterMap.get(id) ?? id.slice(-6)`；截断后逃掉了 `node:` 那条守卫",
  },
];

describe("屏幕守卫的自守卫", () => {
  it.each(HISTORICAL)("抓得住真的上过屏的那一个：$term（$why）", ({ shown, term }) => {
    expect(devTerms(shown)).toContain(term);
  });

  it("五个探针**一个不漏**（少一个就说明这张网被谁改窄了）", () => {
    const caught = HISTORICAL.filter(({ shown, term }) => devTerms(shown).includes(term));
    expect(caught).toHaveLength(HISTORICAL.length);
  });

  it("三张网各自罩住它该罩的那一类", () => {
    expect(machineWords("stale_base_version project_not_found valid_from")).toEqual([
      "stale_base_version",
      "project_not_found",
      "valid_from",
    ]);
    expect(engineWords("KNOWS → BELIEVES · 还是 PROVISIONAL · 新人物（Character）")).toEqual([
      "KNOWS",
      "BELIEVES",
      "PROVISIONAL",
      "Character",
    ]);
    // **截断过的也算。** 前缀白名单（`node:` / `edge:` …）在这一条上会当场漏掉。
    expect(rawIds("边 edge:01J8XK 已撤回 · 当前：n:ID22")).toEqual(["edge:01J8XK", "n:ID22"]);
  });

  it("**不误报**：干净的界面一个字都不许被咬", () => {
    // 假红比漏报更危险：它会让下一个人把守卫关掉，而不是把界面修好。
    const clean = [
      "已确认的情节（谁在场、谁知道了）· 可信程度 60% · 萧决",
      "改「萧决 对 血脉秘密」· 现在是「知道」· 第 3 章起",
      "模型调用 · 抽取 · deepseek-v4 · 读入 1200 / 生成 400 token · 900 ms",
      "这台电脑上没有记价格：钥匙是你自己的，引擎不知道你和模型服务商谈的是多少钱。",
      "2026/08/11 01:28:40", // 时间戳里的冒号前面是数字，不是标识符前缀
      "已整理 3 次 · 花费 未记录",
    ].join("\n");
    expect(devTerms(clean)).toEqual([]);
  });
});
