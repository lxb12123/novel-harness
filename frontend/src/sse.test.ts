import { describe, expect, it } from "vitest";
import fixtures from "./__fixtures__/api.json";
import { SseDecoder } from "./sse";

// 解帧那一小段（`sse.ts`）。**这份文件里唯一重要的一条是最后那个 describe**：
// 拿**后端真的吐出来的那串字节**（`chatTurnEvents`，`tests/test_frontend_contract.py`
// 从真 app dump 的）喂给这个解码器。前面那些是分片、注释、换行这些
// 「网络会怎么折腾我们」的形状，它们造不出真字节，只能自己写。

describe("分片不在我们手上", () => {
  it("一帧被劈成两半也解得出来 —— **这是它存在的全部理由**", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("event: turn\nda")).toEqual([]);
    expect(decoder.push('ta: {"kind":"reply_text"}\n\n')).toEqual([
      { event: "turn", data: '{"kind":"reply_text"}' },
    ]);
  });

  it("一个 chunk 里塞了三帧，三帧都出来", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push("event: a\ndata: 1\n\nevent: b\ndata: 2\n\nevent: c\ndata: 3\n\n");
    expect(frames.map((f) => f.event)).toEqual(["a", "b", "c"]);
  });

  it("**半帧不是一帧** —— 流断在中间时那半截一律丢掉，不许猜后半截", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("event: turn\ndata: {\"kind\"")).toEqual([]);
    expect(decoder.pending).toContain("event: turn");
  });

  it("注释帧（keep-alive）**必须原样忽略**", () => {
    // 服务端闲着的时候会发 `: ping`。当成数据的话，屏幕上会多出一条谁也读不懂的空事件。
    const decoder = new SseDecoder();
    expect(decoder.push(": ping\n\n")).toEqual([]);
    expect(decoder.push("event: turn\ndata: 1\n\n")).toHaveLength(1);
  });

  it("多行 `data:` 按换行拼回去，冒号后头**只有第一个空格**是分隔符", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("event: turn\ndata: 第一行\ndata:  带空格\n\n")).toEqual([
      { event: "turn", data: "第一行\n 带空格" },
    ]);
  });

  it("没有 `event:` 的帧按规范叫 `message`", () => {
    expect(new SseDecoder().push("data: 1\n\n")).toEqual([{ event: "message", data: "1" }]);
  });

  it("`\\r\\n` 和 `\\r` 都认（规范列了三种换行）", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("event: turn\r\ndata: 1\r\n\r\n")).toEqual([
      { event: "turn", data: "1" },
    ]);
  });
});

describe("拿真字节喂它", () => {
  it("**后端 dump 出来的那一整轮**逐帧解得开，最后一帧是回执", () => {
    // 这份夹具是 `tests/test_frontend_contract.py` 从真 app 抓的原始响应体
    // （连 `event:` / `data:` / 空行都是真的）。手写一份「我以为它长这样」的帧
    // 正是这条缝原本的病 —— 两份手写的东西互相验证。
    const decoder = new SseDecoder();
    const frames = decoder.push(fixtures.chatTurnEvents.join(""));
    expect(decoder.pending).toBe("");
    expect(frames.length).toBe(fixtures.chatTurnEvents.length);
    expect(frames.at(-1)!.event).toBe("receipt");
    // 中间那些帧全是 `turn`，而且每一条都 parse 得出来。
    for (const frame of frames.slice(0, -1)) {
      expect(frame.event).toBe("turn");
      expect(JSON.parse(frame.data)).toHaveProperty("kind");
    }
  });

  it("**一次一个字节地喂它，结果一模一样** —— 分片怎么切都不该改变解出来的东西", () => {
    const raw = fixtures.chatTurnEvents.join("");
    const decoder = new SseDecoder();
    const frames = [...raw].flatMap((ch) => decoder.push(ch));
    expect(frames).toEqual(new SseDecoder().push(raw));
  });
});
