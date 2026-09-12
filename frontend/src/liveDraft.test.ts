import { describe, expect, it } from "vitest";
import fixtures from "./__fixtures__/api.json";
import type { ChatTurnEvent } from "./api/types";
import { liveDraftAfter, visibleBody } from "./liveDraft";

// 正在写的那一稿流进左边的编辑器（ADR 0048 第二半）：这儿钉的是那条流的**归约**和
// 「开头那句自述不画进正文」。真事件的形状来自真 dump（`chatTurnEvents`），只改 kind / text。

const frames = fixtures.chatTurnEvents as string[];
const real = (kind: string): ChatTurnEvent => {
  const frame = frames.find((f) => f.includes(`"kind":"${kind}"`));
  if (!frame) throw new Error(`真 dump 里没有 ${kind}`);
  return JSON.parse(frame.split("\ndata: ")[1]) as ChatTurnEvent;
};
const started = real("draft_started");
const delta = (text: string, stream = started.stream): ChatTurnEvent => ({
  ...started,
  kind: "draft_delta",
  text,
  stream,
  said_to_author: "",
});
const kept = real("draft_kept");

describe("编辑器里那条流", () => {
  it("开跑那一声开格，字一片片接上去，收场那一声只标 done、**不清字**", () => {
    let live = liveDraftAfter(null, started);
    expect(live).toEqual({
      chapter: started.chapter,
      stream: started.stream,
      text: "",
      done: false,
      draftId: "",
      instant: false,
    });
    live = liveDraftAfter(live, delta("风雪落在肩上，"));
    live = liveDraftAfter(live, delta("他终于抬起头。"));
    expect(live?.text).toBe("风雪落在肩上，他终于抬起头。");
    // 收场那一声带着候选表里的编号（作者按保存时随请求送回去）；字留着，编辑器接手时才放。
    expect(kept.draft_id).toMatch(/:/); // 探针：真 dump 那一声真的带着编号
    live = liveDraftAfter(live, kept);
    expect(live).toMatchObject({ text: "风雪落在肩上，他终于抬起头。", done: true, draftId: kept.draft_id });
  });

  it("**一次只跟一条流**：一批几稿同时在飞，编辑器只有一个位子，第二条留在右边", () => {
    let live = liveDraftAfter(null, started);
    const second = { ...started, stream: started.stream + 1 };
    live = liveDraftAfter(live, second);
    expect(live?.stream).toBe(started.stream);
    live = liveDraftAfter(live, delta("别的那一稿的字", second.stream));
    expect(live?.text).toBe("");
    live = liveDraftAfter(live, { ...kept, stream: second.stream });
    expect(live?.done).toBe(false);
  });

  it("改一段那条流（`revising`）：整章一片到手、直接放，不逐字露", () => {
    const live = liveDraftAfter(null, { ...started, revising: true });
    expect(live).toMatchObject({ instant: true, done: false });
    const grown = liveDraftAfter(live, delta("整章正文一片送到。"));
    expect(grown?.text).toBe("整章正文一片送到。");
  });

  it("开跑那一声掉了（网抖了一下）：第一片字自己开格，别丢", () => {
    const live = liveDraftAfter(null, delta("风雪落在肩上，"));
    expect(live).toMatchObject({ chapter: started.chapter, text: "风雪落在肩上，", done: false });
  });

  it("别的事件一概不动它", () => {
    const live = liveDraftAfter(null, started);
    expect(liveDraftAfter(live, real("tool_started"))).toBe(live);
    expect(liveDraftAfter(null, real("reply_text"))).toBeNull();
  });
});

describe("开头那句自述不画进正文", () => {
  it("引擎自己的记号 + 模型常把它写成的那几种括号，都只切**第一行**", () => {
    expect(visibleBody("〖自述〗这一版更冷。\n\n风雪落在肩上。")).toBe("风雪落在肩上。");
    expect(visibleBody("「自述」以贾环视角贯穿。\n\n夜雨初歇。")).toBe("夜雨初歇。");
    expect(visibleBody("【自述】更冷。\n风雪落在肩上。")).toBe("风雪落在肩上。");
    expect(visibleBody("\n\n（自述）更冷。\n\n风雪落在肩上。")).toBe("风雪落在肩上。");
  });

  it("第一行还没写完（没换行）时一个字都不画——不知道那句到哪儿结束", () => {
    expect(visibleBody("〖自述〗这一版")).toBe("");
    expect(visibleBody("「自述」")).toBe("");
  });

  it("不是记号开头的字原样画；行中的「自述」是散文，不动", () => {
    expect(visibleBody("风雪落在肩上。")).toBe("风雪落在肩上。");
    expect(visibleBody("风雪落在肩上。【自述】更冷。\n他抬起头。")).toBe(
      "风雪落在肩上。【自述】更冷。\n他抬起头。",
    );
    expect(visibleBody("")).toBe("");
  });
});
