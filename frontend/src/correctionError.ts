import { ApiError } from "./api/client";

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
// 后端的拒绝文案现在本来就是作者的话（`corrections.py` 的 `CorrectionError` 那段
// docstring 写着这条约束，`tests/test_canon_edit_boundary.py` 两头各钉一道）。
// **再看见引擎的词，去改后端，不要在这儿加映射表。**

export type CorrectionFailureKind = "stale" | "gone" | "refused" | "unknown";

export interface CorrectionFailure {
  kind: CorrectionFailureKind;
  message: string;
}

// ── 前端自己写的话只有三句，而且**一句都不是翻译** ────────────────────────
//
// 上面那条纪律（不改写后端的句子）留下了一格没人管的：**后端有几种拒绝一句话都没写。**
// `load_project` 的 404 是 `{"error": "project_not_found", "project_id": …}`，
// `stale_base_version` 的 409 是两个版本号——两条编辑路由都过这道闸门
// （`tests/test_canon_edit_boundary.py::test_these_two_routes_can_answer_with_a_bare_code`
// 从真 app 钉住了这个形状）。
//
// 而 `ApiError` 的 `.message` 在没有 `message` 时**退回 `body.error`**，于是错误框里
// 摆出来的是 `project_not_found` 五个字母——一个小说作者看到的第一反应是「我把书弄坏了」。
//
// **有话就照说，一个字不改；一句话都没有的时候才轮到下面三句。** 它们不翻译任何一个
// 引擎词，所以不是那张被删掉的映射表——那张表干的是「把后端说的 KNOWS 换成知道」，
// 这三句干的是「后端什么都没说的时候别把代号当话」。

/** 「别处刚改过」。必须把作者推去**看**，不是去重点一次：静默重试等于把他的改动
 *  盖到一份他没看过的状态上。哪天后端给这一条补了 message，这一句就该退位。 */
const STALE =
  "这本书在别处刚刚被改过，你看到的还是改动之前的样子。先看一眼最新的，再决定这一处要不要改。";

/** 后端拒了，但一个字都没说为什么。**不许编一个理由**（§10 约束 8：不知道就说不知道），
 *  也不许说「请稍后再试」——那是在暗示重试有用，而书不在了的时候重试一百次都一样。 */
const SILENT =
  "这次改动没能保存，而系统没能说清是为什么。刷新一下看看这本书现在是什么样，再决定要不要重来一次。";

/** 根本没到后端。不冒充一次业务拒绝。 */
const OFFLINE = "没能保存这次改动，请稍后再试。";

/** 后端**写给作者的那句话**，没写就是 `null`。
 *
 *  只认 `message`，故意不读 `error.message`：`ApiError` 的 `.message` 会在没有
 *  `message` 时退回 `body.error`，而 `error` 是给代码分支用的码，不是话。
 *  这一行就是这条缝的补丁位置。
 *
 *  **导出是因为它有第二个消费者**（写作助手面板，`chat.ts::refusalText`）：
 *  那几条路由的 404 同样只有码没有话（`{"error":"chat_not_found",…}`）。
 *  判据只许有一处——在那边照抄一份 `error.body.message` 的读法，
 *  就是这个仓库反复在清的「同一条规矩两份拷贝」。 */
export function saidToTheAuthor(error: ApiError): string | null {
  const text = error.body.message;
  return typeof text === "string" && text.trim() ? text : null;
}

export function readCorrectionError(error: unknown): CorrectionFailure {
  if (error instanceof ApiError) {
    const said = saidToTheAuthor(error);
    if (error.status === 409 || error.code === "stale_base_version")
      return { kind: "stale", message: said ?? STALE };
    if (error.status === 404) return { kind: "gone", message: said ?? SILENT };
    if (error.status === 422) return { kind: "refused", message: said ?? SILENT };
    return { kind: "unknown", message: said ?? SILENT };
  }
  // 网络断了、后端没起来。
  return { kind: "unknown", message: OFFLINE };
}
