// 跑一轮的那条长连接（[ADR 0024](docs/adr/0024-a-turn-is-a-conversation-not-a-black-box.md)）。
//
// **这一层只做运输**：把帧解出来、按帧名分派、最后把那份 `TurnReceipt` 交出去。
// 「屏幕上该说什么」在 `chat.ts`（纯函数），「怎么画」在 `ChatPanel.tsx`。
//
// ── 断了怎么办：退回**今天已经有的** resume，不是一个新机制 ──────────────────
//
// 流断掉的时候那一轮**还在服务端跑着**（后端有意如此：一个掉线的浏览器不该把
// 作者已经付过钱的那一轮弄崩）。所以这一层断了之后**不重连、不重发**：
// 重发 `POST …/turn/events` 会撞上 409（那段对话正在跑），而就算不撞，
// 它也会是第二轮、第二笔钱。
//
// 正确的退路是 ADR 0019 那条既有的 resume：**看尾巴、补跑缺的、继续**——
// 作者重新读一次这段对话（`GET …/chats/{id}`）就能看到已经落库的那几条，
// 侧栏上「上次断在半路」那颗徽标会亮，按「接着往下」发一句空话就接上了。
// **这里一行新代码都不该有**，有的话就是第二套恢复机制。

import { ApiError, type ApiErrorBody } from "./client";
import { SseDecoder } from "../sse";
import type { ChatTurnEvent, TurnReceipt } from "./types";

/** 长连接上那三种帧名。**封闭集合**，和后端 `api/chat.py::SSE_TURN/RECEIPT/FAILED` 同一份。 */
const FRAME_TURN = "turn";
const FRAME_RECEIPT = "receipt";
const FRAME_FAILED = "failed";

export interface TurnStreamHandlers {
  /** 每一条中间事件叫一次。**它必须便宜**：一稿正文是上千片，每片都会经过它。 */
  onEvent?: (event: ChatTurnEvent) => void;
}

/**
 * 跑一轮，边跑边喊。返回的是**最后那一帧**里的 `TurnReceipt`。
 *
 * 三种收场：
 *
 * - 拿到 `receipt` → 正常返回（和不流式那条路由的出参是同一个东西）；
 * - 拿到 `failed` → 抛 `ApiError`，**带着后端那句中文**（唯一一档：这段对话在别的
 *   窗口里刚往前走了一步，那时头已经发出去了，改不成 409 了）；
 * - 流断了、一帧 `receipt` 都没有 → 抛 `ApiError` 且**不带话**，
 *   由调用方说它自己那句「这一轮没跑成，而系统没能说清是为什么」（§10 约束 8：
 *   不知道就说不知道，**编一个理由比不说更贵**）。
 *
 * 开跑之前的拒绝（模型没配好 422 / 这段对话正在跑 409 / 这段对话不在 404）仍然是
 * 真的状态码：后端把它们全抛在第一个字节之前，所以这儿和 `client.ts::request` 一样拆。
 */
export async function runTurnStream(
  path: string,
  body: unknown,
  handlers: TurnStreamHandlers = {},
): Promise<TurnReceipt> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    // 和 `client.ts` 同一种拆法：FastAPI 把 HTTPException 详情裹进 `.detail`，
    // 自定义处理器放顶层。**这里不写第二份**，只是它拿不到 `request()` 的泛型。
    const raw = await res.json().catch(() => null);
    const detail = (raw && (raw.detail ?? raw)) || {};
    throw new ApiError(
      res.status,
      typeof detail === "object" ? (detail as ApiErrorBody) : { message: String(detail) },
    );
  }

  const decoder = new SseDecoder();
  const utf8 = new TextDecoder();
  let receipt: TurnReceipt | null = null;
  let refusal: string | null = null;

  const take = (event: string, data: string): void => {
    if (event === FRAME_TURN) {
      handlers.onEvent?.(JSON.parse(data) as ChatTurnEvent);
    } else if (event === FRAME_RECEIPT) {
      receipt = JSON.parse(data) as TurnReceipt;
    } else if (event === FRAME_FAILED) {
      refusal = (JSON.parse(data) as { message?: string }).message ?? "";
    }
    // 认不出的帧名**一律丢掉**，不报错：后端加一种帧不该让旧界面整轮崩掉，
    // 而丢掉的后果只是少显示一行（同「认不出的说话人不上屏」那条，方向一致）。
  };

  const body_ = res.body;
  if (!body_) {
    // 拿不到流（老浏览器、被中间层整段缓冲了）。**不退回一次阻塞请求**：
    // 那一轮已经在跑了，再发一次是第二笔钱。
    throw new ApiError(0, {});
  }
  const reader = body_.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    // `stream: true` 是必须的：一个 UTF-8 汉字三个字节，被 chunk 边界切开时
    // 不带它就会解出一个 `�` —— 而那正好落在作者的稿子中间。
    for (const frame of decoder.push(utf8.decode(value, { stream: true }))) {
      take(frame.event, frame.data);
    }
  }
  for (const frame of decoder.push(utf8.decode())) take(frame.event, frame.data);

  if (receipt) return receipt;
  // `refusal` 可能是空串（后端认不出那次拒绝，交了白卷）——**空串照样交给调用方**，
  // `ApiError.message` 会退回 `error` 码、而 `chat.ts::refusalText` 会退回那句兜底话。
  throw new ApiError(refusal === null ? 0 : 409, refusal ? { message: refusal } : {});
}
