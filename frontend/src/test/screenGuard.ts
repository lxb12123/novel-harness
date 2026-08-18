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
// 明天新长出来的那个码也一起收。五张网，各罩一种形状：
//
// | 网 | 罩什么 | 真出现过的例子 |
// |---|---|---|
// | `MACHINE` | snake_case | `stale_base_version` / `provider_failure` / `valid_from` |
// | `ENGINE_ENUM` | SCREAMING_SNAKE（形状）+ 裸大写枚举 + 驼峰节点类别 | `NOT_FOUND` / `KNOWS` / `PROVISIONAL` |
// | `RAW_ID` | `前缀:标识` 形状的内部主键，**包括被截断的** | `edge:01J8…` / `n:ID22` |
// | `ENGLISH_PROSE` | **一连三个小写英文单词**（= 一句写给维护者的英文） | `chapter analysis provider failed` |
// | `SHELL_LINE` | **一句「去终端里敲这个」** | `先跑 nh sync` / `用 nh locate 先试` |
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
// **第五张网收的是另一类东西**（2026-08-13 补）：不是「术语」，是「一条命令」。
// 真上过屏的两句是 `declare.py` 里的「先跑 nh sync」「用 nh locate 先试」，
// 而前四张网一张都咬不住（没有下划线 / 不是大写 / 没有冒号 / 只有两个词）。
// 产品的最终用户是那位「用 WPS、不想碰命令行」的作者——见 `SHELL_LINE` 自己那段注释。
//
// 自守卫在 `screenGuard.test.ts`：八个**真的上过屏的**违规当探针，一个都抓不住就红。

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
  /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|\b(?:KNOWS|BELIEVES|UNKNOWN|CANON|PROVISIONAL|PLANNED|REJECTED|RETRACTED|ACTIVE|STALE|FRESH|OWNS|PENDING|RUNNING|SUCCEEDED|FAILED|SUPERSEDED|ACCEPTED|EDITED|AMBIGUOUS|EXACT|FUZZY|NONE)\b|\b(?:Character|Location|Faction|Secret|Foreshadow|Object|StateDim|Chapter)\b/g;

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

/** **第五张网**（2026-08-13）：一句「去终端里敲这个」。
 *
 *  真上过屏的两句在 `declare.py`（经 `api/app.py` 原样进 `message`，由当时的
 *  `DeclareDrawer` 逐字渲染；**那个组件 2026-08-14 删了**，所以今天这两句只到终端）：
 *
 *      「也可能是这一章还没进库：先跑 nh sync。」
 *      「把引语加长到只匹配一处…用 nh locate 先试。」
 *
 *  **前四张网一张都咬不住它们**：`nh sync` 没有下划线（`MACHINE` 过）、不是大写
 *  （`ENGINE_ENUM` 过）、没有冒号（`RAW_ID` 过）、只有两个词（`ENGLISH_PROSE` 要三个）。
 *  产品的最终用户是那位「用 WPS、不想碰命令行」的作者——对他说这句话，等于让他
 *  去改一个他没做错的操作，或者干脆卡死。
 *
 *  ── 两段：前一段纯形状，后一段是命令名 ───────────────────────────────────
 *
 *  1. `--flag`：ASCII 双连字符 + 小写字母。**纯形状**，中文界面上的破折号是「——」，
 *     ASCII 的 `--` 后面跟一个字母只可能是一个命令行开关。
 *  2. `<命令名> <一个 ASCII 词或开关>`。这半段只能是词表——`nh` 形状上和任何两个
 *     字母的缩写分不开，收「所有两字母词」就是假红。**但这张表不是手抄的**：
 *     `tests/test_wording_guard.py` 拿 `pyproject.toml` 的 `[project.scripts]`（本产品
 *     自己的命令名）+ `cli.py` 里 typer 注册的**每一个**子命令来比——命令改名、
 *     新加一个子命令而这儿不跟，那条 pytest 当场红。
 *
 *  ── 门槛：为什么是「命令名 + 空格 + 一个词」而不是光一个命令名 ──────────────
 *
 *  光一个 `nh` 可能是别的东西（人名缩写、单位、型号里的两个字母）；而「`nh` 后面
 *  紧跟着一个小写英文词」在一块中文屏幕上只可能是一条命令。**假红会让下一个人把
 *  守卫关掉**，所以宁可让「孤零零一个 nh」漏过去。
 *
 *  ── 它**明确不收**的那几类，写在这儿免得下一个人以为它全包 ────────────────
 *
 *  - **裸路径**（`chapters/0001.md`）：那是作者自己文件夹里的文件名，他需要看见它
 *    （同步回执就在摆这些名字）。
 *  - **通配符路径**（`chapters/*.md`）：同上，且它今天只出现在终端那一侧。
 *  - **孤零零一个命令名**：见上面那条门槛。
 *  这三类只能靠 review 拦。 */
export const SHELL_LINE =
  /(?<![\w-])--[a-z][a-z0-9-]*|\b(?:nh|novel-harness|uv|uvx|npm|npx|pnpm|pip|pipx|python3?|node|git|bash|curl|docker|pytest|ruff|vitest)[ \t]+[-a-z][\w./-]*/g;

function hits(text: string, pattern: RegExp): string[] {
  // 正则带 /g，`match` 每次都要一个新的 lastIndex —— 复用同一个对象会漏掉一半。
  return [...new Set(text.match(new RegExp(pattern.source, "g")) ?? [])];
}

export const machineWords = (text: string): string[] => hits(text, MACHINE);
export const engineWords = (text: string): string[] => hits(text, ENGINE_ENUM);
export const rawIds = (text: string): string[] => hits(text, RAW_ID);
export const englishProse = (text: string): string[] => hits(text, ENGLISH_PROSE);
export const shellLines = (text: string): string[] => hits(text, SHELL_LINE);

/** 五张网合起来。**这是给「整块屏幕」用的那一个**，单独的五个留给要说清楚
 *  「漏的是哪一类」的断言。 */
export const devTerms = (text: string): string[] => [
  ...new Set([
    ...machineWords(text),
    ...engineWords(text),
    ...rawIds(text),
    ...englishProse(text),
    ...shellLines(text),
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
