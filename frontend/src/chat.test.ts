import { describe, expect, it } from "vitest";
import { fixtures } from "./test/harness";
import {
  CHAT_TAIL,
  elapsedText,
  emphasize,
  receiptNotes,
  stopFootnote,
  tailWindow,
} from "./chat";
import type { ChatMessageView, TurnReceipt } from "./api/types";

/** 回执的底子是**真 dump**（`fixtures.chatTurn`），只改要验的那几个字段——
 *  手写一份 `TurnReceipt` 出来就是在验一份我自己以为的形状。 */
const RECEIPT = fixtures.chatTurn as unknown as TurnReceipt;
const receipt = (over: Partial<TurnReceipt> = {}): TurnReceipt => ({ ...RECEIPT, ...over });
const ctx = (over: Partial<TurnReceipt["context"]>): TurnReceipt["context"] => ({
  ...RECEIPT.context,
  ...over,
});

const msg = (seq: number): ChatMessageView => ({ seq, speaker: "author", text: `第 ${seq} 句` });
const many = (n: number) => Array.from({ length: n }, (_, i) => msg(i));

describe("长对话怎么办", () => {
  it("短的全在屏幕上", () => {
    expect(tailWindow(many(3), false)).toEqual({ hidden: 0, shown: many(3) });
  });

  it("长的只渲染尾巴，**但一条都没丢** —— 收起来的条数说得出来", () => {
    const w = tailWindow(many(CHAT_TAIL + 12), false);
    expect(w.hidden).toBe(12);
    expect(w.shown).toHaveLength(CHAT_TAIL);
    // 尾巴是最后那几条，不是最前那几条：作者要接着说的是最近这一段。
    expect(w.shown[w.shown.length - 1].seq).toBe(CHAT_TAIL + 11);
  });

  it("点过「看更早的」就全给", () => {
    expect(tailWindow(many(200), true).hidden).toBe(0);
    expect(tailWindow(many(200), true).shown).toHaveLength(200);
  });
});

describe("秒表", () => {
  it("一分钟以内只说秒", () => {
    expect(elapsedText(12_400)).toBe("已经 12 秒");
  });

  it("超过一分钟补零，读起来才是一个时间不是两个数", () => {
    expect(elapsedText(65_000)).toBe("已经 1 分 05 秒");
    expect(elapsedText(3 * 60_000 + 42_000)).toBe("已经 3 分 42 秒");
  });

  it("时钟往回跳（系统对时）也不会印出负数", () => {
    expect(elapsedText(-5000)).toBe("已经 0 秒");
  });
});

describe("这一轮实际发生了什么", () => {
  it("查了几次说得出来 —— **查到了什么一个字都不说**", () => {
    const notes = receiptNotes(receipt({ lookups: 3, calls_without_usage: 0 }));
    expect(notes.join("\n")).toContain("查了 3 次");
  });

  it("一次都没查、什么都没裁 —— 一行都不写", () => {
    // 一排「0 次 / 裁掉 0 条」会把真正非零的那一行淹掉。
    expect(
      receiptNotes(receipt({ lookups: 0, calls_without_usage: 0, context: ctx({}) })),
    ).toEqual([]);
  });

  it("**有调用没报用量时必须说** —— 那个 token 数是低估的", () => {
    // 一个自称是全部的低估数字，是这个仓库反复在修的失败形态（底栏的花销汇总同病）。
    const notes = receiptNotes(receipt({ lookups: 0, calls_without_usage: 2 }));
    expect(notes).toHaveLength(1);
    expect(notes[0]).toContain("少算");
  });

  it("后面章节的查询结果被挡掉时，说得出为什么挡", () => {
    const notes = receiptNotes(receipt({ lookups: 0, context: ctx({ off_chapter: 2 }) }));
    expect(notes.join("\n")).toMatch(/后面的章节/);
  });

  it("它手上那份正文过期了要说 —— 否则作者永远不知道它曾经拿着一份旧稿", () => {
    const notes = receiptNotes(receipt({ lookups: 0, context: ctx({ stale_lookups: 1 }) }));
    expect(notes.join("\n")).toMatch(/重新读/);
  });

  it("这一轮主动收起来的那几条合成一句，并且明说作者的话没被删", () => {
    const notes = receiptNotes(
      receipt({
        lookups: 0,
        calls_without_usage: 0,
        context: ctx({ trimmed_results: 2, dropped_lookups: 1, dropped_reasoning: 1 }),
      }),
    );
    expect(notes).toHaveLength(1);
    expect(notes[0]).toContain("4 条");
    expect(notes[0]).toContain("你说过的话一句都没删");
  });

  it("**上次断在半路的那几步单说一句** —— 它和「为了装下收起来」不是一回事", () => {
    // 收起来的重查一次就有；这一条是 canonical 里本来就缺的（`LOST_RESULT`），
    // 而 `pending_calls` 扫到作者发言就停 —— 再也没有人会去补它。
    const notes = receiptNotes(
      receipt({
        lookups: 0,
        calls_without_usage: 0,
        context: ctx({ trimmed_results: 2, lost_lookups: 1 }),
      }),
    );
    expect(notes).toHaveLength(2);
    expect(notes.find((n) => n.includes("断在半路"))).toBeTruthy();
    // 合成一句的话这儿会是「3 条」，而那一句说的是「重查一次就有」——对断掉的那次不成立。
    expect(notes.find((n) => n.includes("收起来"))).toContain("2 条");
  });

  it("真 dump 那一份：查了一次 + 有一次没量准", () => {
    const notes = receiptNotes(RECEIPT);
    expect(notes).toHaveLength(2);
  });
});

describe("按了停、屏幕上却写「说完了」", () => {
  it("停真的送达了而这一轮报的是 done —— 补一句，别让按钮看起来是坏的", () => {
    // 后端报 done 是对的（那一轮本来就在最后一次调用之后结束，停没有让任何事情少发生），
    // 但作者这一侧看到的是「我按了停，它说说完了」。
    expect(stopFootnote(true, receipt({ reason: "done" }))).toMatch(/你按下停/);
  });

  it("停生效了就什么都不补 —— 后端那句话已经说清楚了", () => {
    expect(stopFootnote(true, receipt({ reason: "author_stopped" }))).toBeNull();
  });

  it("压根没按过停，不许凭空补一句", () => {
    expect(stopFootnote(false, receipt({ reason: "done" }))).toBeNull();
  });
});

describe("后端那句话里的重音", () => {
  it("成对的 `**` 渲染成强调，星号本身不上屏", () => {
    // 真形态：`stop_wording(CONTEXT_FULL)` 里就有这么一对。
    const parts = emphasize("这段对话太长了——**你说过的话一句都没被删掉**。");
    expect(parts.map((p) => p.text).join("")).not.toContain("*");
    expect(parts.find((p) => p.strong)?.text).toBe("你说过的话一句都没被删掉");
  });

  it("落单的星号原样留着 —— **认不出就别动它**", () => {
    // 凭空吞掉两个字符比留着两颗星号更糟：那是在改后端写的那句话。
    expect(emphasize("三分之一 * 两倍")).toEqual([{ text: "三分之一 * 两倍", strong: false }]);
    expect(emphasize("**只开了个头")).toEqual([{ text: "**只开了个头", strong: false }]);
  });

  it("一句普通的话原样出来，一段都不多切", () => {
    expect(emphasize("说完了。")).toEqual([{ text: "说完了。", strong: false }]);
  });
});
