// 工作台里**唯一**一条路由（哈希路由，纯逻辑 + 一个订阅 hook）。
//
// ── 为什么是哈希，为什么只有一条 ──────────────────────────────────────────
//
// 「并排比几稿」要开在**新标签页**里（作者的原话：「能不能在 agent 对话中出现一个
// 本地浏览器可以直接查看的链接」）。而工作台本来就是一个本地浏览器应用——
// `nh serve` 开的就是 localhost——所以那就是**同一个应用的另一条路由**，
// **零新基础设施**：哈希不进请求行，服务端不必多认一个路径（`api/app.py` 的 SPA 入口
// 一个字都不用改），也不必多起一个进程。
//
// 其余的「页面」（工作台 / 章节准备 / 活动记录）仍然是 `useCoords.page`，**不搬到这儿**：
// 它们是同一块屏幕的三种排布，作者按前进后退键回到上一个不是他要的东西。
// 这一条不一样——它开在另一个标签页里，**没有地址就没法开**。
//
// ── 「哪一本书」为什么不在地址里 ──────────────────────────────────────────
//
// 地址里只放章号（`#/compare/12`）：那是作者读得懂的坐标。书的内部标识
// （`project:01J…`）**一个字符都不进地址栏**——同「id 不上屏」那条，地址栏也是屏幕。
// 所以开链接的那一下顺手把「哪本书的第几章」留在本地存储里，新标签页读它。
// 读不到时**不猜**（除非这个库里只有一本书，那时没有可猜的），照实说一句。

import { useEffect, useState } from "react";

export type Route = { name: "workbench" } | { name: "compare"; chapter: number };

const COMPARE = /^#\/compare\/(\d+)$/;

/** 地址里的哈希 → 路由。**认不出的一律回工作台**：一个坏地址不该让作者看见空白页。 */
export function parseRoute(hash: string): Route {
  const hit = COMPARE.exec(hash);
  if (!hit) return { name: "workbench" };
  const chapter = Number(hit[1]);
  if (!Number.isInteger(chapter) || chapter < 1) return { name: "workbench" };
  return { name: "compare", chapter };
}

/** 这一刻在哪条路由上。地址栏被改（前进后退、手敲）也跟着变。 */
export function useHashRoute(): Route {
  const [hash, setHash] = useState(() => globalThis.location?.hash ?? "");
  useEffect(() => {
    const onHash = () => setHash(globalThis.location?.hash ?? "");
    window.addEventListener("hashchange", onHash);
    onHash(); // 首帧之后地址可能已经变过（StrictMode 会重挂一次）
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return parseRoute(hash);
}

/** 「刚才那条链接指的是哪本书的第几章」。**章号一起存**：存的这一条只对那一章成立，
 *  作者拿一个旧链接开另一章时，我们宁可说不知道，也不要拿另一本书的稿子冒充。 */
export interface CompareHandoff {
  book: string;
  chapter: number;
}

const HANDOFF_KEY = "nh.compare.v1";

export function writeCompareHandoff(handoff: CompareHandoff): void {
  try {
    globalThis.localStorage?.setItem(HANDOFF_KEY, JSON.stringify(handoff));
  } catch {
    // 隐私模式下 setItem 会抛。存不下就算了 —— 那一页会自己说「不知道是哪本书」，
    // 而不是打不开。
  }
}

export function readCompareHandoff(): CompareHandoff | null {
  try {
    const raw = globalThis.localStorage?.getItem(HANDOFF_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CompareHandoff>;
    if (typeof parsed?.book !== "string" || !parsed.book) return null;
    if (typeof parsed?.chapter !== "number" || !Number.isInteger(parsed.chapter)) return null;
    return { book: parsed.book, chapter: parsed.chapter };
  } catch {
    return null;
  }
}
