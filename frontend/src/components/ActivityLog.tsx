import { useState } from "react";
import { useActivity, useActivityDetail, useRuns, useStartExtraction } from "../api/hooks";
import { useOpenChapter } from "../chapterNavigation";
import { refusalText } from "../chat";
import { useCoords, type Tab } from "../store";
import { shownTime } from "../time";
import { messageForCode } from "../backendMessages";
import { useLanguage, type Language } from "../language";
import type {
  ActivityCost,
  ActivityEntry,
  ActivityJump,
  ActorTally,
  CostTotals,
  JumpTarget,
} from "../api/types";

// 活动记录（进度板 2.2 / [ADR 0020](docs/adr/0020-clean-extraction-auto-canon.md)）。
//
// 这一页存在的理由只有一条：**系统开始自动往作者的书里写东西了**（干净的抽取结果直接
// 升 CANON，`actor='system'`），而 ADR 0020 押的赌是「错了能改」比「错了先拦住」更合用。
// 那个赌注只有在作者**看得见系统写了什么**的时候才成立——在这一页之前，三张表
// （抽取 / 模型调用 / 确认）一直在写真数据，一条都没露过面。
//
// 三条纪律，破一条这页就退化成装饰：
//
// 1. **跳去哪儿由后端说了算。** 每一行带一个结构化 `jump`（坐标 + 措辞 + endpoints），
//    前端不从 title / subtitle / source 反推——反推 = 第二份路由表 = 两份迟早漂。
// 2. **`jump.endpoints` 为空是一个断言**（「今天没有任何路由能改这个东西」），不是没填。
//    自动升上去的**边**就落在这一档（抽取只产 LOCATED_AT / HAS_STATE / RELATED_TO，
//    而改正层只改 KNOWS↔BELIEVES 和事件名单）。这正是 ADR 0020 自己写下的推翻条件之一
//    ——**这一页把它显式说出来，那个条件才第一次可观测**。编个假按钮 = 把观测点关掉。
// 3. **审计信封不上屏。** `ActivityDetail.payload` 是给机器看的（引擎内部字段一堆），
//    作者要看的那份是 `rows`——措辞全在后端，这里不写一行文案分支。

/** actor → 界面语言那句话。**开放字符串**（`decision_log.actor` 那一列就是开放的），
 *  认不出的原样显示：明天多一种 actor，宁可露出英文也别静默显示成空白。
 *
 *  **不在这儿另起一张表**（国际化第四批·笔二起）：这份映射唯一的出处是
 *  `backendMessages.ts` 的 `ACTOR_LABEL`（`decision_entry_title` 组装标题时也查它），
 *  这里复用给 DetailRow「谁改的」用的同一个码 `value_actor`——它的行为本来就是
 *  「这个 actor 值该怎么显示」，跟摆在哪个组件无关。**这里以前有一张自己的
 *  `ACTOR_ZH`，纯中文、不认界面语言，是两份措辞源里没跟上的那一份，删了。** */
const actorName = (actor: string, language: Language): string =>
  messageForCode("value_actor", language, { actor }) ?? actor;

/** 跳转按钮上那句话。`jump.label_code`/`label_params` 是坐标判断的一部分
 *  （同 `target`），措辞按当前界面语言渲染——认不出的码原样显示（同一个开放
 *  世界里「这个码我们还没配文案」的诚实形态，见 `backendMessages.ts`）。 */
const jumpLabel = (jump: ActivityJump, language: Language): string =>
  messageForCode(jump.label_code, language, jump.label_params) ?? jump.label_code;

/** 抽取失败的原因。`ExtractionErrorCode` 的原始值 → 界面语言（`RUN_ERROR_LABEL`，
 *  同 `api/extraction.py::ExtractionRunView.errors` 那条读端查的是同一张表）。 */
const runErrorText = (code: string, language: Language): string =>
  messageForCode("run_error", language, { code }) ?? code;

/** 没跑成的那几种态。成功的不写字——一行「完成」乘以几千行就是噪音。 */
const STATUS_ZH: Record<string, { zh: string; en: string }> = {
  failed: { zh: "失败", en: "Failed" },
  running: { zh: "进行中", en: "Running" },
  pending: { zh: "排队中", en: "Queued" },
};

/** 后端一句话都没写时才轮到的那一句（同 `SummaryTab`）：**不解释「为什么」**
 *  ——不知道就说不知道（§10 约束 8）。 */
const RETRY_FAILED = (language: Language): string =>
  language === "zh"
    ? "本章未能重新排队，未返回原因。请稍后重试。"
    : "This chapter could not be queued again and no reason was returned. Try again shortly.";

/** 跳转坐标 → 右栏的哪一格。
 *
 *  **这不是第二份路由表。** 跳去哪个模块是后端算的（`jump.target`，结构化枚举），
 *  这里只把那个枚举翻成「右栏第几个 tab」——tab 是浏览器自己的东西，后端不知道
 *  也不该知道。改这条事实要打哪条路由，全在 `jump.endpoints` 里，前端一个字都不拼。
 *
 *  `chapter` 是 `null`：它是兜底坐标（「只能定位到这一章」），换完章就到位，
 *  右栏停在作者原来看的那一格比替他跳一格更诚实。
 *
 *  `extraction_retry` 也是 `null`，但理由完全不同：**那一档根本不是跳转**——
 *  按钮就在这一行上，按下去是把没跑成的那次整理再跑一遍（`RetryRow`）。 */
const TARGET_TAB: Record<JumpTarget, Tab | null> = {
  event_cast: "review",
  proposal: "review",
  summary: "summary",
  extraction_retry: null,
  canon_edge: null,
  chapter: null,
};

/** 跳过去之后，作者今天在**工作台里**真的有得点吗。
 *
 *  **2026-08-11：认知矩阵和事件名单这两档从 `false` 变成了 `true`。** 1.1 落的那两条
 *  改正路由（`/canon/knowledge` / `/canon/events/{id}/cast`）此前在浏览器里没有调用方，
 *  两块面板都只能看；现在矩阵那一格点得开、已确认的情节名单也改得动，所以这一页
 *  不再需要那句「直接改动的入口还没做」——**留着它就从一句诚实话变成一句骗人的话。**
 *
 *  这张表和 `endpoints` 问的不是同一个问题，两个都要看：
 *  `endpoints` = 引擎有没有这条路（后端的知识）；这张表 = 工作台有没有那个控件
 *  （前端自己的知识）。少看一个，作者就会点到一个「跳过去发现改不了」的按钮。
 *  `chapter` 那一档仍然是 `false`，而且**不许因为「反正现在都能改了」顺手改成 true**：
 *  它盖着的正是「自动升上去的位置/状态边今天没有任何编辑入口」那一种。 */
const CAN_EDIT_HERE: Record<JumpTarget, boolean> = {
  event_cast: true,
  proposal: true,
  // 右栏第九格「章节总结」：那一段能改、能撤回、能重新生成（2026-08-13）。
  // 在它存在之前，写总结那次调用只能跳到章——**那时这一档写 `false` 才是诚实的**。
  summary: true,
  // 这一档「有得点」的那个控件就是这一行上的按钮本身（`RetryRow`），不在别的屏幕上。
  extraction_retry: true,
  // 自动升上去的地点/状态/关系边（Task 8）：跳过去打开 `CanonEdgeEditor`。
  canon_edge: true,
  chapter: false,
};

/** 跳过去之后能不能动手，说清楚。能动手的（矩阵那一格 / 事件名单 / 待确认队列）不啰嗦。
 *
 *  两个条件都要成立才算「能动手」：引擎有那条路（`endpoints` 非空）**且**工作台有那个
 *  控件（`CAN_EDIT_HERE`）。只看后者的话，将来某个目标控件先落地、路由还没有，
 *  作者会点到一个必然报错的按钮。
 *
 *  ⚠️ **空 `endpoints` 今天有两个意思，下面这句话不许只按一个写**
 *  （`activity._decision_jump` 里那段注释 + `test_activity.py::
 *  test_a_row_that_changed_several_events_does_not_read_as_unfixable`）：
 *
 *  ① 真的没有任何路由能改它（2026-08-17 之前自动升上去的位置/状态边就落在这——
 *      `canon_edge` 那一档落地后，剩在这儿的只有正在补纠错入口的新边类型）；
 *  ② 有好几条、后端**不替作者挑是哪一条**（一次升掉一整章的干净事件，是常态不是边角）
 *     ——那几条其实一打就通。
 *
 *  两者在出参形状上长得一模一样（差别只在 `label` 的措辞里，而从措辞反推是这一页
 *  第一条禁令）。所以这句只说**「从这儿点不到某一处」**，绝不说「改不了」——
 *  把 ② 说成没救了，正好否掉 ADR 0020 押的那条退路。 */
function jumpNote(entry: ActivityEntry, jump: ActivityJump, language: Language): string | null {
  if (CAN_EDIT_HERE[jump.target] && jump.endpoints.length > 0) return null;
  // 只有「改了东西」的那一行值得说这句：一次模型调用本来就没有什么可改的。
  if (entry.source !== "decision") return null;
  return language === "zh"
    ? "此步骤改动的内容无法直接定位，仅可跳转到对应章节。"
    : "The content changed in this step cannot be located directly; only the chapter can be opened.";
}

/** 数字。**null 是「没记」不是 0**（§10 约束 8）：一张写着 0 的账单是假的。 */
const num = (value: number | null, language: Language): string =>
  value === null ? (language === "zh" ? "未记录" : "Not recorded") : String(value);

/** 一笔钱。**后端给的是美元，而且是标价估算，不是账单。**
 *
 *  两条规矩，各防一种假话：
 *  1. `null` ⇒ 「未记录」，**绝不渲染成 0** —— 一张写着 0 元的账单是本仓反复在修的
 *     那种失败形态（漂亮的空结果 + 200）。算不出来有好几种真实原因：自建端点、
 *     公共表里没这个模型、供应商没报 token 数。
 *  2. 算得出时**必须带「约」字**。单价来自一份公开的标价表，而作者可能有折扣、
 *     走中转、用免费额度 —— 把估算摆成账单，比不摆更坏。
 *
 *  小到显示不出来的那些不写「约 $0.00」（那读起来像免费），写「不到 $0.01」。 */
export const money = (value: number | null, language: Language): string => {
  if (value === null) return language === "zh" ? "未记录" : "Not recorded";
  if (value > 0 && value < 0.01) return language === "zh" ? "不到 $0.01" : "Under $0.01";
  return language === "zh" ? `约 $${value.toFixed(2)}` : `about $${value.toFixed(2)}`;
};

/** 用量那一格 —— **「已经知道的那些的合计」和「全部的合计」不是一回事**。
 *
 *  供应商不报 usage 时后端给的是 null（`record_call` 照抄，绝不估算），而 2026-08-12
 *  起草改成可中断之后这一档成了常态：README 教作者填的 DeepSeek 就不报。所以这一格
 *  分三档说三句不同的话，判据是 `metered_calls`（同旁边那格的 `priced_calls`）：
 *
 *  | 报了几次 | 屏幕上 |
 *  |---|---|
 *  | 全报了 | 读入 1200 / 生成 400 token |
 *  | 报了一部分 | 读入 1200 / 生成 400 token（另有 1 次没报，实际更多） |
 *  | 一次都没报 | 用量未记录 |
 *
 *  **「这个数不是全部」必须写在屏幕上，不能只挂在 title 里**：作者要判断的正是这件事，
 *  title 是补一句为什么，不是藏一半真相的地方。 */
function TokenCell({ t }: { t: CostTotals }) {
  const language = useLanguage((s) => s.language);
  const unreported = t.calls - t.metered_calls;
  if (t.metered_calls === 0)
    return (
      <span
        title={
          language === "zh"
            ? "模型服务商未报告这些调用的用量；数字不可得，并非为零。"
            : "The model provider did not report usage for these calls; the number is unavailable, not zero."
        }
      >
        {language === "zh" ? "用量未记录" : "Usage not recorded"}
      </span>
    );
  return (
    <span
      title={
        unreported > 0
          ? language === "zh"
            ? `另有 ${unreported} 次调用未报告用量，此处仅为其余调用的合计。`
            : `${unreported} other call${unreported === 1 ? "" : "s"} reported no usage; this is the total of the rest only.`
          : undefined
      }
    >
      {language === "zh" ? (
        <>
          读入 {num(t.tokens_in, language)} / 生成 {num(t.tokens_out, language)} token
          {unreported > 0 ? `（另有 ${unreported} 次没报，实际更多）` : ""}
        </>
      ) : (
        <>
          in {num(t.tokens_in, language)} / out {num(t.tokens_out, language)} tokens
          {unreported > 0
            ? ` (${unreported} more call${unreported === 1 ? "" : "s"} not reported — actual usage is higher)`
            : ""}
        </>
      )}
    </span>
  );
}

/** 花费那一格 —— **和旁边的用量是同一对形状**（一个合计 + 一个「其中几条算得出」）。
 *
 *  这一格 2026-08-13 之前只有一句 `花费 {money(t.cost)}`，而那个合计**只算得上
 *  `priced_calls` 那几次**：自建端点、公开标价表里没有的模型、供应商没报 token 数的
 *  那几次都算不出钱。一个合计不说清它是不是全部，就是一句看起来确定的假话——
 *  后端 `CostTotals.priced_calls` 那段注释写的正是这件事，而**紧挨着的用量那一格
 *  早就照它写了**，只有这一格没跟上。
 *
 *  | 算得出几次 | 屏幕上 |
 *  |---|---|
 *  | 全算得出 | 花费 约 $0.34 |
 *  | 算得出一部分 | 花费 约 $0.34（另有 12 次算不出，实际更多） |
 *  | 一次都算不出 | 花费未记录 |
 *
 *  **「这个数不是全部」写在屏幕上，不是只挂在 title 里**（同 `TokenCell`）：
 *  作者要判断的正是这件事。 */
function CostCell({ t }: { t: CostTotals }) {
  const language = useLanguage((s) => s.language);
  const unpriced = t.calls - t.priced_calls;
  if (t.priced_calls === 0)
    return (
      <span
        title={
          language === "zh"
            ? "这些调用的费用无法计算：自建端点、公开价目表中没有的模型，或服务商未报告用量。数字不可得，并非为零。"
            : "The cost of these calls cannot be computed: a self-hosted endpoint, a model not in the public price list, or usage the provider did not report. The number is unavailable, not zero."
        }
      >
        {language === "zh" ? "花费未记录" : "Cost not recorded"}
      </span>
    );
  return (
    <span
      title={
        language === "zh"
          ? "按各服务商公开价目估算。实际账单可能因折扣、中转或免费额度而不同。" +
            (unpriced > 0 ? `另有 ${unpriced} 次调用无法计算费用，此处仅为其余调用的合计。` : "")
          : "Estimated from each provider's public pricing. The actual bill may differ because of discounts, relays or free quota." +
            (unpriced > 0
              ? ` The cost of ${unpriced} other call${unpriced === 1 ? "" : "s"} cannot be computed; this is the total of the rest only.`
              : "")
      }
    >
      {language === "zh" ? (
        <>
          花费 {money(t.cost, language)}
          {unpriced > 0 ? `（另有 ${unpriced} 次算不出，实际更多）` : ""}
        </>
      ) : (
        <>
          cost {money(t.cost, language)}
          {unpriced > 0
            ? ` (${unpriced} more call${unpriced === 1 ? "" : "s"} not priced — actual cost is higher)`
            : ""}
        </>
      )}
    </span>
  );
}

/** 这本书到今天为止用掉多少。**用量和花费各是一对「合计 + 其中几条算得出」**，
 *  两格都得说清那个数是不是全部（§10 约束 8：零和半个数都要带着理由）。 */
function UsageStrip() {
  const language = useLanguage((s) => s.language);
  const { projectId } = useCoords();
  const runs = useRuns(projectId);
  const data = runs.data;
  if (!data) return null;
  const t = data.totals;
  // 零带着理由：一排「0 次 / 0 token / 花费未记录」看起来像账没记上，
  // 而真相通常只是这本书还没让系统整理过。
  if (data.run_count === 0 && t.calls === 0)
    return (
      <div className="log-usage">
        {language === "zh" ? (
          <>尚无模型调用。整理本书后，用量将记录于此</>
        ) : (
          <>No model calls yet. Usage is recorded here once the book has been processed</>
        )}
      </div>
    );
  return (
    <div className="log-usage">
      <span>
        {language === "zh"
          ? `已整理 ${data.run_count} 次`
          : `Processed ${data.run_count} time${data.run_count === 1 ? "" : "s"}`}
      </span>
      <span>
        {language === "zh"
          ? `模型调用 ${t.calls} 次`
          : `${t.calls} model call${t.calls === 1 ? "" : "s"}`}
      </span>
      <TokenCell t={t} />
      <CostCell t={t} />
    </div>
  );
}

/** 谁做的 —— 作者亲手点的和系统自动干的必须一眼分得开，且能筛。
 *
 *  **计数是全量的，不随过滤变**：它要回答的正是「我筛掉了多少」。ADR 0020 点名过这件事
 *  ——自动升 CANON 开了之后 system 行会长得快得多，作者自己点过的那几十次会被淹掉。 */
function ActorFilter({
  actors,
  actor,
  onPick,
}: {
  actors: ActorTally[];
  actor: string | null;
  onPick: (next: string | null) => void;
}) {
  const language = useLanguage((s) => s.language);
  const total = actors.reduce((n, a) => n + a.count, 0);
  return (
    <div className="log-filters">
      <button aria-pressed={actor === null} className={actor === null ? "on" : ""} onClick={() => onPick(null)}>
        {language === "zh" ? `全部 ${total}` : `All ${total}`}
      </button>
      {actors.map((a) => (
        <button
          key={a.actor}
          aria-pressed={actor === a.actor}
          className={"log-actor-btn " + a.actor + (actor === a.actor ? " on" : "")}
          onClick={() => onPick(a.actor)}
        >
          {language === "zh"
            ? `${actorName(a.actor, language)}做的 ${a.count}`
            : `${a.count} by ${actorName(a.actor, language)}`}
        </button>
      ))}
    </div>
  );
}

function CostLine({ cost }: { cost: ActivityCost }) {
  const language = useLanguage((s) => s.language);
  return (
    <div className="log-cost">
      {language === "zh" ? (
        <>
          {cost.model} · 读入 {num(cost.tokens_in, language)} / 生成 {num(cost.tokens_out, language)}{" "}
          token · 用时 {cost.ms === null ? "未记录" : `${cost.ms} 毫秒`} · 花费{" "}
          {money(cost.cost, language)}
        </>
      ) : (
        <>
          {cost.model} · in {num(cost.tokens_in, language)} / out {num(cost.tokens_out, language)}{" "}
          tokens · took {cost.ms === null ? "not recorded" : `${cost.ms} ms`} · cost{" "}
          {money(cost.cost, language)}
        </>
      )}
    </div>
  );
}

/** 没跑成的那一条上那颗「再来一次」。**它不是跳转，是一次动作。**
 *
 *  作者截图里那条红的（「第 722 章抽取 · 没能连上你配置的模型服务 · 失败」）此前只配着
 *  一颗「去第 722 章 →」：`_run_jump` 一眼都不看跑成没跑成，成功和失败走同一条路径。
 *  **而重跑的能力后端一直都在**——那条路由把同一行 run 原地重置回排队，不新建行。
 *
 *  三条纪律：
 *  1. **说什么由后端定坐标、前端按界面语言渲染**（`jump.label_code`/`label_params`，
 *     码和 `target` 是同一次判断的两个产物），这里不编第二句；
 *  2. **`endpoints` 空就不画按钮**——那是一个断言（「今天没有任何路由能让这件事不一样」），
 *     不是没填。同这一页别处那条纪律；
 *  3. **打的就是 `endpoints[0]`**，只补一个 `force`：没有它，接口照样 202、
 *     那一行照旧红着（后端见到已经失败的那条 run 会原样还回来），也就是一颗点了
 *     没反应的按钮——比没有按钮更糟。两头钉着：pytest 那侧有一个「不带 force」的探针，
 *     vitest 这侧扫的是请求 URL。 */
function RetryRow({ jump }: { jump: ActivityJump }) {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const chapter = jump.chapter_number;
  const retry = useStartExtraction(projectId ?? "", chapter ?? 0);
  const refused = refusalText(retry.error, RETRY_FAILED(language));

  if (jump.endpoints.length === 0 || !projectId || chapter === null) return null;
  return (
    <div className="log-jump">
      <button
        className="log-go"
        disabled={retry.isPending}
        onClick={() => retry.mutate({ force: true })}
      >
        {language === "zh"
          ? retry.isPending ? "重新排队中…" : `${jumpLabel(jump, language)} →`
          : retry.isPending ? "Queueing again…" : `${jumpLabel(jump, language)} →`}
      </button>
      {retry.isSuccess && (
        <span className="log-jump-note">
          {language === "zh" ? (
            <>已重新排队，在后台处理；此行稍后更新。</>
          ) : (
            <>Queued again and processing in the background; this row updates shortly.</>
          )}
        </span>
      )}
      {refused && <span className="err-box">{refused}</span>}
    </div>
  );
}

/** 跳到对应模块。**坐标、能不能改由后端给，措辞按界面语言渲染。** */
function JumpRow({ entry, jump }: { entry: ActivityEntry; jump: ActivityJump }) {
  const openChapter = useOpenChapter();
  const jumpFromActivity = useCoords((s) => s.jumpFromActivity);
  const language = useLanguage((s) => s.language);
  const note = jumpNote(entry, jump, language);

  const go = () => {
    // 换章走全项目唯一那个入口（离开的那一章交后台整理），不裸 setChapter。
    if (jump.chapter_number !== null) openChapter(jump.chapter_number);
    jumpFromActivity({
      tab: TARGET_TAB[jump.target],
      // 高亮哪一格用后端给的两个 id。**不是从 subtitle 里的人名反解出来的**——
      // 名字解析可能歧义，而歧义时服务端的规矩是绝不替作者挑。
      // 同理：改哪条情节的名单用 `jump.event_id`，不从那行字里认。
      eventId: jump.event_id,
      // 改自动升上去的地点/状态/关系边用 `jump.edge_id`（`canon_edge` 那一档）——
      // 同一句规矩：坐标由后端给，前端不从那行字里认哪条边。
      edgeId: jump.edge_id,
    });
  };

  return (
    <div className="log-jump">
      <button className="log-go" onClick={go}>
        {jumpLabel(jump, language)} →
      </button>
      {note && <span className="log-jump-note">{note}</span>}
    </div>
  );
}

/** 展开层：这一步跑了什么、结果是什么、花了多少、去哪儿改。
 *
 *  **`payload` 一个字都不渲染。** 它是审计信封（给机器重放用的），里面是引擎内部字段；
 *  作者要看的那一份后端已经写成 `rows` 了。渲染信封 = 把研发术语摆到作者脸上，
 *  同时把日志页变成一个新的泄漏面。 */
function EntryDetail({ entry }: { entry: ActivityEntry }) {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const detail = useActivityDetail(projectId, entry.id);

  if (detail.isLoading)
    return <div className="log-detail dim">{language === "zh" ? "读取中…" : "Loading…"}</div>;
  if (detail.isError || !detail.data)
    return (
      <div className="log-detail err-box">
        {language === "zh"
          ? "此条记录的详情读取失败"
          : "The details of this entry could not be loaded"}
      </div>
    );

  const { rows, cost, errors, entry: full } = detail.data;
  return (
    <div className="log-detail">
      {rows.length > 0 && (
        <dl className="log-rows">
          {rows.map((r, i) => (
            <div className="log-rows-line" key={`${r.label_code}-${i}`}>
              <dt>{messageForCode(r.label_code, language) ?? r.label_code}</dt>
              <dd>{messageForCode(r.value_code, language, r.value_params) ?? r.value_code}</dd>
            </div>
          ))}
        </dl>
      )}
      {errors.length > 0 && (
        <div className="err-box">
          {errors.map((code, i) => (
            <div key={i}>{runErrorText(code, language)}</div>
          ))}
        </div>
      )}
      {cost && <CostLine cost={cost} />}
      {/* 「再来一次」和「跳过去」是两件事，判据是后端给的 `target`——**不是从
          `entry.status` 反推**：跑没跑成是引擎那一侧的判断，而它已经把结论写进坐标里了。 */}
      {full.jump &&
        (full.jump.target === "extraction_retry" ? (
          <RetryRow jump={full.jump} />
        ) : (
          <JumpRow entry={full} jump={full.jump} />
        ))}
    </div>
  );
}

/** 一行一条：折叠的时候只有「谁 · 做了什么 · 结果」，点开才去取详情。 */
function EntryRow({
  entry,
  open,
  onToggle,
}: {
  entry: ActivityEntry;
  open: boolean;
  onToggle: () => void;
}) {
  const language = useLanguage((s) => s.language);
  const time = shownTime(entry.ts, language);
  return (
    <li className={"log-item" + (open ? " open" : "")}>
      <button className="log-line" aria-expanded={open} onClick={onToggle}>
        <span className="log-fold" aria-hidden="true" />
        <span className={"log-actor " + entry.actor}>{actorName(entry.actor, language)}</span>
        <span className="log-title">
          {messageForCode(entry.title_code, language, entry.title_params) ?? entry.title_code}
        </span>
        <span className="log-sub">
          {messageForCode(entry.subtitle_code, language, entry.subtitle_params) ?? entry.subtitle_code}
        </span>
        {entry.status !== "succeeded" && (
          <span className={"log-status " + entry.status}>
            {STATUS_ZH[entry.status]?.[language] ?? entry.status}
          </span>
        )}
        {time && <span className="log-time">{time}</span>}
      </button>
      {open && <EntryDetail entry={entry} />}
    </li>
  );
}

/** 活动记录页 —— 占中栏（左栏书架和右栏面板照旧留在原地，跳转过去就是那一栏在动）。 */
export function ActivityLog() {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const [actor, setActor] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const log = useActivity(projectId, actor);

  const pages = log.data?.pages ?? [];
  const entries = pages.flatMap((p) => p.entries);
  // actors 取第一页那份就够：它是全量计数，翻页不会变（变了反而说明后端在骗人）。
  const actors = pages[0]?.actors ?? [];

  return (
    <section className="pane log">
      <div className="log-head">
        <h2>{language === "zh" ? "活动记录" : "Activity log"}</h2>
        <UsageStrip />
        <ActorFilter
          actors={actors}
          actor={actor}
          onPick={(next) => {
            setActor(next);
            setOpenId(null);
          }}
        />
      </div>

      {/* ⚠️ **「你交代过的」那块（`RulesTable`，ADR 0028 + 迁移 016）2026-09-09 从这儿撤了。**
          作者指着那一块和上面那段面板说明说「删除了」。撤的理由不是它做错了什么，是它
          在真书上**从来没有过一行**（`remember_rule` 一次都没开过火），于是这一页顶上
          常年挂着一个标题加一句「还没有」——占的是位置，给的是零。
          组件本身还在（`RulesTable.tsx` + 它的测试 + ADR），**但今天没有任何地方渲染它**，
          按本仓库「废弃即删」那条它要么找个新去处，要么整份删掉。等维护者裁定。 */}
      {log.isLoading && (
        <div className="empty">{language === "zh" ? "读取中…" : "Loading…"}</div>
      )}
      {log.isError && (
        <div className="err-box">
          {language === "zh" ? "活动记录读取失败" : "The activity log could not be loaded"}
        </div>
      )}
      {!log.isLoading && !log.isError && entries.length === 0 && (
        <div className="empty">
          {language === "zh"
            ? actor === null
              ? "尚无记录。整理本书后，每一步操作将记录于此"
              : `${actorName(actor, language)}在本书上尚无记录`
            : actor === null
              ? "Nothing recorded yet. Once the book has been processed, every step is recorded here"
              : `No record from ${actorName(actor, language)} on this book yet`}
        </div>
      )}

      <ul className="log-list">
        {entries.map((e) => (
          <EntryRow
            key={e.id}
            entry={e}
            open={openId === e.id}
            onToggle={() => setOpenId(openId === e.id ? null : e.id)}
          />
        ))}
        {/* **它在列表里，不在列表下面**（2026-09-09）。原来它是钉在滚动区外面的一条，
            于是一进这一页就看得见——而它说的是「已经到底了，还要更早的吗」，那句话在
            你还没往下翻的时候是没有意义的。挪进 `<ul>` 之后，只有真滚到当前这一批的
            末尾才会遇上它。
            **样子也不再像按钮**（作者原话「不要这种按钮感，直接文字点击就好」）：
            边框底色都抽掉，只剩一行 `--dim` 的字，悬浮转强调色——同这一屏其余每一处。
            **但它仍然是 `<button>`**：读屏要念得出「这是个能按的东西」，而一段
            `<span>` 加 onClick 念出来只是一段字（`icons.tsx` 开头第 3 条同一条理由）。 */}
        {log.hasNextPage && (
          <li className="log-more-row">
            <button
              className="log-more"
              disabled={log.isFetchingNextPage}
              onClick={() => log.fetchNextPage()}
            >
              {language === "zh"
                ? log.isFetchingNextPage ? "读取中…" : "查看更早之前的…"
                : log.isFetchingNextPage ? "Loading…" : "See earlier entries…"}
            </button>
          </li>
        )}
      </ul>
    </section>
  );
}
