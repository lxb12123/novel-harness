import { describe, expect, it } from "vitest";
import { fixtures } from "./test/harness";
import {
  applyTurnEvent,
  CHAT_TAIL,
  elapsedText,
  emphasize,
  receiptNotes,
  receiptSays,
  stopFootnote,
  tailWindow,
  visibleMessages,
  NO_PROGRESS,
  SPEAKER_ZH,
  type TurnProgress,
} from "./chat";
import type { ChatMessageView, ChatTurnEvent, TurnReceipt } from "./api/types";

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
    expect(elapsedText(12_400, "zh")).toBe("12 秒");
  });

  it("超过一分钟补零，读起来才是一个时间不是两个数", () => {
    expect(elapsedText(65_000, "zh")).toBe("1 分 05 秒");
    expect(elapsedText(3 * 60_000 + 42_000, "zh")).toBe("3 分 42 秒");
  });

  it("时钟往回跳（系统对时）也不会印出负数", () => {
    expect(elapsedText(-5000, "zh")).toBe("0 秒");
  });

  it("英文那半是整句模板，不是拼中文那一半翻过去的词", () => {
    expect(elapsedText(12_400, "en")).toBe("12s elapsed");
    expect(elapsedText(65_000, "en")).toBe("1m 05s elapsed");
    expect(elapsedText(-5000, "en")).toBe("0s elapsed");
  });
});

describe("这一轮实际发生了什么", () => {
  it("查了几次说得出来 —— **查到了什么一个字都不说**", () => {
    const notes = receiptNotes(receipt({ lookups: 3, calls_without_usage: 0 }), "zh");
    expect(notes.join("\n")).toContain("本轮查询 3 次资料");
  });

  it("一次都没查、什么都没裁 —— 一行都不写", () => {
    // 一排「0 次 / 裁掉 0 条」会把真正非零的那一行淹掉。
    expect(
      receiptNotes(receipt({ lookups: 0, calls_without_usage: 0, context: ctx({}) }), "zh"),
    ).toEqual([]);
  });

  it("**有调用没报用量时必须说** —— 那个 token 数是低估的", () => {
    // 一个自称是全部的低估数字，是这个仓库反复在修的失败形态（底栏的花销汇总同病）。
    const notes = receiptNotes(receipt({ lookups: 0, calls_without_usage: 2 }), "zh");
    expect(notes).toHaveLength(1);
    expect(notes[0]).toContain("偏少");
  });

  it("后面章节的查询结果被挡掉时，说得出为什么挡", () => {
    const notes = receiptNotes(receipt({ lookups: 0, context: ctx({ off_chapter: 2 }) }), "zh");
    expect(notes.join("\n")).toMatch(/后续章节/);
  });

  it("它手上那份正文过期了要说 —— 否则作者永远不知道它曾经拿着一份旧稿", () => {
    const notes = receiptNotes(receipt({ lookups: 0, context: ctx({ stale_lookups: 1 }) }), "zh");
    expect(notes.join("\n")).toMatch(/重新读/);
  });

  it("**中途说的没来得及答的要说**（2026-09-12）—— 它已经在对话里，不是丢了", () => {
    const quiet = { lookups: 0, calls_without_usage: 0, context: ctx({}) };
    const notes = receiptNotes(receipt({ ...quiet, unanswered: 2 }), "zh");
    expect(notes).toHaveLength(1);
    expect(notes[0]).toContain("2 条");
    expect(notes[0]).toMatch(/保留在对话中/);
    expect(receiptNotes(receipt({ ...quiet, unanswered: 0 }), "zh")).toEqual([]);
  });

  it("这一轮主动收起来的那几条合成一句，并且明说作者的话没被删", () => {
    const notes = receiptNotes(
      receipt({
        lookups: 0,
        calls_without_usage: 0,
        context: ctx({ trimmed_results: 2, dropped_lookups: 1, dropped_reasoning: 1 }),
      }),
      "zh",
    );
    expect(notes).toHaveLength(1);
    expect(notes[0]).toContain("4 条");
    expect(notes[0]).toContain("对话记录未删减");
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
      "zh",
    );
    expect(notes).toHaveLength(2);
    expect(notes.find((n) => n.includes("查询中断"))).toBeTruthy();
    // 合成一句的话这儿会是「3 条」，而那一句说的是「重查一次就有」——对断掉的那次不成立。
    expect(notes.find((n) => n.includes("已收起"))).toContain("2 条");
  });

  it("真 dump 那一份：查了一次 + 有一次没量准", () => {
    const notes = receiptNotes(RECEIPT, "zh");
    expect(notes).toHaveLength(2);
  });

  it("英文那半整句处理单复数，不是拼「N + 中文那半翻过去的词」", () => {
    // n === 1：单数整句。
    expect(receiptNotes(receipt({ lookups: 1, calls_without_usage: 0 }), "en")[0]).toBe(
      "1 lookup this round",
    );
    expect(
      receiptNotes(receipt({ lookups: 0, calls_without_usage: 1 }), "en")[0],
    ).toContain("undercount");
    // n > 1：复数整句，数字本身也要对。
    const plural = receiptNotes(receipt({ lookups: 3, calls_without_usage: 0 }), "en")[0];
    expect(plural).toBe("3 lookups this round");
  });
});

describe("按了停、屏幕上却写「说完了」", () => {
  it("停真的送达了而这一轮报的是 done —— 补一句，别让按钮看起来是坏的", () => {
    // 后端报 done 是对的（那一轮本来就在最后一次调用之后结束，停没有让任何事情少发生），
    // 但作者这一侧看到的是「我按了停，它说说完了」。
    expect(stopFootnote(true, receipt({ reason: "done" }), "zh")).toMatch(/「停」送达时/);
  });

  it("停生效了就什么都不补 —— 后端那句话已经说清楚了", () => {
    expect(stopFootnote(true, receipt({ reason: "author_stopped" }), "zh")).toBeNull();
  });

  it("压根没按过停，不许凭空补一句", () => {
    expect(stopFootnote(false, receipt({ reason: "done" }), "zh")).toBeNull();
  });

  it("英文那半是整句", () => {
    expect(stopFootnote(true, receipt({ reason: "done" }), "en")).toMatch(/Stop arrived/);
  });
});

describe("一轮没跑成，那句话留在对话里（后端迁移 012）", () => {
  /** 真 dump 的那一段（作者说了一句、那一轮死在发出去之前）。 */
  const failed = fixtures.chatDetailFailed.messages as unknown as ChatMessageView[];

  it("「系统」是认得出来的一档 —— 它画得出来，而且有名字", () => {
    expect(failed[1].speaker).toBe("system"); // 探针：夹具里真有这一档
    expect(SPEAKER_ZH.system.zh).toBe("系统");
    // 真 dump 的就是作者那块屏幕：你好 / 没跑成 / fff / 没跑成，一条都不许被筛掉。
    expect(visibleMessages(failed)).toEqual(failed);
  });

  it("认不出的说话人照旧一条都不上屏 —— 加了一档不等于把门打开了", () => {
    const sneaky = [
      ...failed,
      { seq: 9, speaker: "tool", text: "must_not_reveal" },
    ] as unknown as ChatMessageView[];
    expect(visibleMessages(sneaky)).toEqual(failed);
  });

  it("**这一轮留了一行的话，回执上那句话就不再画一遍**", () => {
    // 后端把「为什么」落进了库，`message` 是同一串字：两处一起画 = 作者读两遍。
    const notice = { seq: 1, speaker: "system", text: RECEIPT.message } as ChatMessageView;
    expect(receiptSays(receipt({ reason: "step_limit", messages: [notice] }))).toBeNull();
  });

  it("没留那一行的时候照旧画 —— 判据是结构，不是拿两串字去比", () => {
    const stopped = receipt({ reason: "step_limit" });
    expect(receiptSays(stopped)).toBe(RECEIPT.message);
    // 反证：这一轮的确说过话（所以后端不会留那一行），而回执那句话仍然要说。
    expect(RECEIPT.messages.some((m) => m.speaker === "system")).toBe(false);
  });

  it("🔴 **正常收场那一句不画** —— 每一轮都一样的「说完了。」不是信息", () => {
    // 作者 2026-09-10：「没有必要每次结束有这个」。`_STOP_WORDING[DONE]` 是固定的
    // 一句话，而它说的事屏幕上明摆着（话就在上面）。
    expect(RECEIPT.reason).toBe("done"); // 探针：真 dump 的那一份就是这一档
    expect(receiptSays(RECEIPT)).toBeNull();
    // **反证：别的收场一条都不许被顺手关掉。** 那几句每一句都在说一件屏幕上
    // 看不出来的事（查太多次 / 额度到顶 / 装不下了），关掉就等于这一轮不明不白地断了。
    expect(receiptSays(receipt({ reason: "cost_limit" }))).toBe(RECEIPT.message);
  });

  it("**作者按停、它自己已经问了一句：回执那句不再画** —— 那句话本身就在说「我停了」", () => {
    // 2026-09-12：停下来之后后端会再问作者一句（`run_turn` 的 debrief），`reply` 就是
    // 那句话，它以 assistant 气泡的样子已经在屏幕上了。再画「按你的意思停下了」= 同一件事
    // 说两遍。判据是 `reason` + `reply` 非空，不是拿字面去比。
    const asked = receipt({ reason: "author_stopped", reply: "我停在翻目录之前了。想换个方向？" });
    expect(receiptSays(asked)).toBeNull();
    // 没问出来（端点坏了 / 他又按了一次停）：回执那句照画，否则这一轮不明不白地断了。
    const silent = receipt({ reason: "author_stopped", reply: "" });
    expect(receiptSays(silent)).toBe(RECEIPT.message);
    // **反证：别的收场带着 `reply` 也照画。** `reply` 非空在正常一轮里是常态
    // （模型最后说的那段话），只有「作者停 + 它问了」这一对才是同一件事说两遍。
    expect(receiptSays(receipt({ reason: "cost_limit", reply: "我先查到这儿。" }))).toBe(
      RECEIPT.message,
    );
  });

  it("`seq` 撞号是**正常的** —— 所以它当不了 key", () => {
    // 系统那一行带的数是「它前面有几条历史」，和紧跟其后那一条同号。
    // 拿它当 React key 会把两条画成一条，而少掉的正是作者要看的那一句。
    const clash = [
      { seq: 1, speaker: "system", text: "这一轮没跑成" },
      { seq: 1, speaker: "author", text: "fff" },
    ] as unknown as ChatMessageView[];
    expect(new Set(clash.map((m) => m.seq)).size).toBe(1);
    expect(visibleMessages(clash)).toHaveLength(2);
  });
});

describe("后端那句话里的重音", () => {
  it("成对的 `**` 渲染成强调，星号本身不上屏", () => {
    // 真形态：`stop_wording(CONTEXT_FULL)` 里就有这么一对。
    const parts = emphasize("这段对话太长了——**当前对话记录未删减**。");
    expect(parts.map((p) => p.text).join("")).not.toContain("*");
    expect(parts.find((p) => p.strong)?.text).toBe("当前对话记录未删减");
  });

  it("落单的星号原样留着 —— **认不出就别动它**", () => {
    // 凭空吞掉两个字符比留着两颗星号更糟：那是在改后端写的那句话。
    expect(emphasize("三分之一 * 两倍")).toEqual([{ text: "三分之一 * 两倍", strong: false }]);
    expect(emphasize("**只开了个头")).toEqual([{ text: "**只开了个头", strong: false }]);
  });

  it("一句普通的话原样出来，一段都不多切", () => {
    expect(emphasize("回复完成。")).toEqual([{ text: "回复完成。", strong: false }]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 一轮跑到一半时屏幕上有什么（ADR 0024 第二刀）
// ══════════════════════════════════════════════════════════════════════════

/** 真事件的底子。**从后端 dump 的那串帧里解出来**，不是手写一个我以为的形状——
 *  少一个字段、多一个字段，这儿立刻跟着变。 */
const REAL: ChatTurnEvent[] = fixtures.chatTurnEvents
  .filter((frame) => frame.startsWith("event: turn"))
  .map((frame) => JSON.parse(frame.split("\ndata: ")[1]) as ChatTurnEvent);

const real = (kind: ChatTurnEvent["kind"]): ChatTurnEvent => {
  const found = REAL.find((e) => e.kind === kind);
  if (!found) throw new Error(`真 dump 里没有 ${kind} —— 换个底子，别手写一个`);
  return found;
};

/** 后端那次跑不出来的那几种（起草的桩不流式，所以 dump 里没有 `draft_delta`）。
 *  **底子仍然是真的**：拿同一轮里那条真 `draft_started` 改 `kind` 和 `text`，
 *  字段集合因此永远等于后端今天发出去的那一份。 */
const from = (base: ChatTurnEvent, over: Partial<ChatTurnEvent>): ChatTurnEvent => ({
  ...base,
  ...over,
});

const fold = (events: ChatTurnEvent[]) =>
  events.reduce((acc, event) => applyTurnEvent(acc, event, "zh"), NO_PROGRESS);

/** 屏幕上那几行里某一种的字，按到达顺序。 */
const texts = (after: TurnProgress, kind: "step" | "said") =>
  after.lines.flatMap((line) => (line.kind === kind ? [line.text] : []));

describe("跑到一半：它在做什么", () => {
  it("真跑的那一轮 —— 每一件事一行，**措辞全是后端那句**", () => {
    const after = fold(REAL);
    expect(texts(after, "step")).toEqual(
      REAL.filter((e) => e.kind === "tool_started" || e.kind === "tool_finished").map(
        (e) => e.said_to_author,
      ),
    );
    // 它说的那整段话单独一档（不是逐字拼出来的，见下面那条）。
    expect(texts(after, "said")).toEqual([real("reply_text").text]);
  });

  it("**说的和做的按到达顺序排在一起** —— 它先说「我去翻」再去翻，分两堆摆就成了先翻后说", () => {
    // 2026-09-12 起这几行直接排在对话里（不再框在一张卡里），顺序就是内容。
    // 真跑的顺序（后端 `loop.py`：叫工具那一步说的话先喊，再喊 `tool_started`）。
    const started = real("tool_started");
    const finished = real("tool_finished");
    const said = real("reply_text");
    const after = fold([
      from(said, { text: "我先翻一下目录。" }),
      started,
      finished,
      from(said, { text: "翻完了，这章是结局。" }),
    ]);
    expect(after.lines).toEqual([
      { kind: "said", text: "我先翻一下目录。" },
      { kind: "step", text: started.said_to_author },
      { kind: "step", text: finished.said_to_author },
      { kind: "said", text: "翻完了，这章是结局。" },
    ]);
  });

  it("**`reply_delta` 一个字都不拼** —— 回话那一档今天不逐字，装成逐字就是编节奏", () => {
    // 回话的输出预算远在流式阈值之下 ⇒ `plan.stream is False` ⇒ 这条事件今天不响。
    // 万一它响了（谁把预算抬过阈值），这块屏幕也不许拿它假装打字机：
    // 真正到手的整段话走 `reply_text`，而那一条是真的。
    const deltas = [
      from(real("reply_text"), { kind: "reply_delta", text: "血" }),
      from(real("reply_text"), { kind: "reply_delta", text: "脉" }),
    ];
    expect(fold(deltas)).toEqual(NO_PROGRESS);
  });

  it("**`turn_stopped` 不画** —— 那句话回执上有一份，同一个出处", () => {
    expect(fold([real("turn_stopped")]).lines).toEqual([]);
  });

  it("**作者中途那句进了对话就排在它读到的位置**（`author_said`，2026-09-12）", () => {
    const said = real("reply_text");
    const after = fold([
      from(said, { text: "我先翻一下目录。" }),
      from(said, { kind: "author_said", text: "顺便看看第 2 章" }),
      from(said, { text: "好，第 2 章也看了。" }),
    ]);
    expect(after.lines).toEqual([
      { kind: "said", text: "我先翻一下目录。" },
      { kind: "author", text: "顺便看看第 2 章" },
      { kind: "said", text: "好，第 2 章也看了。" },
    ]);
  });

  it("工具查到了什么进不来 —— 这一层读的只有 `said_to_author` 和 `text`", () => {
    const after = fold(REAL);
    const screen = [...texts(after, "step"), ...texts(after, "said")].join("\n");
    // 工具名是机器码，它在事件上（界面要分派用），但一个字都不该到屏幕上。
    expect(texts(after, "step").join()).not.toContain("scene_constraints");
    expect(screen).not.toContain("must_not_reveal");
  });
});

describe("跑到一半：稿子真的一个字一个字长出来", () => {
  const started = real("draft_started");
  const delta = (text: string, stream = started.stream) =>
    from(started, { kind: "draft_delta", text, stream, said_to_author: "" });

  it("片接着片接上去", () => {
    const after = fold([started, delta("风雪落在"), delta("肩上，")]);
    expect(after.drafts).toEqual([
      { stream: started.stream, chapter: started.chapter, text: "风雪落在肩上，", done: "" },
    ]);
  });

  it("**一批三稿是同时在飞的，按 `stream` 分格** —— 交错到达也不许混成一坨", () => {
    // 同一章的三稿连章号都一样：没有这个数就没法把它们分开摆（后端那个字段的
    // docstring 写着这条）。而「交错到达」这件事鼠标点不出来，只有这儿点得出来。
    const after = fold([
      from(started, { stream: 1 }),
      from(started, { stream: 2 }),
      delta("甲一", 1),
      delta("乙一", 2),
      delta("甲二", 1),
    ]);
    expect(after.drafts.map((d) => [d.stream, d.text])).toEqual([
      [1, "甲一甲二"],
      [2, "乙一"],
    ]);
  });

  it("收场那句话照抄后端 —— **半截的那一稿不许说成写好了**", () => {
    const kept = real("draft_kept");
    const after = fold([from(started, { stream: kept.stream }), delta("半句", kept.stream), kept]);
    expect(after.drafts[0].done).toBe(kept.said_to_author);
    expect(after.drafts[0].text).toBe("半句");
  });

  it("**开跑那一声掉了也要开一格** —— 少一稿而屏幕不说，是这个仓库最怕的那种", () => {
    const after = fold([delta("凭空来的一片", 7)]);
    expect(after.drafts).toEqual([
      { stream: 7, chapter: started.chapter, text: "凭空来的一片", done: "" },
    ]);
  });

  it("收尾那一声先到（开跑那声掉了）也要摆出来，而不是静静地少一格", () => {
    const failed = from(real("draft_started"), {
      kind: "draft_failed",
      stream: 9,
      said_to_author: "第 2 章起草未成功。",
    });
    expect(fold([failed]).drafts[0].done).toBe(failed.said_to_author);
  });
});

describe("它停下来问了一句", () => {
  it("问句和选项原样收下 —— **这一层一个字都不加**", () => {
    const asked = from(real("turn_stopped"), {
      kind: "asked_author",
      said_to_author: "写作助手提出了一个问题，等待回答。",
      reason: null,
      asked: { question: "这一场让萧决知道吗？", options: ["让他知道", "先瞒着"] },
    });
    const after = fold([asked]);
    expect(after.asked).toEqual(asked.asked);
    // 问句进的是那张卡，而「有人在等你」那半句仍然进进度行 —— 两件事。
    expect(texts(after, "step")).toEqual([asked.said_to_author]);
  });
});
