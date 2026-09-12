// 流进编辑器的字**匀速露出来**（作者 2026-09-12：「他的内容是一段一段字的走，不是一个一个的，
// 看起来就是不美观」）。
//
// 模型那头是一片一片吐的，而一片有多大不由我们定：有的服务商按 token 发，有的攒成一句、
// 一段才发；网络再把几片攒成一个包一起送到。直接把到手的字画上去，屏幕上就是一段一段跳。
// 这儿把「到手」和「露出来」分开：到手的字排着队，每一帧只露一点，像在打字。
//
// ── 速度：跟着来的速度走，不是一个写死的数 ─────────────────────────────────
//
// 露字的速度按**最近两秒来了多少字**定（`pace`）：模型每秒吐三十个字，屏幕就每秒露三十
// 几个——一片字到了之后在下一片到之前均匀铺开，中间没有「一下全出来、然后停住等」。
// 比来的速度略快一点点（× 1.15），积压才会慢慢消掉；积压超过三秒的量时额外加速，
// 否则模型一次吐一段时屏幕会越落越远，最后写完了字还在慢慢露。
// 流收场之后（`fast`）加速收尾：落盘那一版几百毫秒后就到，别让它等一句没露完的话。
//
// 这一层不知道字是什么，只知道露到第几个：**纯函数**，归约可测。

/** 一帧多长（按 60 帧算；真的帧率由 `requestAnimationFrame` 定，这儿只用来换算速度）。 */
export const FRAME_MS = 1000 / 60;
/** 最慢每帧半个字（每秒三十个字）：模型再慢，屏幕也不能慢过人读。 */
export const MIN_PACE = 0.5;
/** 算来的速度看最近多久。 */
export const PACE_WINDOW_MS = 2000;
/** 积压超过这么多帧的量就额外加速（三秒）。 */
const COMFORT_FRAMES = 180;

export interface Arrival {
  /** 到手的时刻（ms）。 */
  t: number;
  /** 这一片有几个字。 */
  n: number;
}

/** 按最近来的速度算每帧该露几个字。一片都没来过时按最慢的算。 */
export function pace(arrivals: readonly Arrival[], now: number): number {
  const recent = arrivals.filter((a) => now - a.t <= PACE_WINDOW_MS);
  if (recent.length === 0) return MIN_PACE;
  const chars = recent.reduce((sum, a) => sum + a.n, 0);
  // 第一片刚到时窗口只有一瞬：按至少半个窗口算，否则一片 600 字会算出每帧几百字。
  const span = Math.max(PACE_WINDOW_MS / 2, now - recent[0].t);
  return Math.max(MIN_PACE, (chars / span) * FRAME_MS * 1.15);
}

export interface Reveal {
  /** 露到第几个 code unit。 */
  revealed: number;
  /** 不足一个字的余额：每帧半个字时，攒两帧露一个。 */
  budget: number;
}

/** 下一帧露到哪儿。`total` 是到手的总长，`perFrame` 是这一刻的速度（`pace`）。 */
export function nextReveal(state: Reveal, total: number, perFrame: number, fast: boolean): Reveal {
  if (state.revealed >= total) return { revealed: total, budget: 0 };
  const backlog = total - state.revealed;
  let step: number;
  let budget = state.budget;
  if (fast) {
    // 收尾：每帧至少十二个字，十几帧内露完。
    step = Math.max(12, Math.ceil(backlog / 4));
    budget = 0;
  } else {
    budget += perFrame;
    const comfort = perFrame * COMFORT_FRAMES;
    if (backlog > comfort) budget += (backlog - comfort) / 10;
    step = Math.floor(budget);
    budget -= step;
  }
  const revealed = Math.min(total, state.revealed + step);
  return { revealed, budget: revealed >= total ? 0 : budget };
}

/**
 * 露出来的那一截该从哪里断：**不切在代理对中间**。中文里的生僻字、表情是两个 code unit，
 * 切在中间屏幕上是一个乱码。
 */
export function revealCut(text: string, at: number): number {
  if (at <= 0 || at >= text.length) return Math.max(0, Math.min(at, text.length));
  const code = text.charCodeAt(at - 1);
  // 高代理在前、低代理在后：断在两者之间就把它们拆开了。
  return code >= 0xd800 && code <= 0xdbff ? at - 1 : at;
}
