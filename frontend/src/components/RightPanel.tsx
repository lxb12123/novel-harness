import {
  useConstraints,
  useMentioned,
  useStates,
  useCheck,
  useProposals,
  useRoster,
} from "../api/hooks";
import { ApiError } from "../api/client";
import { saidToTheAuthor } from "../correctionError";
import { useLanguage, type Language } from "../language";
import { useCoords, type Tab } from "../store";
import { LocalGraph } from "./LocalGraph";
import { EvidenceTab } from "./EvidenceTab";
import { StateTab } from "./StateCards";
import { ProposalReviewTab } from "./ProposalReviewTab";
import { RosterTab } from "./RosterTab";
import { SummaryTab } from "./SummaryTab";
import { SystemNotifications } from "./SystemNotifications";
import type { CheckResult } from "../api/types";
import { useState } from "react";

/** 算的是「其中某个人怎么样」的那几格 —— 只有它们需要知道在场是谁。
 *  花名册（全项目）、原文依据、待确认、检查（读正文）都不吃 cast。 */
const CAST_TABS = new Set<Tab>(["state", "constraints"]);

const TABS: { key: Tab; label: { zh: string; en: string } }[] = [
  { key: "roster", label: { zh: "花名册", en: "Roster" } },
  { key: "state", label: { zh: "人物状态", en: "Status" } },
  { key: "graph", label: { zh: "人物关系", en: "Relationships" } },
  { key: "evidence", label: { zh: "原文依据", en: "Evidence" } },
  { key: "constraints", label: { zh: "写作提醒", en: "Reminders" } },
  { key: "check", label: { zh: "检查", en: "Check" } },
  { key: "review", label: { zh: "待确认", en: "Pending" } },
  { key: "summary", label: { zh: "章节总结", en: "Summary" } },
  { key: "notifications", label: { zh: "通知", en: "Notifications" } },
];

/** 花名册空着的时候仍然有话可说的那几格。
 *
 *  其余每一格都是「其中某个人怎么样」，没有人就没有料。**章节总结不是**：它是这一章
 *  正文压出来的一段字，和花名册里有没有人一点关系都没有。把它一起藏进那句「先去加人」
 *  里，作者就会对着一个能用的功能读到一句不相干的话。 */
const ROSTER_FREE_TABS = new Set<Tab>(["roster", "summary"]);

function ConstraintsView() {
  const { projectId, chapter, cast, castInclude } = useCoords();
  const language = useLanguage((s) => s.language);
  const { data } = useConstraints(projectId, chapter, cast, castInclude);
  if (!data) return <div className="empty">—</div>;
  return (
    <div>
      <div className="mnr">
        <div className="lab">{language === "zh" ? "本章尚未登场" : "Not yet in this chapter"}</div>
        {data.forbidden_entities.length ? (
          data.forbidden_entities.map((e) => (
            <span className="tag" key={e.node.id}>
              {e.node.name}
              {language === "zh"
                ? `（第 ${e.first_appears_chapter} 章登场）`
                : ` (appears in chapter ${e.first_appears_chapter})`}
            </span>
          ))
        ) : (
          <span className="empty">{language === "zh" ? "（无）" : "(none)"}</span>
        )}
      </div>
      {data.unresolved_cast.length > 0 && (
        <div className="warn">
          {language === "zh"
            ? `这些称呼未在花名册中找到：${data.unresolved_cast.join("、")}。请检查名称或补充称呼。`
            : `These names weren't found in the roster: ${data.unresolved_cast.join(", ")}. Check the spelling, or add them as aliases.`}
        </div>
      )}
    </div>
  );
}

/** 「检查本章」失败时说什么。**曾经直接读 `(check.error as Error).message`**——
 *  `ApiError` 没有 `.message` 字段时那个 getter 退回 `body.error`，也就是原样把
 *  `chapter_not_found` 五个字母摆上屏（国际化第四批·裸错误码审计发现的真回归）。
 *  走 `saidToTheAuthor` 才是这份文件其余每一格拒绝共用的那条路。 */
function checkFailure(error: unknown, language: Language): string {
  if (error instanceof ApiError) {
    return (
      saidToTheAuthor(error) ??
      (language === "zh"
        ? "这次检查没能跑完，而系统没能说清是为什么。刷新一下再试一次。"
        : "This check couldn't finish, and the system couldn't say why. Refresh and try again.")
    );
  }
  return language === "zh"
    ? "没能连上服务，请再试一次。"
    : "Couldn't reach the service — please try again.";
}

function CheckView() {
  const { projectId, chapter, setHighlight } = useCoords();
  const language = useLanguage((s) => s.language);
  const check = useCheck(projectId!);
  const [result, setResult] = useState<CheckResult | null>(null);
  const summaryLine = !result
    ? ""
    : result.issues.length === 0
      ? language === "zh"
        ? "没有发现需要处理的问题。"
        : "No issues found."
      : language === "zh"
        ? `发现 ${result.issues.length} 处需要留意。`
        : `Found ${result.issues.length} issue${result.issues.length === 1 ? "" : "s"} to review.`;
  return (
    <div>
      <button
        disabled={!projectId || check.isPending}
        onClick={() => check.mutate(chapter, { onSuccess: setResult })}
      >
        {check.isPending
          ? language === "zh" ? "检查中…" : "Checking…"
          : language === "zh" ? "检查本章" : "Check this chapter"}
      </button>
      {check.error && <div className="err-box">{checkFailure(check.error, language)}</div>}
      {result && (
        <div style={{ marginTop: 10 }}>
          {/* 这两行 2026-08-14 改了，**其中一行是在改一句假话**：
              「已检查 N 个场景」和「这一章还没有场景信息，暂时无法进行内容检查」都是
              `ALL_CHECKS` 只有 R4 那会儿写的。R2/R3 在 2026-08-02 进来之后，
              **零场景块照样查了两条规则**（`checks/` 里只有 `location_conflict` 读
              `ctx.scenes`），而屏幕上说的是「无法进行内容检查」——
              和 `demo.sh` 那次是同一天、同一个原因断的，只是这一处没人发现。
              「场景」这个词一起去掉：它指的是作者要在正文里手写的标记块，
              导进来的真书上永远是零个，说了也只是把一个内部名字摆到他面前。 */}
          <div className="row" style={{ color: "var(--dim)", fontSize: 12 }}>
            {summaryLine}
          </div>
          {result.issues.map((iss, i) => (
            <div
              className="statecard clickable"
              key={i}
              title={language === "zh" ? "点击 → 跳到正文那一段并高亮" : "Click to jump to this passage and highlight it"}
              onClick={() => setHighlight(iss.anchor)}
            >
              <div className="nm">{language === "zh" ? "需要留意" : "Needs attention"}</div>
              {/* 段号按作者的数法从 1 起。`para_index` 内部是 0-based，这儿原本直接
                  印了它，于是第一段在屏幕上叫「第 0 段」——而同一条问题落成通知时
                  说的是「第 1 段」（`system_notifications` 那边换算过）。
                  同一处正文两个数，两处必须一致。 */}
              <div className="row">
                {language === "zh"
                  ? `第 ${iss.chapter} 章 · 第 ${iss.anchor.para_index + 1} 段 · 点击回到原文`
                  : `Chapter ${iss.chapter} · Paragraph ${iss.anchor.para_index + 1} · Click to jump to the passage`}
              </div>
              <div className="row">{iss.message}</div>
              {iss.suggested_action && (
                <div className="row">
                  {language === "zh" ? "建议：" : "Suggestion: "}
                  {iss.suggested_action}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 这几格算的是「其中某个人怎么样」，所以它们都得知道「谁」。作者不填这个「谁」——
 *  引擎从本章正文里数出来（`mentioned.py`），这一条把数出来的结果显示给作者看。
 *
 *  **措辞是「提到」不是「在场」，这是有意的。** 引擎在数字符串，没有读懂剧情：
 *  回忆里的死人、信里写到的名字都会进来。说「在场」等于向作者承诺一件系统做不到的事。
 *  （多算是安全的那一侧——`must_not_reveal` 的判据是「至少有一个人还不知道」。） */
function CastLine({ chapter }: { chapter: number }) {
  const { projectId, chapter: here, cast, setCast } = useCoords();
  const language = useLanguage((s) => s.language);
  const mentioned = useMentioned(projectId, chapter);
  // **「这一章」只在真的是作者停着的那一章上说得出口。** 「人物状态」那一格可以停在
  // 上一章（`StateTab` 的开关），而那时下面几张卡是**另一章**正文里数出来的人——
  // 这一行照旧说「这一章提到：…」就是在给一块讲第 12 章的面板配一句讲第 13 章的话。
  const which =
    language === "zh"
      ? chapter === here ? "这一章" : `第 ${chapter} 章`
      : chapter === here ? "this chapter" : `chapter ${chapter}`;

  // **这一行只说 `cast`（过滤）那一种**——收窄是作者自己要的，这一行存在的全部理由
  // 就是让他看见「现在只在看几个人」并且一键退出去。
  //
  // ⚠️ **2026-08-14 起 `cast` 在界面上没有写入方了**：唯一那两个（场景条 / 底栏场景列）
  // 随场景块一起删了（ADR 0027）。这一支因此今天渲不出来。**留着它不是忘了删**：
  // `?cast=` 这条通道在后端还在，而 `JumpCast.coord.test.tsx` 那条探针正是靠直接写
  // `cast` 来证明「系统替作者收窄」这件事真的抓得住——把这一支删掉，那条守卫就
  // 没有了它要守的形态。
  //
  // 日志页跳过来的那个坐标**不在这里**：它走 `castInclude`（只加不减），没有把任何人
  // 从表上拿掉，所以没有「退出去」这回事。系统要是能往 `cast` 里写，这一行就得替它
  // 认罪，而正确的做法是不让它写（ADR 0018 §3：少一个人 = 少一批禁令）。
  if (cast) {
    return (
      <div className="castline">
        <span className="lab">{language === "zh" ? "只看：" : "Showing only:"}</span>
        <span className="who">{cast}</span>
        <button className="link" onClick={() => setCast("")}>
          {language === "zh" ? "改回整章" : "Show the whole chapter"}
        </button>
      </div>
    );
  }
  if (!mentioned.data) return null;
  if (!mentioned.data.has_text) {
    return (
      <div className="castline dim">
        {language === "zh"
          ? `${which}还没有正文，下面按「这一场不知道有谁」显示。`
          : `${which} doesn't have any text yet, so everything below assumes nobody's presence is known.`}
      </div>
    );
  }
  if (!mentioned.data.surfaces.length) {
    return (
      <div className="castline dim">
        {language === "zh"
          ? `${which}的正文里没有出现花名册中的人。到「花名册」那一格补上，这里就会认出他们。`
          : `No one from the roster appears in ${which}'s text yet. Add them on the "Roster" tab and this will recognize them.`}
      </div>
    );
  }
  return (
    <div className="castline">
      <span className="lab">{language === "zh" ? `${which}提到：` : `${which} mentions:`}</span>
      <span className="who">{mentioned.data.surfaces.join(language === "zh" ? "、" : ", ")}</span>
    </div>
  );
}

export function RightPanel() {
  const { projectId, chapter, cast, castInclude, activeTab, setTab } = useCoords();
  const language = useLanguage((s) => s.language);
  // 「人物状态」那一格在看哪一章。**只有这一格**——`[valid_from, valid_to)` 那台时光机
  // 一直建好着，但右栏此前永远只问「当前章」。开关的语义是「本章 / 上一章」两个位置，
  // 作者敲不进任何一个章号（约束 10 的理由写在 `StateTab` 的 docstring 里）。
  //
  // **存的是「他在第几章上按下了这个开关」，不是一个布尔。** 换来的性质只有一条，
  // 但它正是这块屏幕全部的风险所在：
  //
  //     没在那一章上按过这个开关，就永远不会落在上一章模式里。
  //
  // 开着的时候屏幕上是**另一章**的局面，而作者照着它去改本章的事实，造出来的是一条
  // `valid_from` 错了的 CANON 边——在面板上长得完全正常。存布尔的话第 1 章那一档会长出
  // 一个**说谎的控件**：按钮画成没按下（那一章没有上一章），而心里那个 `true` 还留着，
  // 他翻到第 5 章面板就自己跳了进去，而他最后看见的是一颗没按下的按钮。
  // 存章号让那种状态**表示不出来**——按钮的样子永远等于面板的样子。
  // （代价是它只记得住最近按过的那一章，而那个方向是安全的：记岔了 = 回到本章。）
  //
  // 它是这一格的显示模式、不是坐标，所以留在这儿而不进 `store.ts`。
  const [lookBackAt, setLookBackAt] = useState<number | null>(null);
  const prevChapter = chapter - 1;
  const lookingBack = lookBackAt === chapter && prevChapter >= 1;
  const stateChapter = lookingBack ? prevChapter : chapter;
  // **在场也跟着那一章走**：后端的 `_effective_cast` 从**路径上那一章**的正文里数人
  // （ADR 0018），所以这条一换章号，卡片上的人就是第 N-1 章提到的那几个——
  // 这正是「上一章结束时是什么局面」要的那一份，也是被删掉的「章节准备」页当时的算法。
  const states = useStates(projectId, stateChapter, cast, castInclude);
  const proposals = useProposals(projectId, chapter);
  const roster = useRoster(projectId);
  const pendingCount = (proposals.data ?? []).filter((p) => p.status === "PENDING").length;
  const tabs = TABS.map((t) => {
    const base = t.label[language];
    if (t.key === "review") return { key: t.key, label: `${base} ${pendingCount}` };
    // 面板一滚下去横幅就看不见了，而标签一直在。**两处说同一件事是有意的**：
    // 「在看的不是本章」漏掉一次，作者就会照着上一章的局面改这一章的事实。
    if (t.key === "state" && lookingBack) {
      return {
        key: t.key,
        label: language === "zh" ? `${base} · 第 ${prevChapter} 章` : `${base} · Chapter ${prevChapter}`,
      };
    }
    return { key: t.key, label: base };
  });

  // 花名册空 = 其余每一格都没有料可显示（它们全都是「其中某个人怎么样」）。
  // **但不能整块 return 掉**：花名册自己就是那一格，连它一起藏起来的话，
  // 「＋」也跟着没了——作者会停在一个说着「先加人」却没有加人入口的面板上。
  const bare = !roster.data?.length;

  return (
    <section className="pane">
      <div className="tabs">
        {tabs.map((t) => (
          <button key={t.key} className={activeTab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {/* 「这一格算的是谁」这句话必须跟着那一格真正问的那一章走 —— 「人物状态」停在
          上一章时，它说的是第 N-1 章提到了谁。 */}
      {!bare && CAST_TABS.has(activeTab) && (
        <CastLine chapter={activeTab === "state" ? stateChapter : chapter} />
      )}
      {activeTab === "roster" && <RosterTab />}
      {activeTab === "summary" && <SummaryTab />}
      {bare && !ROSTER_FREE_TABS.has(activeTab) && (
        <div className="empty workbench-empty">
          {language === "zh" ? (
            <>
              添加人物或设定后，这里会显示他们在当前章节知道什么、身处何处，以及需要留意的内容。
              回到「花名册」那一格，点“＋”开始。
            </>
          ) : (
            <>
              Once you add characters or settings, this will show what they know, where they are,
              and anything worth watching for in the current chapter. Go back to the "Roster" tab
              and click “＋” to get started.
            </>
          )}
        </div>
      )}
      {!bare && activeTab === "state" && (
        <StateTab
          states={states.data}
          chapter={chapter}
          lookingBack={lookingBack}
          onLookBack={(on) => setLookBackAt(on ? chapter : null)}
        />
      )}
      {!bare && activeTab === "graph" && <LocalGraph />}
      {!bare && activeTab === "evidence" && <EvidenceTab />}
      {!bare && activeTab === "constraints" && <ConstraintsView />}
      {!bare && activeTab === "check" && <CheckView />}
      {!bare && activeTab === "review" && <ProposalReviewTab />}
      {activeTab === "notifications" && <SystemNotifications />}
    </section>
  );
}
