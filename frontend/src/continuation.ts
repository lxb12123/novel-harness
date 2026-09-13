// 行内续写的**触发策略**（ADR 0015）。纯函数，不碰 CodeMirror、不碰网络——
// 因为「什么时候该去问模型」是这件事里唯一会花钱花错的判断，它必须能被单测钉住。
//
// 作者担心的「推理赶不上删改」在续写这条路上**不是增量失效问题，是取消问题**：
// 停手 `IDLE_MS` 才发一次，一敲键就把在飞的那次丢掉。没有状态要维护。

/** 停手多久才去问。太短 = 边打字边烧钱、建议追着光标乱跳；太长 = 感觉不到它在。
 *  400ms 试下来太急——正常打字的换气停顿都会踩到，逐口吃建议时也一样：每按一次 →
 *  文档都算「变了」，1 秒内不再动手才重新问，一直点着吃不会被打断。 */
export const IDLE_MS = 1000;

export interface SuggestSignal {
  /** 光标前的正文。 */
  before: string;
  /** 作者正选着一段文字（选中时他在做别的事，不是在往下写）。 */
  hasSelection: boolean;
  /** 已经有一条建议挂在屏幕上。 */
  hasSuggestion: boolean;
  /** 编辑器里这一章还没打开（正文没加载完）。 */
  loading?: boolean;
  /** 写作助手开着（novel-agent 模式，`useCoords.chatOpen`）。 */
  assistantOpen?: boolean;
  /** 模型服务配好了没有（`settings.model_configured`）。**`false` 才拦**：设置还没回来时
   *  是 `undefined`，那时不拦——配好的作者不该因为设置慢了一拍就少一次续写。 */
  modelConfigured?: boolean;
  /** 设置里「是否在 novel-agent 模式下续写」那颗开关（`settings.continuation_in_agent_mode`）。
   *  设置还没回来时它是 `undefined`——按关着算，默认本来就是关。 */
  continuationInAgentMode?: boolean;
}

/**
 * 该不该去问模型。**默认是「不问」**——每一次问都是作者自己的钱（BYOK）。
 *
 * 五条不问的理由，每条都是真实会发生的场景：
 * - 光标前没有实质内容：空章 / 刚开头，问了也只能瞎编
 * - 有选区：作者在选、在删、在查，不是在往下写
 * - 已经有建议挂着：再问一次是覆盖自己，纯浪费
 * - 正文还没加载完：拿到的上文是空的或是上一章的
 * - 写作助手开着、而作者没在设置里放行：**模式二默认没有续写**（作者 2026-09-10 定的，
 *   起因是助手开着、正文里还在往下冒灰字）。「模式」在代码里只是一块布局，编辑器本来
 *   不知道面板开没开——所以这条得在这儿明写，不能指望别处替它拦
 */
export function shouldSuggest(signal: SuggestSignal): boolean {
  if (signal.loading) return false;
  // 模型服务没配好就不问：每停一下笔就发一次必败的请求，换来的只有一次次静默的失败。
  // 该说这件事的是顶栏那盏灰灯（`TopBar.tsx::StatusLight`），不是编辑器。
  if (signal.modelConfigured === false) return false;
  if (signal.hasSelection) return false;
  if (signal.hasSuggestion) return false;
  if (signal.assistantOpen && !signal.continuationInAgentMode) return false;
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

/** 光标**后面**那截同章正文——作者跳回去改旧章时，那是**已经写好的**几千字。
 *
 *  今天续写只给光标之前的；模型看不见后面那段，写出来的一句就可能跟紧接着的下一段
 *  接不上，或者干脆把它重写一遍。后端把它渲染成【下文】块，并在块首写死
 *  「别重写、要能接上」（`draft/product_assemble.py`）——**这句话必须在后端**，
 *  和续写的提示语是常量同一个理由（前端能传的东西作者就能改）。
 *
 *  ⚠️ 和 `tailBefore` 共用**同一个** `limit`（后端那一份公式算出来的），这里同样
 *  不许出现第二个上限的字面量。两刀朝着光标切：上文留末尾，下文留开头。
 *
 *  后端还会再截一次（同一个额度）。两头都截不是重复：这一头省的是请求体，
 *  那一头是**不信客户端**——`following_text` 是个自由字符串入口。 */
export function tailAfter(doc: string, pos: number, limit: number): string {
  const after = doc.slice(Math.max(0, Math.min(pos, doc.length)));
  const points = Array.from(after);
  return points.length <= limit ? after : points.slice(0, limit).join("");
}

/** 建议文本里「下一口」该吞多长——方向键逐口接受用（`ghostText.ts` 的 `acceptSuggestionChunk`）。
 *
 *  用 `Intl.Segmenter({granularity:"word"})` 而不是按空格/固定字数切：中文没有空格，
 *  固定字数会把词切断，而 `Intl.Segmenter` 是浏览器内置的机械分词（ICU 词典），
 *  中英文同一套代码，不是本仓库自己去猜「这算不算一个词」的语义判断。
 *
 *  一口 = 一个词，外加它前后粘着的标点/空白（下一个词开始前为止）——这样标点不会
 *  单独占一次按键，句尾的「，」「。」跟着前一个词一起落地。 */
export function nextChunkLength(text: string): number {
  if (!text) return 0;
  const segments = Array.from(new Intl.Segmenter(undefined, { granularity: "word" }).segment(text));
  let end = 0;
  let haveWord = false;
  for (const s of segments) {
    if (s.isWordLike && haveWord) break;
    end = s.index + s.segment.length;
    if (s.isWordLike) haveWord = true;
  }
  return end || text.length;
}

/** 模型返回的那一段清理成能直接插进正文的样子。
 *
 *  模型偶尔会把上文的最后一句重复一遍再往下写；也常带首尾空行。这里只做**无损的**
 *  修剪（去首尾空白），**不做去重**——去重要判断「这两句是不是同一句」，那是语义判断。 */
export function cleanSuggestion(text: string): string {
  return text.replace(/^[\s　]+/, "").replace(/[\s　]+$/, "");
}
