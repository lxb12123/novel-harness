import { ApiError } from "./api/client";
import { messageForCode } from "./backendMessages";
import { type Language, useLanguage } from "./language";

// 改一条**已经生效**的事实被拒绝时，屏幕上该出现什么（ADR 0020 的「可改」那一半）。
//
// 三类拒绝，三种含义，**不许合并成一句「保存失败」**：
//
// | HTTP | 后端的 error | 真正发生的事 | 作者该做什么 |
// |---|---|---|---|
// | 409 | `stale_base_version` | 这本书在别处刚被改过（后台自动升 CANON 跑完了） | 先看一眼最新的 |
// | 404 | `fact_not_found` | 他点的那条事实今天不在了 | 看一眼现在是什么样 |
// | 422 | `bad_request` | 这次改动本身讲不通 | 改一下再提交 |
//
// **409 绝不静默重试。** 重试等于把作者的改动盖到一份他没看过的状态上——而那正是
// ADR 0020 用「可查 + 可改」换掉「事前批准」时唯一还站得住的地方：他看得见发生了什么。
//
// ── 这里**不改写**后端那句话，一个字都不改 ────────────────────────────────
//
// 曾经有过一张「引擎的词 → 作者的词」的映射表（`KNOWS` → 「知道」…）。它被删掉了，
// 理由是措辞的**源**只能有一个：有了这张表，后端就可以一直吐 `KNOWS`，而表永远只
// 覆盖写它那天想得到的几个词（`Character`、裸 id、写给维护者的「面板 §3.2」全在外面），
// 更糟的是屏幕上的说法和 CLI、和活动记录不再是同一句话。
// 后端的拒绝文案在这份文件写下的当时本来就是作者的话（`corrections.py` 的
// `CorrectionError` 那段 docstring 写着这条约束，`tests/test_canon_edit_boundary.py`
// 两头各钉一道）。**这一条今天仍然成立，`saidToTheAuthor` 一个字不改（见下）。**
//
// ── 国际化第四批打破的是另一个前提，不是这一条 ────────────────────────────
//
// 「源只能有一个」这句话本身没有被推翻，被推翻的是**那个源必须在后端**——因为那个
// 假设默认「后端算出来的中文就是读它的人要的话」，而界面语言独立于书的语言（维护者
// 裁定 B）之后，后端不再知道读这句话的人用什么界面语言（后台任务甚至没有 HTTP
// 请求可读）。于是**新的单一源在前端**（`backendMessages.ts`），而它不是那张被删掉
// 的旧表的复辟：旧表覆盖不了引擎内部随时会长出的开放词表、也没有机械校验；这张表
// 只收这个仓库自己定义的**封闭**码集合，`test_every_backend_code_has_a_frontend_translation`
// （后端）钉着"后端能发的每个码，这张表都有"，`backendMessages.test.ts` 钉着两侧
// 非空、英文零中文——旧表死于"会漂"，这张表结构上漂不了。
//
// **判据依然只有一个**：认得的码走 `backendMessages.ts` 整句渲染；认不出的码（还没
// 迁移的端点、真正的开放世界失败）落到 `.body.message`（过渡期）或下面这三句通用兜底。
// **不认识某个具体后端拒绝就编一句翻译**——那才是当年那张表真正的病，不是"前端有一
// 张表"这件事本身。

export type CorrectionFailureKind = "stale" | "gone" | "refused" | "unknown";

export interface CorrectionFailure {
  kind: CorrectionFailureKind;
  message: string;
}

// ── 前端自己写的话只有三句通用兜底，而且**一句都不针对某个具体拒绝** ─────────
//
// 有两种情况会走到这三句：**后端这个码还没配文案**（`backendMessages.ts` 没这一条），
// 或者**后端这一条路由本来就没打算说话**（`load_project` 的 404 是
// `{"error": "project_not_found", "project_id": …}`，`stale_base_version` 的 409
// 是两个版本号——两条编辑路由都过这道闸门，
// `tests/test_canon_edit_boundary.py::test_these_two_routes_can_answer_with_a_bare_code`
// 从真 app 钉住了这个形状）。
//
// **认得的码，一个字都不编**——`saidToTheAuthor` 查得到就用查到的那句，查不到才轮到
// 下面三句。它们不是某个具体拒绝的翻译（那种要写就写进 `backendMessages.ts`，
// 归码表管），是「这次真的什么都不知道」时的通用措辞——**跟着界面语言切换，
// 但内容本身是前端原创的兜底，不是从中文翻过去的**。

/** 「别处刚改过」。必须把作者推去**看**，不是去重点一次：静默重试等于把他的改动
 *  盖到一份他没看过的状态上。哪天后端给这一条配了码，这一句就该退位。 */
const STALE: Record<Language, string> = {
  zh: "本书已在别处修改，当前显示的仍是修改前的内容。请先查看最新版本，再决定是否修改。",
  en: "This book was changed elsewhere; what is shown is the version before that change. Review the latest version before making this edit.",
};

/** 后端拒了，但一个字都没说为什么。**不许编一个理由**（§10 约束 8：不知道就说不知道），
 *  也不许说「请稍后再试」——那是在暗示重试有用，而书不在了的时候重试一百次都一样。 */
const SILENT: Record<Language, string> = {
  zh: "本次改动未能保存，未返回原因。请刷新查看本书当前状态，再决定是否重试。",
  en: "This change could not be saved and no reason was returned. Refresh to see the current state of the book, then decide whether to try again.",
};

/** 根本没到后端。不冒充一次业务拒绝。 */
const OFFLINE: Record<Language, string> = {
  zh: "本次改动未能保存，请稍后重试。",
  en: "This change could not be saved; try again shortly.",
};

/** 后端**写给作者的那句话**，没有就是 `null`。
 *
 *  三级优先：① `error.code` 在 `backendMessages.ts` 里查得到——用整句模板渲染
 *  （国际化第四批的新源，`error.body.params` 是填模板的原始事实）；② 查不到、
 *  但后端这一条路由还在用旧形状发 `message`——原样用它，**过渡期**兼容，一个字不改
 *  （`ApiError` 的 `.message` getter 会在没有 `message` 时退回 `body.error`，
 *  这里故意直接读 `body.message`，不借那个 getter，防止把码当话使）；③ 都没有 → `null`，
 *  轮到调用方自己的通用兜底。
 *
 *  **导出是因为它有第二个消费者**（写作助手面板，`chat.ts::refusalText`）：
 *  那几条路由的 404 同样只有码没有话（`{"error":"chat_not_found",…}`）。
 *  判据只许有一处——在那边照抄一份读法，就是这个仓库反复在清的
 *  「同一条规矩两份拷贝」。 */
export function saidToTheAuthor(error: ApiError): string | null {
  const language = useLanguage.getState().language;
  const coded = error.code ? messageForCode(error.code, language, error.body.params) : undefined;
  if (coded) return coded;
  const legacy = error.body.message;
  return typeof legacy === "string" && legacy.trim() ? legacy : null;
}

export function readCorrectionError(error: unknown): CorrectionFailure {
  const language = useLanguage.getState().language;
  if (error instanceof ApiError) {
    const said = saidToTheAuthor(error);
    if (error.status === 409 || error.code === "stale_base_version")
      return { kind: "stale", message: said ?? STALE[language] };
    if (error.status === 404) return { kind: "gone", message: said ?? SILENT[language] };
    if (error.status === 422) return { kind: "refused", message: said ?? SILENT[language] };
    return { kind: "unknown", message: said ?? SILENT[language] };
  }
  // 网络断了、后端没起来。
  return { kind: "unknown", message: OFFLINE[language] };
}
