// 行内续写的**触发策略**（ADR 0015）。纯函数，不碰 CodeMirror、不碰网络——
// 因为「什么时候该去问模型」是这件事里唯一会花钱花错的判断，它必须能被单测钉住。
//
// 作者担心的「推理赶不上删改」在续写这条路上**不是增量失效问题，是取消问题**：
// 停手 `IDLE_MS` 才发一次，一敲键就把在飞的那次丢掉。没有状态要维护。

/** 停手多久才去问。太短 = 边打字边烧钱；太长 = 感觉不到它在。 */
export const IDLE_MS = 400;

export interface SuggestSignal {
  /** 光标前的正文。 */
  before: string;
  /** 作者正选着一段文字（选中时他在做别的事，不是在往下写）。 */
  hasSelection: boolean;
  /** 已经有一条建议挂在屏幕上。 */
  hasSuggestion: boolean;
  /** 编辑器里这一章还没打开（正文没加载完）。 */
  loading?: boolean;
}

/**
 * 该不该去问模型。**默认是「不问」**——每一次问都是作者自己的钱（BYOK）。
 *
 * 四条不问的理由，每条都是真实会发生的场景：
 * - 光标前没有实质内容：空章 / 刚开头，问了也只能瞎编
 * - 有选区：作者在选、在删、在查，不是在往下写
 * - 已经有建议挂着：再问一次是覆盖自己，纯浪费
 * - 正文还没加载完：拿到的上文是空的或是上一章的
 */
export function shouldSuggest(signal: SuggestSignal): boolean {
  if (signal.loading) return false;
  if (signal.hasSelection) return false;
  if (signal.hasSuggestion) return false;
  return signal.before.trim().length > 0;
}

/** 送给模型的上文长度上限**由后端算**，这里只写它怎么被用（ADR 0015 / ADR 0019 边界五）。
 *
 *  ⚠️ **这里不许再出现一个上限的字面量。** 2026-08-22 之前这儿写着
 *  `TAIL_LIMIT = 1000`，而后端 `draft/assemble.py::product_tail_limit()` 本来会按模型
 *  真实窗口伸缩（200k 的模型算出来是一万六千多字）——**那套设计被这一个常量整个架空了**：
 *  32k 和 1M 的模型送出去的都是 1,000 字，利用率 2%。少给上文不会有人发现，
 *  只会觉得模型忽然变笨，而这正是 `product_tail_limit` 的 docstring 点名要避免的事。
 *
 *  今天那个数跟着 `GET /api/settings` 的 `continuation_tail_limit` 过来
 *  （`CenterEditor` → `CodeEditor`）。**公式只有后端一份**，两头由
 *  `tests/test_continuation_tail_limit.py` 钉着：这两个文件里一旦冒出续写上限的
 *  数字字面量，那条 pytest 当场红。
 */
export function tailBefore(doc: string, pos: number, limit: number): string {
  const before = doc.slice(0, Math.max(0, Math.min(pos, doc.length)));
  const points = Array.from(before);
  return points.length <= limit ? before : points.slice(-limit).join("");
}

/** 模型返回的那一段清理成能直接插进正文的样子。
 *
 *  模型偶尔会把上文的最后一句重复一遍再往下写；也常带首尾空行。这里只做**无损的**
 *  修剪（去首尾空白），**不做去重**——去重要判断「这两句是不是同一句」，那是语义判断。 */
export function cleanSuggestion(text: string): string {
  return text.replace(/^[\s　]+/, "").replace(/[\s　]+$/, "");
}
