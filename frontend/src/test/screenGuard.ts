// 「屏幕上不许出现研发术语」这条纪律的**唯一一份判据**。
//
// ── 为什么是形状，不是词表 ────────────────────────────────────────────────
//
// 这个仓库为「词表只覆盖写它那天想得到的几个词」吃过两次亏：
//
// 1. `correctionError.ts` 里那张「码 → 中文」的映射表（已删）——它同时还是第二份措辞源；
// 2. `ActivityLog.test.tsx` 里那条「不摆研发术语」的断言，正则写的是
//    `valid_from|canon_version|PROVISIONAL|endpoints|payload|decision_log`，
//    而后端当时正在往屏幕上印 `KNOWS → BELIEVES`——**那两个词恰好不在表里，
//    于是它绿了一整轮**。补一个词进去只会让下一个词接着漏。
//
// 所以判据是**形状**：一个中文界面上出现「长得像标识符的英文」本身就是判据，
// 明天新长出来的那个码也一起收。三张网，各罩一种形状：
//
// | 网 | 罩什么 | 真出现过的例子 |
// |---|---|---|
// | `MACHINE` | snake_case | `stale_base_version` / `provider_failure` / `valid_from` |
// | `ENGINE_ENUM` | SCREAMING_SNAKE（形状）+ 裸大写枚举 + 驼峰节点类别 | `NOT_FOUND` / `KNOWS` / `PROVISIONAL` |
// | `RAW_ID` | `前缀:标识` 形状的内部主键，**包括被截断的** | `edge:01J8…` / `n:ID22` |
// | `ENGLISH_PROSE` | **一连三个小写英文单词**（= 一句写给维护者的英文） | `chapter analysis provider failed` |
//
// ── 这张网**看不见**的那一类，写在这儿免得下一个人以为它全包 ────────────────
//
// **单个小写的裸枚举值**：`author` / `system` / `failed` / `succeeded` / `extraction` /
// `decision` / `accept` / `reject` / `edit` / `extractor` / `canonical` / `queued`。
// HTTP 层有意把一批枚举小写化（`ActivityStatus = "failed"`），而一个小写英文单词
// 和界面上合法的英文（`token` / `ms` / `deepseek-v4`）形状上分不开——**收它就假红，
// 而假红会让下一个人把守卫关掉**。这一类只能在源头堵：那些值一律不许直接上屏，
// 由后端翻好（`activity.actor_label`）或由前端一张**类型上全列**的表映射
//（`ActivityLog.ACTOR_ZH` / `STATUS_ZH`），两者的完整性由
// `tests/test_wording_guard.py` 拿枚举本身钉住。
//
// **第四张网收的是它的另一半**（2026-08-13 补）：单个词分不开，**一连三个就分得开**。
// `provider_failure: chapter analysis provider failed` 曾经整句摆在小说作者的屏幕上，
// 而当时三张网只咬到了前面那个 snake_case——后面那句英文一个字都没被咬。
// 判据仍然是形状不是词表：明天换一句别的英文诊断，它照样收。
//
// 第三张网为什么不是前缀白名单（`edge|event|node|…`）：`ProposalReviewTab` 那条
// `id.slice(-6)` 把 `location:ID22` 截成了 `n:ID22`，白名单里的 `node:` 当场逃掉。
// **截断一个认不出的东西不会让它变得认得出，只会让守卫看不见它。**
//
// 自守卫在 `screenGuard.test.ts`：五个**真的上过屏的**违规当探针，一个都抓不住就红。

/** 屏幕上的 snake_case 标识符：错误码、库字段名、请求体键名。 */
export const MACHINE = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g;

/** 引擎的封闭枚举值：边类型 / 生效层 / 边状态 / 运行态 / 节点类别。
 *  **这一批一个下划线都没有**，`MACHINE` 天生罩不住它们。
 *
 *  ── 三段，前一段是形状，后两段是词表（词表由枚举本身钉住）──────────────────
 *
 *  1. `SCREAMING_SNAKE` 是**形状**：`LOCATED_AT` / `NOT_FOUND` / `WRONG_LABEL` /
 *     `SUPERSEDED_IN_ANALYSIS` 一网打尽，**明天新加的那个枚举值也一起收**。
 *     2026-08-11 之前这儿是一张 30 个词的手抄表，而当天实测它漏掉 13 个真实枚举值
 *     （`DiscardOutcome` 七个、`LocateOutcome` 五个一个都不在）——**「换成形状」
 *     那一轮只换了裸 id 那一条，大写枚举这一条仍然是词表。**
 *  2. 没有下划线的裸大写值（`CANON` / `ACCEPTED` / `FUZZY`）形状上和缩写
 *     （`API` / `URL` / `TXT`）分不开，只能列。**但这份清单不是手抄的**：
 *     `tests/test_wording_guard.py` 拿 Python 那边每一个 `StrEnum` 的成员逐个来比，
 *     少一个就红——枚举加一行、这儿不补，CI 拦得住。
 *  3. 节点类别是驼峰（`Character`），同理只能列，同样由那条 pytest 钉住。 */
export const ENGINE_ENUM =
  /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|\b(?:KNOWS|BELIEVES|UNKNOWN|CANON|PROVISIONAL|PLANNED|REJECTED|RETRACTED|ACTIVE|STALE|FRESH|OWNS|PENDING|RUNNING|SUCCEEDED|FAILED|ACCEPTED|EDITED|AMBIGUOUS|EXACT|FUZZY|NONE)\b|\b(?:Character|Location|Faction|Secret|Foreshadow|Object|StateDim|Chapter)\b/g;

/** 内部主键的形状：`字母开头的前缀` + `:` + `标识`。
 *
 *  故意**不**列前缀白名单——被截断过的 id（`n:ID22`）没有完整前缀，而它正是这张网
 *  存在的理由。中文界面上的冒号是全角「：」，时间戳里的冒号前面是数字，
 *  两者都不会被咬（`screenGuard.test.ts` 钉住了这一条）。 */
export const RAW_ID = /\b[A-Za-z][A-Za-z0-9]*:[A-Za-z0-9]+/g;

/** 写给维护者的一句英文：**一连三个小写英文单词**。
 *
 *  真出现过的那一句是 `chapter analysis provider failed`（`ExtractionRunError.message`，
 *  `extract/control.py` 明写「它永远不上作者的屏幕」，而审阅面板渲染的正是它）。
 *
 *  **门槛是三个词，不是两个**：中文界面上合法的英文全是单个 token
 *  （`token` / `ms` / `deepseek-v4-flash` / `1200 token`），两个词的组合还可能是
 *  一个模型名加一个单位，三个连着的小写英文单词只可能是一句话。
 *  假红比漏报更危险——它会让下一个人把守卫关掉，而不是把界面修好。 */
export const ENGLISH_PROSE = /\b[a-z]{2,}(?:[ \t]+[a-z]{2,}){2,}\b/g;

function hits(text: string, pattern: RegExp): string[] {
  // 正则带 /g，`match` 每次都要一个新的 lastIndex —— 复用同一个对象会漏掉一半。
  return [...new Set(text.match(new RegExp(pattern.source, "g")) ?? [])];
}

export const machineWords = (text: string): string[] => hits(text, MACHINE);
export const engineWords = (text: string): string[] => hits(text, ENGINE_ENUM);
export const rawIds = (text: string): string[] => hits(text, RAW_ID);
export const englishProse = (text: string): string[] => hits(text, ENGLISH_PROSE);

/** 四张网合起来。**这是给「整块屏幕」用的那一个**，单独的四个留给要说清楚
 *  「漏的是哪一类」的断言。 */
export const devTerms = (text: string): string[] => [
  ...new Set([
    ...machineWords(text),
    ...engineWords(text),
    ...rawIds(text),
    ...englishProse(text),
  ]),
];

/** 屏幕 = 文本节点 **+ 无障碍属性**。
 *
 *  `document.body.textContent` 不含 `aria-label` / `title` / `placeholder` / `alt`——
 *  而读屏的作者听见的正是前者。它们和屏幕上的字是同一块屏，漏掉就等于只验了一半。 */
export function screenText(): string {
  const parts = [document.body.textContent ?? ""];
  for (const el of document.querySelectorAll("[aria-label],[title],[placeholder],[alt]")) {
    for (const name of ["aria-label", "title", "placeholder", "alt"]) {
      const value = el.getAttribute(name);
      if (value) parts.push(value);
    }
  }
  return parts.join("\n");
}
