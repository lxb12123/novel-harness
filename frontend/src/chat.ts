// 写作助手面板的纯逻辑（同 `continuation.ts` / `chapterCursor.ts` 的分法）：
// **这里不碰 DOM，也不碰 fetch**，好让「一轮跑完之后屏幕上该说什么」能被单测钉死。
//
// 这一层要守的两条：
//
// 1. **措辞的唯一出处在后端**（`agent.loop.stop_wording()`）。这里不翻译停止原因，
//    也不按 `reason` 分支写文案——那会是第二份措辞源，而两份措辞一定会漂。
//    这里只做后端**说不出来**的那几句：查了几次、账少算了多少、上下文剪了什么。
// 2. **工具返回不上屏。** 一轮里查到的东西后端根本没发出来（`api/chat.py` 模块
//    docstring 第三条），所以这里能说的只有一个**数**。

import { ApiError } from "./api/client";
import type { ChatMessageView, ChatSpeaker, TurnReceipt } from "./api/types";
import { saidToTheAuthor } from "./correctionError";

/** 屏幕上认得的说话人**就是这张表的键**。
 *
 *  它同时是措辞表和白名单，**故意合并成一份**：分开写的那一刻，「谁能上屏」
 *  和「上屏了叫什么」就成了两份会漂的清单，而漂开的方向是 fail-open 那一侧
 *  （认不出名字仍然把正文画出来，见 `visibleMessages`）。 */
export const SPEAKER_ZH: Record<ChatSpeaker, string> = { author: "你", assistant: "写作助手" };

/**
 * 屏幕上放得出去的那几条。**认不出的说话人一律丢掉，不是原样摆出去。**
 *
 * 今天后端的投影是对的（`api/chat.py::_visible` 只放 `USER` / 有话的 `ASSISTANT`
 * 出来，工具返回一条都不发）。这一层是**第二道**，理由是 ADR 0019 边界一那句：
 * 「一旦某个工具把 `Node` 交出去过，那段秘密就已经在作者的持久化对话里了，
 * 改代码不会把它删掉」——屏幕是那条链的最后一厘米，而它今天是敞开的：
 * `SPEAKER_ZH[speaker]` 认不出来只会让名字变成空，**正文照画**。
 * 那一条工具返回是 `model_dump_json()` 出来的内部模型，里头是 `secret:01J…`
 * 加一串 snake_case 字段名，外加作者写在秘密节点上的 `twist`。
 *
 * **丢掉而不是显示**是这个仓库一贯的那一侧：系统不确定时的默认动作是闭嘴。
 * 真要新增一种说话人，得先往 `SPEAKER_ZH` 里加一行——那是一次有意的改动。
 */
export function visibleMessages(
  messages: readonly ChatMessageView[] | undefined,
): ChatMessageView[] {
  return (messages ?? []).filter((m) => m.speaker in SPEAKER_ZH);
}

/**
 * 一次被拒绝之后，错误框里该出现的那句话。
 *
 * **`error.message` 不许直接上屏。** `ApiError` 在后端没写 `message` 时退回
 * `body.error`，而写作助手那几条路由的 404 恰恰只有码没有话
 * （真 app 打出来的原样：`{"detail":{"error":"chat_not_found","chat_id":…}}`，
 * `project_not_found` 同理）——于是小说作者看到的是一串下划线英文。
 *
 * **有话就照说，一个字不改**（措辞的源在后端，这里不做第二份，更不做码 → 中文的
 * 映射表：那张表被删过一次，理由写在 `correctionError.ts` 顶上）。
 * 一句话都没有的时候才轮到调用方给的 `fallback`，而它**不许编一个理由**
 * （§10 约束 8：不知道就说不知道）。
 */
export function refusalText(error: unknown, fallback: string): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return saidToTheAuthor(error) ?? fallback;
  // 根本没打到后端（网断了 / 服务没起来）。**也要说一句**：一次没有任何反馈的
  // 失败，在这块屏幕上和「它还在想」长得一模一样。
  return fallback;
}

/** 一屏默认显示最后多少条。
 *
 *  后端的详情端点给的是**整段**（canonical 只增不改，没有分页端点），一段跑了三个月的
 *  对话可能有几千条。**不许因此不渲染早先那些**——那等于悄悄丢掉作者的历史；
 *  只是默认收起来，点一下就全在。 */
export const CHAT_TAIL = 30;

export interface ChatWindow {
  /** 收起来的条数。0 = 全在屏幕上。 */
  hidden: number;
  shown: ChatMessageView[];
}

/** 长对话只渲染尾巴。`expanded` = 作者已经点过「看更早的」。 */
export function tailWindow(
  messages: ChatMessageView[],
  expanded: boolean,
  tail: number = CHAT_TAIL,
): ChatWindow {
  if (expanded || messages.length <= tail) return { hidden: 0, shown: messages };
  return { hidden: messages.length - tail, shown: messages.slice(-tail) };
}

/** 一轮跑了多久。**只说秒和分**：一轮跑到小时是故障不是形态，那时该说的是别的话。 */
export function elapsedText(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `已经 ${total} 秒`;
  const s = total % 60;
  return `已经 ${Math.floor(total / 60)} 分 ${s < 10 ? "0" : ""}${s} 秒`;
}

/**
 * 回执上那几句「这一轮实际发生了什么」。
 *
 * **零不写。** 一排「查了 0 次 / 裁掉 0 条 / 0 次没量准」是噪音，而它会把真正非零的
 * 那一行淹掉。反过来：**非零的每一条都必须带着一句能读懂的理由**（§10 约束 8）——
 * 尤其是 `calls_without_usage`，它一非零，上面那个 token 数就是**低估**，
 * 而一个自称是全部的低估数字正是这个仓库反复在修的失败形态。
 */
export function receiptNotes(receipt: TurnReceipt): string[] {
  const notes: string[] = [];
  const c = receipt.context;
  if (receipt.lookups > 0) {
    // 查了什么不说 —— 后端根本没发出来（工具返回里是内部标识）。
    notes.push(`这一轮它查了 ${receipt.lookups} 次资料。`);
  }
  if (c.stale_lookups > 0) {
    notes.push(
      `有 ${c.stale_lookups} 处它手上那份正文你已经改过了，这一轮让它重新读了一遍。`,
    );
  }
  if (c.off_chapter > 0) {
    notes.push(
      `有 ${c.off_chapter} 条它更早查到的东西属于后面的章节，这一轮没带上——` +
        `免得拿后面的情况来判断这一章能不能说。`,
    );
  }
  const trimmed = c.trimmed_results + c.dropped_lookups + c.dropped_reasoning;
  if (trimmed > 0) {
    notes.push(
      `这段对话太长了，为了装得下，${trimmed} 条早先查到的东西被收起来了` +
        `（要用它会重新查一次）。你说过的话一句都没删。`,
    );
  }
  // **`lost_lookups` 不并进上面那一句**，虽然两者都是「它手上少了点东西」。
  // 上面那些是**这一轮为了装下主动收起来的**（收起来的重查一次就有）；这一条是
  // 上一轮**断在半路、canonical 里本来就缺的**那几步（`agent/loop.py::LOST_RESULT`，
  // 而 `pending_calls` 扫到作者发言就停，所以再也没人会去补它）。
  // 合成一句会把「有一件事它这一轮没查成」说成「它的记性被裁了」——原因和下一步都不同。
  if (c.lost_lookups > 0) {
    notes.push(
      `有 ${c.lost_lookups} 次查询上次断在半路、结果没留下，这一轮它按「没查到」往下走的。` +
        `要用到那几处，跟它说一声让它重新查。`,
    );
  }
  if (receipt.calls_without_usage > 0) {
    notes.push(
      `这一轮有 ${receipt.calls_without_usage} 次调用没报用量，所以「活动记录」里这一笔是少算的。`,
    );
  }
  return notes;
}

/**
 * 作者按了「停」，而这一轮的回执说「说完了」—— 要不要补一句。
 *
 * **这不是翻译后端那句话，是补一件后端不可能知道的事**：它那一轮本来就在最后一次调用
 * 之后结束，停没有让任何事情少发生，所以它报 `done` 是对的（后端那份已知限制里写着
 * 这一条）。但作者这一侧看到的是「我按了停，屏幕上却写说完了」——读起来像按钮坏了。
 *
 * 判据是**前端自己观察到的两件事**：那次「停」真的送达了（`stopped === true`），
 * 而这一轮的结局不是「按你的意思停下了」。两者都不成立就一个字都不加。
 */
export function stopFootnote(stopped: boolean, receipt: TurnReceipt): string | null {
  if (!stopped || receipt.reason === "author_stopped") return null;
  return "你按下停的时候，这一轮已经跑到最后一步了，所以它还是把话说完了。";
}

/**
 * 这一轮的标识。**「停」拿它认出自己要停的是哪一轮**（后端 `TurnBody.run_id`）。
 *
 * ── 为什么由这一边造 ──────────────────────────────────────────────────────
 *
 * 后端造不了：`POST …/turn` 是**跑完才回来**的，而「停」必须在那之前就能按。
 * 不比对的那一版有一个实测得出的坏序列：
 *
 *     按停 → 请求在路上 → 上一轮自己跑完了 → 作者又发一句 → 新一轮开始
 *     → 停止请求到达 → **杀掉新的那一轮**
 *
 * 于是作者看到「我刚发出去的那句话，它自己停了」，而他按的那一下是给上一轮的。
 *
 * `crypto.randomUUID` 不在的环境（老浏览器、某些 jsdom）走后面那条：**唯一性够用就行**
 * ——它只在一个进程内的一张表里比对，而同一段对话同时只可能有一轮
 * （后端 `_Running.begin` 拦着）。它**不进任何一行数据**。
 */
export function newRunId(): string {
  const uuid = globalThis.crypto?.randomUUID;
  if (typeof uuid === "function") return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface Emphasis {
  text: string;
  strong: boolean;
}

/**
 * 把后端那句话里的 `**…**` 画成重音。
 *
 * **它不是第二份措辞源**：一个字都没换，没有分支，没有按 `reason` 挑句子——
 * 只是把作者本来就该看见的强调渲染出来，而不是让他看见两颗星号。
 * （`stop_wording(CONTEXT_FULL)` 里有一对字面量 `**`；后端那一层不知道自己会被
 * 渲染成什么，所以它写 markdown 是合理的，接不接由这一层决定。）
 *
 * 只认成对的 `**`，落单的原样留着——**认不出就别动它**，凭空吞掉两个字符
 * 比留着两颗星号更糟。
 */
export function emphasize(text: string): Emphasis[] {
  const out: Emphasis[] = [];
  let rest = text;
  while (rest.length > 0) {
    const open = rest.indexOf("**");
    if (open < 0) break;
    const close = rest.indexOf("**", open + 2);
    if (close < 0) break;
    if (open > 0) out.push({ text: rest.slice(0, open), strong: false });
    out.push({ text: rest.slice(open + 2, close), strong: true });
    rest = rest.slice(close + 2);
  }
  if (rest.length > 0) out.push({ text: rest, strong: false });
  return out;
}
