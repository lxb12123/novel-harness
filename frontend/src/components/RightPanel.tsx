import {
  useAddValidationRule,
  useChapterText,
  useCheck,
  useDeleteValidationRule,
  useEvents,
  useNotificationsCount,
  useRoster,
  useUpdateValidationRule,
  useValidationRules,
} from "../api/hooks";
import { ApiError } from "../api/client";
import { SYSTEM_RULE_TEXT } from "../backendMessages";
import {
  BoltIcon,
  CloseIcon,
  EventsIcon,
  NotificationsIcon,
  PencilIcon,
  RosterIcon,
  RulesIcon,
  SummaryIcon,
} from "./icons";
import { saidToTheAuthor } from "../correctionError";
import { useLanguage, type Language } from "../language";
import { useCoords, type Tab } from "../store";
import { ProposalReviewTab } from "./ProposalReviewTab";
import { RosterTab } from "./RosterTab";
import { SummaryTab } from "./SummaryTab";
import { SystemNotifications } from "./SystemNotifications";
import type { ValidationRuleView } from "../api/types";
import { useEffect, useRef, useState } from "react";

/** **2026-08-31：「写作提醒」（未登场实体提醒）连同它背后的 `forbidden_entities`
 *  一起删了**（[ADR 0041](../../../docs/adr/0041-forbidden-entities-cut.md)），
 *  这一格里其余每一格本来就不吃 cast。
 *
 *  **2026-08-31 之前这里还有「人物状态」**，当时两格共用一个 Set；那一格并进
 *  角色册之后（见 `RosterTab.tsx` 的 `CharacterStatus`），只剩这一格，
 *  直接判等比再维护一个单元素 Set 直白。 */

/** `review` 这个 key 没跟着改名：它是内部路由，作者从来看不到这个字符串，
 *  改标签就够了——真要改 key 还得动 `store.ts` 的 `Tab` 联合类型和任何持久化
 *  过的旧值，成本换不来任何看得见的差别。 */
const TABS: {
  key: Tab;
  label: { zh: string; en: string };
  Icon: () => JSX.Element;
}[] = [
  { key: "roster", label: { zh: "角色册", en: "Roster" }, Icon: RosterIcon },
  { key: "check", label: { zh: "检验规则", en: "Rules" }, Icon: RulesIcon },
  { key: "review", label: { zh: "事件", en: "Events" }, Icon: EventsIcon },
  { key: "summary", label: { zh: "章节总结", en: "Summary" }, Icon: SummaryIcon },
  { key: "notifications", label: { zh: "通知", en: "Notifications" }, Icon: NotificationsIcon },
];

/** 角色册空着的时候仍然有话可说的那几格。
 *
 *  其余每一格都是「其中某个人怎么样」，没有人就没有料。**章节总结不是**：它是这一章
 *  正文压出来的一段字，和角色册里有没有人一点关系都没有。把它一起藏进那句「先去加人」
 *  里，作者就会对着一个能用的功能读到一句不相干的话。 */
const ROSTER_FREE_TABS = new Set<Tab>(["roster", "summary"]);


/** 「检查本章」失败时说什么。**曾经直接读 `(check.error as Error).message`**——
 *  `ApiError` 没有 `.message` 字段时那个 getter 退回 `body.error`，也就是原样把
 *  `chapter_not_found` 五个字母摆上屏（国际化第四批·裸错误码审计发现的真回归）。
 *  走 `saidToTheAuthor` 才是这份文件其余每一格拒绝共用的那条路。 */
function checkFailure(error: unknown, language: Language): string {
  if (error instanceof ApiError) {
    return (
      saidToTheAuthor(error) ??
      (language === "zh"
        ? "本次检验未完成，未返回原因。请刷新后重试。"
        : "This check did not complete and no reason was returned. Refresh and try again.")
    );
  }
  return language === "zh"
    ? "无法连接服务，请重试。"
    : "The service could not be reached; try again.";
}

/** 规则表里的一行：序号 · 内容 · 启用开关 · 改 · 删。
 *
 *  ⚠️ **行里显示的是作者写的那个词本身**（`config.literal`），不是标题。标题在库里
 *  就等于那个词（`POST` 不传 title 时后端拿 literal 兜底），显示 literal 之后
 *  **改词不会留下一个说着旧词的标题**——那是「同一件事两处存」的老病。
 *  系统规则（今天一条都没有）没有 literal，走 `SYSTEM_RULE_TEXT` 那条：标题 + 说明。
 */
function RuleRow({
  rule,
  index,
  language,
  onToggle,
  onRename,
  onDelete,
}: {
  rule: ValidationRuleView;
  index: number;
  language: Language;
  onToggle?: (next: boolean) => void;
  onRename?: (next: string) => void;
  onDelete?: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const system = SYSTEM_RULE_TEXT[rule.rule_id]?.[language];
  const word = rule.config?.literal ?? "";
  const body = system ? (
    <>
      <b>{system.title}</b>
      {system.desc ? (language === "zh" ? `：${system.desc}` : `: ${system.desc}`) : ""}
    </>
  ) : (
    // **一行读起来就是一条规则**，不是一个词。作者 2026-09-05：「为什么会是禁用词啊？
    // 这是个检查的规则……文章要是不符合这个规则就像闸门一样拦住。」——「禁用词」把它
    // 说成了内容安全里的黑名单，而它是作者给自己这本书定的一条硬要求。
    // 句子在这儿现拼，**不进库**：库里只存那个词，改词不会剩下一句说着旧词的话。
    language === "zh" ? `不许出现「${word || rule.title}」` : `Must not contain "${word || rule.title}"`
  );

  const commit = () => {
    const next = (editing ?? "").trim();
    setEditing(null);
    if (next && next !== word) onRename?.(next);
  };

  return (
    <div className={"rule-row" + (rule.enabled ? "" : " off")}>
      <span className="rule-no">{String(index + 1).padStart(2, "0")}</span>
      {editing === null ? (
        <span className="rule-body">{body}</span>
      ) : (
        <input
          className="rule-edit"
          autoFocus
          value={editing}
          onChange={(e) => setEditing(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setEditing(null);
          }}
        />
      )}
      {onToggle && (
        <>
          <button
            type="button"
            role="switch"
            aria-checked={rule.enabled}
            aria-label={
              language === "zh" ? `${rule.enabled ? "停用" : "启用"}这条规则` : "Toggle this rule"
            }
            className={"switch" + (rule.enabled ? " on" : "")}
            onClick={() => onToggle(!rule.enabled)}
          >
            <span className="knob" />
          </button>
          {/* 开关旁边那两个字**说的是当前状态**，不是按钮名（按钮名在 aria-label 上）。
              开着写「启用」、关着写「停用」——一个孤零零的开关，作者得先猜哪边是开。 */}
          <span className="switch-tag">
            {language === "zh" ? (rule.enabled ? "启用" : "停用") : rule.enabled ? "On" : "Off"}
          </span>
        </>
      )}
      {onRename && (
        <button
          type="button"
          className="row-act"
          aria-label={language === "zh" ? "改这条规则" : "Edit this rule"}
          onClick={() => setEditing(word)}
        >
          <PencilIcon />
        </button>
      )}
      {onDelete && (
        <button
          type="button"
          className="row-act"
          aria-label={language === "zh" ? "删掉这条规则" : "Delete this rule"}
          onClick={onDelete}
        >
          <CloseIcon />
        </button>
      )}
    </div>
  );
}

function CheckView() {
  const { projectId, chapter } = useCoords();
  const language = useLanguage((s) => s.language);
  const check = useCheck(projectId!);
  // **默认规则要写在屏幕上**（2026-09-04 作者：「这种默认的规则就应该写上去，
  // 而不是屏幕上没写的那个默认的规则」）：这颗按钮会去查什么，作者点之前就该看得见，
  // 否则「没有发现问题」这句话他没法判断是「书没问题」还是「根本没查那件事」。
  // 这条端点（`GET /validation-rules`）返回系统规则 + 作者自定义规则，
  // 而它的四个 hook 在这一天之前**一个组件都没调用过**。
  const rules = useValidationRules(projectId);
  const addRule = useAddValidationRule(projectId ?? "");
  const removeRule = useDeleteValidationRule(projectId ?? "");
  const patchRule = useUpdateValidationRule(projectId ?? "");
  const [literal, setLiteral] = useState("");
  const system = (rules.data ?? []).filter((rule) => rule.template === "system");
  const mine = (rules.data ?? []).filter((rule) => rule.template !== "system");
  const submitRule = () => {
    const text = literal.trim();
    if (!text) return;
    // **不传 title**：后端拿 literal 兜底，表里那一行显示的也是 literal——
    // 一个词只存一处，改词才不会剩下一个说着旧词的标题。
    addRule.mutate({ literal: text }, { onSuccess: () => setLiteral("") });
  };
  /* 检验完说一句话，**过几秒自己消失**（作者 2026-09-05：「你这个文字不要一直留在
     这边」）。留得住的那一份在「通知」那一栏——查出问题时后端会落一条
     `validation_blocked`，带着第一条命中的锚，点得过去。所以这儿只需要一句回执，
     不需要把命中清单再摆一遍。 */
  /* 那颗闪电的三态（作者 2026-09-05 定的）：
     **紫 = 还没验过 · 金黄 = 正在验 · 绿 = 验过了**。
     绿**不按秒退**，它一直亮到这一章的正文变了为止——「这一章验过没有」是一件关于
     这一版正文的事实，不是一个动画。所以拿**验的时候那一版的 `text_sha256`** 和当前
     这一版比：一样就还是绿的，作者改完存了盘就自己变回紫。 */
  const [checkedSha, setCheckedSha] = useState<string | null>(null);
  const chapterText = useChapterText(projectId, chapter, !!projectId);
  const fresh = checkedSha !== null && checkedSha === chapterText.data?.text_sha256;
  const boltPhase = check.isPending ? "busy" : fresh ? "done" : "idle";
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | null>(null);
  const say = (line: string) => {
    setToast(line);
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4200);
  };
  // 卸载时把定时器收掉，否则切走再切回来会看到上一次那句话。
  useEffect(() => () => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
  }, []);
  const on = mine.filter((rule) => rule.enabled).length;

  return (
    <div className="checkview">
      {/* **系统那一组空着就整组不渲染**（2026-09-05 起它一直是空的，ADR 0042 砍了最后
          一条系统规则）——维护者的话：「我们只是把系统那个隐藏了……因为指不定我们以后
          也有规则。」所以这儿判的是**空不空**，不是写死「没有系统规则」。 */}
      {system.length > 0 && (
        <>
          <div className="rules-sec">
            <span className="lab">{language === "zh" ? "内置规则" : "Built-in rules"}</span>
          </div>
          {system.map((rule, i) => (
            <RuleRow key={rule.rule_id} rule={rule} index={i} language={language} />
          ))}
        </>
      )}

      <div className="rules-sec">
        <span className="lab">{language === "zh" ? "自定义规则" : "Custom rules"}</span>
        {mine.length > 0 && (
          <span className="rules-count">
            {language === "zh" ? `已启用 ${on} / ${mine.length} 条` : `${on} / ${mine.length} on`}
          </span>
        )}
        {/* 「检验本章」**收成这一行末尾的一颗闪电**（作者 2026-09-05 定的位置）。
            名字由 `data-tip` 悬浮画出来（`.icon-btn::after`，全仓库只此一份）——
            **不用原生 `title`**：那个要等约一秒，作者的原话是「以为没有」。 */}
        <button
          type="button"
          className={`icon-btn tip-right rules-run ${boltPhase}`}
          aria-label={language === "zh" ? "快速检验本章" : "Check this chapter"}
          data-tip={
            check.isPending
              ? language === "zh" ? "检验中…" : "Checking…"
              : language === "zh" ? "快速检验本章" : "Check this chapter"
          }
          disabled={!projectId || check.isPending}
          onClick={() =>
            check.mutate(chapter, {
              onSuccess: (report) => {
                setCheckedSha(report.text_sha256);
                say(
                  report.issues.length === 0
                    ? language === "zh"
                      ? "没有发现需要处理的问题"
                      : "No issues found"
                    : language === "zh"
                      ? `发现 ${report.issues.length} 处，已放进「通知」`
                      : `Found ${report.issues.length} — see Notices`,
                );
              },
            })
          }
        >
          <BoltIcon />
        </button>
      </div>
      {mine.map((rule, i) => (
        <RuleRow
          key={rule.rule_id}
          rule={rule}
          index={i}
          language={language}
          onToggle={(next) => patchRule.mutate({ ruleId: rule.rule_id, enabled: next })}
          onRename={(next) => patchRule.mutate({ ruleId: rule.rule_id, literal: next })}
          onDelete={() => removeRule.mutate(rule.rule_id)}
        />
      ))}
      <div className="rule-add">
        {/* 只收「禁用词」这一样东西：引擎逐段做精确子串匹配，**不执行作者写的代码、
            不上正则、不把这句话交给模型**。所以这儿不能有第二个输入框去收「用自然语言
            描述的规矩」——那种规矩归写作助手读，不归这一步查。 */}
        <input
          className="rule-new"
          value={literal}
          onChange={(e) => setLiteral(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitRule();
          }}
          placeholder={
            language === "zh"
              ? "添加章节检验规则，回车保存"
              : "Add a rule for this chapter, press Enter to save"
          }
        />
      </div>

      {check.error && <div className="err-box">{checkFailure(check.error, language)}</div>}
      {toast && <div className="check-toast">{toast}</div>}
    </div>
  );
}

/** 这几格算的是「其中某个人怎么样」，所以它们都得知道「谁」。作者不填这个「谁」——
 *  引擎从本章正文里数出来（`mentioned.py`），这一条把数出来的结果显示给作者看。
 *
 *  **措辞是「提到」不是「在场」，这是有意的。** 引擎在数字符串，没有读懂剧情：
 *  回忆里的死人、信里写到的名字都会进来。说「在场」等于向作者承诺一件系统做不到的事。
 *  （多算是安全的那一侧——`must_not_reveal` 的判据是「至少有一个人还不知道」。） */
export function RightPanel() {
  const { projectId, chapter, activeTab, setTab } = useCoords();
  const language = useLanguage((s) => s.language);
  const roster = useRoster(projectId);
  // 2026-08-31：待确认提案搬进了通知面板（`SystemNotifications.tsx` +
  // `proposal_notifications.py`），数字 badge 跟着一起搬——`/notifications/count`
  // 现在已经把它们算进去了，不用在这儿再单独查一次提案状态。
  const notificationsCount = useNotificationsCount(projectId);
  // 待确认的**情节**也算进这个数（作者 2026-09-05：「这个页面的通知数字，应该也
  // 包括待确认的事件」）。
  //
  // **这一半只能在前端加**，不是图省事：后端那条 `/notifications/count` 是**整本书**
  // 的口径，而这一摞 PROVISIONAL 情节是**「到当前章为止」**的（同这一格底下画出来的
  // 那一份）。在后端相加要么给它传一个章号（多一个会漂的参数），要么两个口径混在一个
  // 数里——那时 badge 和它底下真画出来的条目数会对不上，而那正是 2026-08-31 把提案
  // 算进这个数时立的规矩：**badge = 这一格底下真的有几件事等你看**。
  // 这里读的是和 `SystemNotifications` 同一个 query（react-query 去重），所以两个数
  // 不可能分叉。
  const provisional = useEvents(projectId, chapter, "PROVISIONAL");
  const waiting =
    (notificationsCount.data?.open ?? 0) + (provisional.data?.length ?? 0);
  const tabs = TABS.map((t) => {
    const base = t.label[language];
    if (t.key === "notifications" && waiting > 0) {
      return { key: t.key, label: `${base} ${waiting}`, Icon: t.Icon };
    }
    return { key: t.key, label: base, Icon: t.Icon };
  });

  // 角色册空 = 其余每一格都没有料可显示（它们全都是「其中某个人怎么样」）。
  // **但不能整块 return 掉**：角色册自己就是那一格，连它一起藏起来的话，
  // 「＋」也跟着没了——作者会停在一个说着「先加人」却没有加人入口的面板上。
  const bare = !roster.data?.length;

  return (
    <section className="pane">
      <div className="tabs">
        {/* **图标在左、字在右**（作者 2026-09-09；此前是反过来的，2026-09-06 定的）。
            图标是 `aria-hidden` 的，所以这颗按钮的可及名字仍然只是那几个字——
            读屏念出来的和以前一样，`getByRole("button", { name: "检验规则" })`
            那批断言换边也不用改。 */}
        {tabs.map((t) => (
          <button key={t.key} className={activeTab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            <t.Icon />
            <span>{t.label}</span>
          </button>
        ))}
      </div>
      {activeTab === "roster" && <RosterTab />}
      {activeTab === "summary" && <SummaryTab />}
      {bare && !ROSTER_FREE_TABS.has(activeTab) && (
        <div className="empty workbench-empty">
          {language === "zh" ? (
            <>
              添加人物或设定后，可在此查看其状态、关系、依据和参与的事件。在「角色册」中点击「＋」添加。
            </>
          ) : (
            <>
              Once characters or settings are added, their status, relationships, evidence and
              events appear here. Click “＋” in "Roster" to add one.
            </>
          )}
        </div>
      )}
      {!bare && activeTab === "check" && <CheckView />}
      {!bare && activeTab === "review" && <ProposalReviewTab />}
      {activeTab === "notifications" && <SystemNotifications />}
    </section>
  );
}
