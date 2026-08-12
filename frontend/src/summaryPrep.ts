// ⚠️ **2026-08-10 起零调用方。** 它的唯一入口「AI 起草」抽屉当天被删了
// （填表式起草不留第二个入口，起草归模式二的 agent 面板）。
//
// **留着不删是有意的**：模式二的 `draft` 工具要的正是这套判断——起草前缺什么就补什么、
// 后台正在补就等而不是再付一次钱。它是纯函数 + 依赖注入 + 5 条单测，接过去是改调用方，
// 不是重写。**接上之后删掉这条注释；如果模式二最终没用它，删掉整个文件**——
// 零调用方的模块放着不管，就是下一个「文档说有其实没有」。
//
// 起草前把缺的前置资料补齐。**纯逻辑：不碰 React、不碰 fetch**，依赖从外面注入——
// 因为「哪一章要花钱补、什么时候该等后台而不是自己再付一次」是这件事里唯一会花错钱的
// 判断，它必须能被单测钉住（同 `continuation.ts` 的理由）。
//
// 这条路径**只在作者真的要起草时**跑：不预警、不提示（作者原话：不要明确返回消息给
// 用户看）。不够就当场补，并且让他看见在补什么。

import type { AutopilotStatus } from "./api/types";

/** 后台正在整理时的轮询间隔与次数上限。等不到就自己跑一遍（后端幂等，最差是白等）。 */
export const POLL_MS = 1000;
export const MAX_POLLS = 30;

export interface PrepProgress {
  /** 正在补第几章。 */
  chapter: number;
  /** 已经补完几章（不含正在补的这一章）/ 一共要补几章。 */
  done: number;
  total: number;
}

export interface PrepDeps {
  /** 问后台整理的进度。**问不到（端点还没上线 / 网络错）一律返回 null**，当作「不知道」。 */
  status: (chapter: number) => Promise<AutopilotStatus | null>;
  /** 当场生成某一章的总结（会调模型、会花钱；后端幂等）。 */
  summarize: (chapter: number) => Promise<unknown>;
  onProgress: (progress: PrepProgress) => void;
  wait: (ms: number) => Promise<void>;
}

/** 补齐失败时抛这个：作者要知道是**哪一章**没补上，否则「这一稿少了点东西」等于没说。 */
export class SummaryPrepError extends Error {
  chapter: number;
  constructor(chapter: number, cause: unknown) {
    super(cause instanceof Error ? cause.message : String(cause));
    this.name = "SummaryPrepError";
    this.chapter = chapter;
  }
}

/**
 * 把 `missing` 里的每一章挨个补上。任意一章失败就**停下并抛**——不许继续往下走成
 * 一份「悄悄少了几章记忆」的稿子，那正是作者看不出差别的那种降级。
 */
export async function ensureSummaries(missing: number[], deps: PrepDeps): Promise<void> {
  for (const [index, chapter] of missing.entries()) {
    deps.onProgress({ chapter, done: index, total: missing.length });
    try {
      let status = await deps.status(chapter);
      // 作者可能刚从这一章离开，后台正在整理它。等它跑完，别再发一次同样的付费请求。
      for (let polls = 0; status?.running && polls < MAX_POLLS; polls += 1) {
        await deps.wait(POLL_MS);
        status = await deps.status(chapter);
      }
      if (!status?.summary_ready) await deps.summarize(chapter);
    } catch (cause) {
      throw new SummaryPrepError(chapter, cause);
    }
  }
}
