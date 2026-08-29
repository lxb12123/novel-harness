// 屏幕上的时间怎么写。**全仓一份。**
//
// 它原来是 `ActivityLog.tsx` 里的一个私有函数。写作助手的会话列表要的是同一件事
// （「三个月前那一段是哪一段」），而两份日期格式化会在第一次有人改 `hour12` 的那天
// 分叉——同 `backendMessages.ts::EDGE_LABEL` 那条：**一张表一份，第二份拷贝就是
// 第二份措辞源**。

import type { Language } from "./language";

/** 界面语言 → `toLocaleString` 的 locale。
 *
 *  ⚠️ **这是格式问题，不是翻译问题**：这儿从来没有中文字符串要挑，只有
 *  「日期该按哪种习惯摆」跟着界面语言走——原来硬编码 `"zh-CN"`，不看界面语言，
 *  国际化第四批·前端文案批次顺手带上。扫「中文字符串」的人扫不到这一类，
 *  但它管的是同一件事：坐在屏幕前的人怎么读。 */
const LOCALE: Record<Language, string> = { zh: "zh-CN", en: "en-US" };

/** 后端给的时间戳 → 作者读得懂的一行。
 *
 *  **解析不出来就返回空串，不返回「无效日期」也不返回原文。**
 *  原文是机器格式（契约夹具里那个占位符就是一例），把它摆上屏正是这个仓库
 *  反复在修的「认不出就原样回吐」。 */
export function shownTime(ts: string, language: Language): string {
  const at = Date.parse(ts);
  if (Number.isNaN(at)) return "";
  return new Date(at).toLocaleString(LOCALE[language], { hour12: false });
}
