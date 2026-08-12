// 屏幕上的时间怎么写。**全仓一份。**
//
// 它原来是 `ActivityLog.tsx` 里的一个私有函数。写作助手的会话列表要的是同一件事
// （「三个月前那一段是哪一段」），而两份日期格式化会在第一次有人改 `hour12` 的那天
// 分叉——同 `EDGE_ZH` 那条：**一张表一份，第二份拷贝就是第二份措辞源**。

/** 后端给的时间戳 → 作者读得懂的一行。
 *
 *  **解析不出来就返回空串，不返回「无效日期」也不返回原文。**
 *  原文是机器格式（契约夹具里那个占位符就是一例），把它摆上屏正是这个仓库
 *  反复在修的「认不出就原样回吐」。 */
export function shownTime(ts: string): string {
  const at = Date.parse(ts);
  if (Number.isNaN(at)) return "";
  return new Date(at).toLocaleString("zh-CN", { hour12: false });
}
