// 一条 `text/event-stream` 怎么拆成帧。**纯函数，不碰 fetch 也不碰 DOM**
// （同 `chat.ts` / `continuation.ts` 的分法），好让「网络把一帧切成两半」这件事被单测钉死。
//
// ── 为什么不用 `EventSource` ─────────────────────────────────────────────
//
// `EventSource` 只会发 GET，而跑一轮要带一个请求体（章号 + 作者说的那句话 + 这一轮的
// 标识）。把它们塞进查询串就等于把作者写的一整段话放进 URL——长度有上限、会进日志、
// 而且**章号是这一轮的查询坐标**，它不该长得像一个可以被人手改的东西。
// 所以走 `fetch` + `ReadableStream`，解帧这一小段自己写。
//
// ── 这个解码器唯一存在的理由：**分片不在我们手上** ─────────────────────────
//
// TCP 想在哪儿切就在哪儿切，一帧被劈成两个 chunk 是常态而不是边角。
// 「按 chunk 解析」在本机上几乎永远是对的，然后在作者的机器上偶尔丢半帧——
// 而丢掉的那半帧的形态是：一稿写到一半停住了，屏幕上没有任何东西说它停了。

/** 一帧。`event` 缺省时按规范是 `"message"`；`data` 是多行 `data:` 用换行拼起来的原文。 */
export interface SseFrame {
  event: string;
  data: string;
}

/**
 * 增量解帧。**喂进去多少都行**（半帧、三帧、空串），吐出来的永远是完整的帧。
 *
 * 只实现这条流真的用得到的那部分规范：`event:` / `data:`（可多行）/ `:` 注释。
 * `id:` 和 `retry:` 认得出、但一律丢掉——我们不做断线自动重连
 * （断了要退回的是 resume，见 `api/turnStream.ts`，那是 ADR 0024 写死的）。
 * **不实现的那几条要写出来**，免得下一个人以为这是一份完整的 SSE 客户端。
 */
export class SseDecoder {
  private buffer = "";

  /** 收一段字节解出来的文本，吐出这一段里**已经完整**的那几帧。 */
  push(chunk: string): SseFrame[] {
    // `\r\n` 和 `\r` 都要认（规范列了三种换行）。归一成 `\n` 之后下面只有一种切法。
    this.buffer += chunk.replace(/\r\n|\r/g, "\n");
    const frames: SseFrame[] = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const frame = parseBlock(block);
      if (frame) frames.push(frame);
      boundary = this.buffer.indexOf("\n\n");
    }
    return frames;
  }

  /** 流关掉之后还剩半帧吗。**它不是一帧**——半帧一律丢掉，不许猜后半截。 */
  get pending(): string {
    return this.buffer;
  }
}

/** 一个块（两个空行之间那一段）解成一帧。**只有注释和空块返回 `null`。** */
function parseBlock(block: string): SseFrame | null {
  let event = "";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    // 注释帧（`: ping`）。keep-alive 走的就是它，**必须原样忽略**：
    // 当成数据的话，界面上会多出一条谁也读不懂的空事件。
    if (line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    // 规范：冒号后面**恰好一个**空格属于分隔符，再多的空格属于数据。
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (!event && data.length === 0) return null;
  return { event: event || "message", data: data.join("\n") };
}
