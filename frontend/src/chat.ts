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
import type {
  ChatAuthorQuestion,
  ChatMessageView,
  ChatSpeaker,
  ChatTurnEvent,
  TurnReceipt,
} from "./api/types";
import { saidToTheAuthor } from "./correctionError";

/** 屏幕上认得的说话人**就是这张表的键**。
 *
 *  它同时是措辞表和白名单，**故意合并成一份**：分开写的那一刻，「谁能上屏」
 *  和「上屏了叫什么」就成了两份会漂的清单，而漂开的方向是 fail-open 那一侧
 *  （认不出名字仍然把正文画出来，见 `visibleMessages`）。
 *
 *  ── 第三行「系统」是 2026-08-13 有意加的（后端迁移 012）────────────────────
 *
 *  作者第一次真用就撞到：那一轮在发出去之前就死了（他那台机器到端点的 TLS 全断），
 *  屏幕上确实弹过一句提醒，可它活在组件状态里——组件一卸载、他再发一句就没了。
 *  他的原话：「有提醒文字，但是过一会文字消失了，**没有必要消失**。」
 *
 *  所以那句话现在**落在库里、跟着对话一起回来**，而这一行就是它上屏的许可证
 *  （`visibleMessages` 的 docstring 早写着：真要新增一种说话人，得先往这儿加一行，
 *  那是一次有意的改动）。**措辞仍然全在后端**：这儿加的只是「谁在说」那三个字。 */
export const SPEAKER_ZH: Record<ChatSpeaker, string> = {
  author: "你",
  assistant: "写作助手",
  system: "系统",
};

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
 * 一轮跑完之后，回执上那句话还要不要画。**`null` = 不画，它已经在对话里了。**
 *
 * ── 判据是结构，不是拿两串字去比 ────────────────────────────────────────────
 *
 * 这一轮什么都没跑出来时，后端把「为什么」**落进了库**（`messages` 末尾那条
 * `system`，后端迁移 012），而回执上那句 `message` 是同一串字。两处一起画，
 * 作者会在同一块屏幕上把同一句话读两遍——`applyTurnEvent` 拒绝 `turn_stopped`
 * 用的是同一条理由。
 *
 * 判据只能是「这一轮有没有留下一条 `system`」：拿 `message` 去和最后一条比字面
 * 就是**从字符串反推**，而这个仓库为那种写法栽过两次（`activity.py` 那条
 * 「别从 label 的措辞去分辨」、前端那条「拿屏幕上的人名自己去凑」）。
 */
export function receiptSays(receipt: TurnReceipt): string | null {
  return receipt.messages.some((m) => m.speaker === "system") ? null : receipt.message;
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
  if (c.compressed_blocks > 0) {
    notes.push(
      `更早的 ${c.compressed_blocks} 块对话被压成了摘要，它读到的是概括；` +
        `你的原话一句没删、还在上面，需要时它会按编号取回。`,
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

// ══════════════════════════════════════════════════════════════════════════
// 跑到一半时屏幕上有什么（ADR 0024 决策一）
// ══════════════════════════════════════════════════════════════════════════

/** 正在写的（或者刚写完的）一稿。**按 `stream` 分组**——一批三稿是同时在写的，
 *  三条流的片会交错着到达，而同一章的三稿连章号都一样。 */
export interface LiveDraft {
  stream: number;
  chapter: number;
  /** 已经到手的字，**原样**。这是全屏幕唯一一处真的逐字长出来的东西。 */
  text: string;
  /** 空 = 还在写；非空 = 后端说的那句收尾（写好了 / 停在这儿了 / 没写成）。 */
  done: string;
}

/** 一轮跑到这一刻，屏幕上该有的全部东西。 */
export interface TurnProgress {
  /** 它做过的每一件事，按到达顺序。**措辞全是后端的 `said_to_author`**，这里不翻。 */
  steps: string[];
  /** 它这一路说过的那几整段话。**不是逐字**（见 `applyTurnEvent` 里那段）。 */
  said: string[];
  drafts: LiveDraft[];
  asked: ChatAuthorQuestion | null;
}

export const NO_PROGRESS: TurnProgress = { steps: [], said: [], drafts: [], asked: null };

/**
 * 收到一条事件之后，屏幕上该变成什么样。**纯函数**，这样「一批三稿交错着到达」
 * 这种没法用鼠标复现的情形能被单测钉死。
 *
 * ── 三件这一层必须做对的事（ADR 0024 的三条诚实）───────────────────────────
 *
 * 1. **逐字的只有稿子。** `draft_delta` 一片一片接上去，作者真的看着它长。
 * 2. **回话那一档不逐字，所以这里不装。** `reply_delta` 今天在产品上不响
 *    （回话的输出预算远在流式阈值之下 ⇒ `plan.stream is False`，后端那条
 *    `test_todays_agent_call_is_not_streaming_…` 钉着它），而**给它做一个假的
 *    打字机 = 让作者按一个编出来的节奏判断它卡没卡住**。所以这条事件到手也不拼字：
 *    真正到手的整段话走 `reply_text`。
 * 3. **工具在干什么可以显示，工具查到了什么不许显示。** 这里读的只有
 *    `said_to_author`（引擎写的中文）和 `text`（模型自己的字）——
 *    `tool` / `kind` / `reason` 一个字都没往 `steps` 里放。
 */
export function applyTurnEvent(prev: TurnProgress, event: ChatTurnEvent): TurnProgress {
  switch (event.kind) {
    case "reply_delta":
      // 见上面第 2 条。**它不是被忘了，是被拒了。**
      return prev;
    case "turn_stopped":
      // 「为什么停」那句话回执上有一份，而且是同一个出处（后端 `stop_wording()`）。
      // 在这儿再画一遍 = 同一句话在同一块屏幕上出现两次。
      return prev;
    case "reply_text":
      return event.text ? { ...prev, said: [...prev.said, event.text] } : prev;
    case "asked_author":
      return { ...prev, asked: event.asked, steps: pushSaid(prev.steps, event) };
    case "draft_started":
      return { ...prev, drafts: [...prev.drafts, openDraft(event)] };
    case "draft_delta":
      return { ...prev, drafts: growDraft(prev.drafts, event) };
    case "draft_kept":
    case "draft_failed":
      return { ...prev, drafts: closeDraft(prev.drafts, event) };
    default:
      return { ...prev, steps: pushSaid(prev.steps, event) };
  }
}

function pushSaid(steps: string[], event: ChatTurnEvent): string[] {
  return event.said_to_author ? [...steps, event.said_to_author] : steps;
}

function openDraft(event: ChatTurnEvent): LiveDraft {
  return { stream: event.stream, chapter: event.chapter ?? 0, text: "", done: "" };
}

/** 一片字接到它那条流上。
 *
 *  **认不出的流也要开一格**，不许丢：`draft_started` 那一声掉了（网抖了一下、
 *  界面挂晚了一拍）而这里按「找不到就忽略」处理的话，作者会看着一批三稿里少一稿，
 *  而屏幕上没有任何东西说它少了。 */
function growDraft(drafts: LiveDraft[], event: ChatTurnEvent): LiveDraft[] {
  const found = drafts.some((d) => d.stream === event.stream);
  const grown = drafts.map((d) =>
    d.stream === event.stream ? { ...d, text: d.text + event.text } : d,
  );
  return found ? grown : [...grown, { ...openDraft(event), text: event.text }];
}

/** 一条流收场。**半截的那一稿不许说成写好了**——那句话由后端写（`draft_kept` 会说
 *  「停在这儿了，没写完」），这里只是把它放上去。 */
function closeDraft(drafts: LiveDraft[], event: ChatTurnEvent): LiveDraft[] {
  const closing = event.said_to_author || "这一条停在这儿了。";
  const found = drafts.some((d) => d.stream === event.stream);
  const closed = drafts.map((d) =>
    d.stream === event.stream ? { ...d, done: closing } : d,
  );
  return found ? closed : [...closed, { ...openDraft(event), done: closing }];
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
