// 「桌上摆着的那几稿」这块屏幕的纯逻辑（同 `chat.ts` / `layout.ts` 的分法）：
// **这里不碰 DOM，也不碰 fetch**，好让「三档怎么切」「默认摊开哪一版」能被单测钉死。
//
// 规格是 [ADR 0022](docs/adr/0022-drafting-is-a-proposal-not-a-write.md)。这一层守三条：
//
// 1. **不排名、不打分、不挑。** 后端不给散文打分（ADR 0005），这一层更不许——
//    它连正文都没有，只有一段 120 字的开头。**「推荐位」的唯一判据是 `landed`**：
//    助手把哪一版写进了书，那是一个**动作**不是一句评价。一批都没落盘时
//    **两边都不挑**（同 `AmbiguousName`：两个方向都贵就摊开）。
// 2. **顺序照后端给的**（`(chapter, ordinal)`）。并发跑的稿子谁先回来是随机的，
//    在这儿重排一次，作者每次刷新看到的次序就会变。
// 3. **屏幕上说「第 N 稿」，不说 id。** id 是 `draft:01J…` 形状，
//    `src/test/screenGuard.ts` 第三张网认的就是它。

import type { DraftCandidateView } from "./api/types";
import type { Language } from "./language";

/** 一列正文要读得下去的最窄宽度。
 *
 *  低于它就不该并排——三条 180px 的竖缝，每行塞不下十个字，比一张一张摊开更难读。
 *  **这不是断点，是「一列」的下限**：并排几列由它和实际宽度一起决定，
 *  所以作者把面板拖到多宽都不会出现「挤成缝」的那一档（同 `layout.ts` 那三条下限）。 */
export const COLUMN_MIN_PX = 260;

/**
 * 这一批稿子该并排摆，还是一张一张摞着。
 *
 * **作者的原话是「拉长到一定的宽度就三窗口并排」**，所以判据是**实际量到的宽度**，
 * 不是窗口大小、不是一个设置项——他拖对话面板那一下就是他的意图，界面跟着变。
 *
 * `width` 量不到（首帧 / jsdom 里 `clientWidth` 恒为 0）时**回窄档**：
 * 窄档是默认形态，猜错了只是少并排一次；反过来猜错则是把三章正文硬摊在一条缝里。
 */
export function sideBySide(width: number, count: number): boolean {
  if (count < 2 || !Number.isFinite(width)) return false;
  return width >= count * COLUMN_MIN_PX;
}

/**
 * 默认摊开哪几版。
 *
 * **判据只有 `landed`**（ADR 0022 的入口形态：「推荐那一版摊开 + 另两版各一句自述」）。
 * 一批都没落盘时返回空——**那时后端没挑，这一层也不挑**，全部收着，作者自己点。
 *
 * 为什么不默认全摊开：ADR 0022 的「代价」第三条——作者要读三章才能做一个决定，
 * 而三章正文硬摊在入口上，他连「哪一版是哪一版」都还没分清。
 */
export function openByDefault(drafts: readonly DraftCandidateView[]): string[] {
  return drafts.filter((d) => d.landed).map((d) => d.id);
}

/** 并排比那一页上，默认摊开几列。
 *
 *  作者要的是「三窗口分别对应三个原文」，所以是 3。**这个数不是省事，是价格**：
 *  一列 = 一整章正文（一次 `GET …/drafts/{id}`），而那一页列出的是这一章**桌上所有**
 *  的稿子——摊开的那几列之外还可能有更早的几稿，它们收着，点一下才取。 */
export const COMPARE_OPEN_MAX = 3;

/** 并排比那一页默认摊开哪几列：**最近的那几稿**（后端给的顺序就是最近在前）。
 *
 *  这儿和 `openByDefault` 的判据**有意不同**：那一档是对话里的入口（不许替作者挑，
 *  所以只认 `landed`），这一页是作者**专门点开来并排读的**——他要的就是摊开。 */
export function openOnCompare(drafts: readonly DraftCandidateView[]): string[] {
  return drafts.slice(0, COMPARE_OPEN_MAX).map((d) => d.id);
}

/** 「第 N 稿」。**作者认得的是这个数**（`ordinal`），不是那一串内部标识。 */
export function draftLabel(draft: DraftCandidateView, language: Language): string {
  return language === "zh" ? `第 ${draft.ordinal} 稿` : `Draft ${draft.ordinal}`;
}

/** 千位分隔。**手写而不是 `toLocaleString`**：那一个的结果跟着运行环境的区域设置变，
 *  而这儿要的是一个每台机器上都一样的字符串（测试里也要能钉住）。 */
function grouped(n: number): string {
  const digits = String(Math.max(0, Math.trunc(n)));
  let out = "";
  for (let i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 === 0) out += ",";
    out += digits[i];
  }
  return out;
}

/**
 * 「约 2,800 字」。
 *
 * ⚠️ **「字」/「characters」这个量词今天是对的，但它是钉在后端一个常量上的**：
 * 起草台恒用中文档（`agent/drafting.py::AGENT_DRAFT_LENGTH` = 中文默认长度，
 * `units` 数的是非空白字符）。那一天起草跟着**书的语言**走了（英文数的是词），
 * **这一行就开始骗人，而没有任何东西会拦住它**——候选那份出参上没有语言这一列，
 * 前端推不出来。**这条警告跟界面语言无关**：`units` 数的是书的正文，不是这句
 * 提示本身；提示本身翻不翻译不会让这个量词更准或更不准。
 */
export function unitsLabel(units: number, language: Language): string {
  return language === "zh" ? `约 ${grouped(units)} 字` : `about ${grouped(units)} characters`;
}

/** 这一批稿子摆在哪几章。**后端已经排好序**，这儿只去重不重排。 */
export function chaptersOf(drafts: readonly DraftCandidateView[]): number[] {
  return [...new Set(drafts.map((d) => d.chapter))];
}

/**
 * 这一堆稿子上面那句话：写了几稿、是哪一章的。
 *
 * **不说「给你写了三个版本，挑一个」**——那是在替助手表态；它自己那句话在回执里，
 * 这儿只报事实（§10 约束 8：说得出来的才说）。
 *
 * **整句模板**：英文那半要处理稿数的单复数（1 draft / 2 drafts），
 * 拼片段会在这个数变化时漏掉或多出一个 s。
 */
export function draftsHeading(drafts: readonly DraftCandidateView[], language: Language): string {
  const chapters = chaptersOf(drafts);
  if (language === "zh") {
    const where = chapters.length === 1 ? `第 ${chapters[0]} 章` : `第 ${chapters.join("、")} 章`;
    return `本轮 ${drafts.length} 稿 · ${where}`;
  }
  const where =
    chapters.length === 1 ? `chapter ${chapters[0]}` : `chapters ${chapters.join(", ")}`;
  return `Wrote ${drafts.length} draft${drafts.length === 1 ? "" : "s"} this round · ${where}`;
}

/**
 * 有稿子已经进书了 —— 屏幕上必须说一句，而且要说得出**怎么退**。
 *
 * 落盘**不问作者**（ADR 0021 的核心，ADR 0022 一个字没改），所以这一侧欠他两件事：
 * **看得见**（这一行）和**改得掉**（那句话里的「历史」，`CenterEditor` 上那颗按钮）。
 * 只说前一半 = 告诉他书被改了却不说怎么办；一句都不说 = 他在编辑器里看见一段
 * 自己没写的字（ADR 0021 的「代价」第三条点名了这一档：闸挡不住它，只能靠界面）。
 *
 * 一个都没落盘时返回 `null`：**没发生的事不写**（零不写，同 `chat.ts::receiptNotes`）。
 *
 * **整句模板**：英文那半要处理落盘稿数的单复数（has / have），拼片段会漏掉这个变化。
 */
export function landedNote(drafts: readonly DraftCandidateView[], language: Language): string | null {
  const landed = drafts.filter((d) => d.landed);
  if (landed.length === 0) return null;
  if (language === "zh") {
    const which = landed.map((d) => `第 ${d.chapter} 章的${draftLabel(d, language)}`).join("、");
    return `${which}已写入正文。如需撤销，可在正文「历史」中退回上一版。`;
  }
  const which = landed
    .map((d) => `${draftLabel(d, language)} for chapter ${d.chapter}`)
    .join(", ");
  return `${which} ${landed.length === 1 ? "has" : "have"} been written into the text. To undo, use “History” on the text side to revert to the previous version.`;
}

/** 并排比那一页的地址。**哈希路由 = 零新基础设施**：工作台本来就是本地浏览器应用
 *  （`nh serve` 开的就是 localhost），这一页是同一个应用的另一条路由，
 *  不需要服务端多认一个路径，也不需要多一个进程。 */
export function comparePath(chapter: number): string {
  return `#/compare/${chapter}`;
}
