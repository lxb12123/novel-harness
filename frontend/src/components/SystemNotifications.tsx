import { useMemo, useState } from "react";
import {
  useAllProposals,
  useConfirmProvisional,
  useEvents,
  useIgnoreNotification,
  useNotifications,
  useProjects,
  useReviewProposal,
  useRoster,
  useUndoTocSkip,
} from "../api/hooks";
import { messageForCode } from "../backendMessages";
import { refusalText } from "../chat";
import { readCorrectionError } from "../correctionError";
import type {
  EdgeConflictItem,
  EventView,
  LowConfidenceEventItem,
  NodeRef,
  ProposalAction,
  ProposalEditInput,
  ProposalRecord,
  SystemNotification,
} from "../api/types";
import { useCoords } from "../store";
import { type Language, useLanguage } from "../language";
import { useOpenChapter } from "../chapterNavigation";
import { CastPicker, DIMENSIONS, candidates, idsOf, same } from "./CastPicker";
import type { Dimension } from "./CastPicker";

/** `title_code` 认得就整句渲染；认不出（还没配文案的码）或者 `null`
 *  （迁移 033 之前创建的旧通知，历史行不回填——同 `decision_log` 的既有纪律）
 *  就原样显示码本身，不编一句话糊弄过去（国际化第四批 Phase B）。**永远不返回
 *  空字符串**：一张没有正文的通知卡片比一句"码认不出来"更容易被当成 bug 忽略过去。 */
function noticeBody(item: SystemNotification, language: Language): string {
  if (!item.title_code) return "—";
  return messageForCode(item.title_code, language, item.title_params ?? {}) ?? item.title_code;
}

// 系统通知（Task 10 / 021，前端 Task 14）：右栏那一格。
//
// 六种语义：总结可能与正文不一致（只告警）、后台失败、正文验证阻断（保留了旧
// 结果）、保存后的语义核对（026，**只告警**）、这一章整理完但一件没留下（027）、
// 导入时跳过了目录页假章（032，带「撤销」）。每一条是「要作者知道、也许要点一下
// 确认」的东西，不是错误——它永远不会是因为引擎坏了。
//
// **坐标和动作全由后端给**：`jump`（TextAnchor / 章号）定位，`actions` 是可点
// 的动作。前端不从那行字里认「去哪儿改」。
//
// ⚠️ 这张表**必须罩住每一个 kind**（`Record<…["kind"], string>` 会在漏一个时让
// tsc 红）。漏了的话那一档会把 `text_advisory` 这种机器码原样摆到作者屏幕上——
// `screenGuard` 的 snake_case 那张网正是为这种兜底存在的。
//
// **2026-08-31：多了两档，`proposal_conflict` / `proposal_low_confidence`。**
// 它们走的是完全不同的渲染路（`ProposalNotificationRow`，見下方），這张表
// 仍然要罩住它们只是为了让 tsc 的穷举检查继续覆盖它们——真正显示的标题
// 由 `ProposalNotificationRow` 自己拼，不读这张表。
const KIND_TITLE: Record<SystemNotification["kind"], { zh: string; en: string }> = {
  summary_mismatch: {
    zh: "总结与正文可能不一致",
    en: "The summary may not match the text",
  },
  background_failure: {
    zh: "后台任务未完成",
    en: "A background task did not finish",
  },
  validation_blocked: {
    zh: "本章检验需要处理",
    en: "This chapter's check needs attention",
  },
  // 措辞刻意和上一条分开：那一条**停掉了**这一章的自动整理，这一条没有。
  text_advisory: {
    zh: "此段落建议复核",
    en: "This passage may need review",
  },
  // 措辞刻意不含「失败」二字：这一次没失败，只是一件都没留下（027）。
  extraction_yielded_nothing: {
    zh: "本章未整理出内容",
    en: "Nothing was extracted from this chapter",
  },
  // 措辞刻意不说「切章器出错」——它没错，是这本书自带了一页跟正文长得一样的目录（032）。
  import_toc_skipped: {
    zh: "导入时跳过了空章节",
    en: "Empty chapters were skipped during import",
  },
  // 更贴身的家在角色卡的事件时间线上（`CharacterEventRow.cast_changed`），
  // 这张通用面板只是它的第二个出口——标题照样不能漏，漏了就是 034 之前
  // 那批码原样摆屏幕的同一种坏法。
  event_cast_changed: {
    zh: "事件参与者已变更",
    en: "An event's cast changed",
  },
  proposal_conflict: { zh: "关系冲突", en: "Conflicting fact" },
  proposal_low_confidence: { zh: "待确认的情节", en: "Event awaiting confirmation" },
};

function NotificationRow({
  item,
  onHandled,
}: {
  item: SystemNotification;
  onHandled: (id: string) => void;
}) {
  const { projectId, setHighlight } = useCoords();
  const openChapter = useOpenChapter();
  const ignore = useIgnoreNotification(projectId ?? "");
  const undoTocSkip = useUndoTocSkip(projectId ?? "");
  const language = useLanguage((s) => s.language);
  const failure =
    refusalText(
      ignore.error,
      language === "zh" ? "忽略失败，请重试。" : "This notification could not be dismissed; try again.",
    ) ??
    refusalText(undoTocSkip.error, language === "zh" ? "撤销失败，请重试。" : "Undo failed; try again.");

  const go = () => {
    if (item.chapter_number !== null) openChapter(item.chapter_number);
  };

  // 带锚的那些（规则报的问题、保存后核对出来的问题）能一路点到那一句上：换章 +
  // 设 highlight，编辑器按 quote 重寻并滚进视野（`anchor.ts::locate`，永不用 offset）。
  // **锚是后端给的**，前端不从标题里认位置。
  const goToQuote = () => {
    go();
    if (item.jump) setHighlight(item.jump);
  };

  return (
    <div className="notice-card">
      <div className="notice-head">
        <span className="lab">{KIND_TITLE[item.kind]?.[language] ?? item.kind}</span>
        {item.chapter_number !== null && (
          <span className="row dim">
            {language === "zh" ? `第 ${item.chapter_number} 章` : `Chapter ${item.chapter_number}`}
          </span>
        )}
      </div>
      <div className="row">{noticeBody(item, language)}</div>
      <div className="actions">
        {item.jump ? (
          <button className="link" onClick={goToQuote}>
            {language === "zh" ? "查看原句 →" : "Go to the sentence →"}
          </button>
        ) : (
          item.chapter_number !== null && (
            <button className="link" onClick={go}>
              {language === "zh" ? "查看该章 →" : "Go to the chapter →"}
            </button>
          )
        )}
        {item.actions.includes("ignore") && (
          <button
            className="link"
            disabled={ignore.isPending}
            onClick={() =>
              ignore.mutate(item.id, { onSuccess: () => onHandled(item.id) })
            }
          >
            {language === "zh"
              ? ignore.isPending ? "忽略中…" : "忽略"
              : ignore.isPending ? "Dismissing…" : "Dismiss"}
          </button>
        )}
        {item.actions.includes("undo_toc_skip") && (
          <button
            className="link"
            disabled={undoTocSkip.isPending}
            onClick={() =>
              undoTocSkip.mutate(item.id, { onSuccess: () => onHandled(item.id) })
            }
          >
            {language === "zh"
              ? undoTocSkip.isPending ? "撤销中…" : "撤销"
              : undoTocSkip.isPending ? "Undoing…" : "Undo"}
          </button>
        )}
      </div>
      {failure && <div className="err-box">{failure}</div>}
    </div>
  );
}

/** 同一档通知攒到这个数就折叠成一行。
 *
 *  ── 为什么需要这个数（2026-08-25）────────────────────────────────────
 *
 *  这一批之前，一本 158 章的老书只跑过 3 次抽取（只有保存过的章才排得上）。
 *  定期扫描从这一天起也管抽取了（`summary_schedule.ChapterSummaryState.needs_work`），
 *  于是**其余 155 章会第一次被整理**——其中每一章「一件都没留下」都落一条通知
 *  （引语对不上、称呼有歧义，都还会发生）。
 *
 *  一次冒出上百张卡片，每张都带「去这一章」和「不再提醒这一条」两颗按钮，
 *  等于把「要作者知道」变成「作者关掉这一格」。**这不是美化，是让它还读得下去。**
 *
 *  3 以下不折叠：一两条的时候摊开更好读，而且这样既有的形状一个字节没变。
 *
 *  **待确认提案不参与折叠**（2026-08-31）：折叠是给"知道就好"的告警省视线的，
 *  提案是"需要你做一个决定"的队列，把它们藏进"逐条看"背后等于多按一次才能
 *  开始干活——`ProposalNotificationRow` 一直逐条摊开。 */
const COLLAPSE_AT = 4;

/** 一档折叠起来的通知。**逐条的动作一个都没少**，只是默认收着。 */
function CollapsedKind({
  items,
  onHandled,
}: {
  items: SystemNotification[];
  onHandled: (id: string) => void;
}) {
  const language = useLanguage((s) => s.language);
  const [open, setOpen] = useState(false);
  const openChapter = useOpenChapter();
  // 章号按大小排，**不按通知落库的先后**：作者找的是「第几章」，不是「哪条先报的」。
  const chapters = [...new Set(items.map((i) => i.chapter_number))]
    .filter((n): n is number => n !== null)
    .sort((a, b) => a - b);

  return (
    <div className="notice-card">
      <div className="notice-head">
        <span className="lab">{KIND_TITLE[items[0].kind]?.[language] ?? items[0].kind}</span>
        <span className="row dim">
          {language === "zh"
            ? `${items.length} 条`
            : `${items.length} item${items.length === 1 ? "" : "s"}`}
        </span>
      </div>
      {chapters.length > 0 && (
        // 章号摆出来而不是只报一个总数：「12 条」不告诉作者该去看哪儿，
        // 「第 4、7、9… 章」他扫一眼就知道是不是同一段书。
        <div className="row dim">
          {language === "zh" ? (
            <>
              第 {chapters.slice(0, 12).join("、")} 章
              {chapters.length > 12 && ` 等 ${chapters.length} 章`}
            </>
          ) : (
            <>
              Chapter{chapters.length > 1 ? "s" : ""} {chapters.slice(0, 12).join(", ")}
              {chapters.length > 12 && ` among ${chapters.length} chapters total`}
            </>
          )}
        </div>
      )}
      <div className="actions">
        <button className="link" onClick={() => setOpen(!open)}>
          {language === "zh"
            ? open ? "收起" : "展开"
            : open ? "Collapse" : "Expand"}
        </button>
        {chapters.length > 0 && (
          <button className="link" onClick={() => openChapter(chapters[0])}>
            {language === "zh" ? `查看第 ${chapters[0]} 章 →` : `Go to chapter ${chapters[0]} →`}
          </button>
        )}
      </div>
      {open &&
        items.map((item) => (
          <NotificationRow key={item.id} item={item} onHandled={onHandled} />
        ))}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// 待确认提案（2026-08-31 从「待确认」tab 搬过来的部分）
//
// 通知列表里 kind 是 proposal_conflict/proposal_low_confidence 的行只是一句
// 摘要（`proposal_notifications.py` 现读现拼，没有对照/在场/知情/可信度/原文
// 引用那些字段）。完整卡片靠 `useAllProposals` 单独取一份项目全量的
// `ProposalRecord[]`，按 `subject_id === record.id` 跟通知行对上再画。
// ══════════════════════════════════════════════════════════════════════════

/** 这一格里每一次被拒绝的动作共用同一句话。
 *
 *  **为什么不直接渲染 `(error as Error).message`**（这儿原先就是那么写的）：
 *  `ApiError` 在后端没写 `message` 时会退回 `body.error`，而这一格上三种拒绝都只有码
 *  没有话——`stale_base_version` / `proposal_not_found` / `proposal_already_resolved`。
 *  于是错误框里摆给小说作者的是一串下划线英文。
 *
 *  这条缝在「已确认的情节」搬进本格之后从罕见路变成常态路：**改一次名单就把 canon
 *  版本推高一格**，紧接着按「确认所选」用的还是缓存里的旧数字 → 409（后台自动升
 *  CANON 跑完时同理，而那是 ADR 0020 的常态）。
 *  `readCorrectionError` 是那两个编辑器已经在用的同一份判断，不是第二份措辞源。 */
function ReviewRefusal({ error, onStale }: { error: unknown; onStale: () => void }) {
  const language = useLanguage((s) => s.language);
  const failure = readCorrectionError(error);
  return (
    <div className="err-box">
      <div>{failure.message}</div>
      {failure.kind === "stale" && (
        <button className="link" onClick={onStale}>
          {language === "zh" ? "查看最新版本" : "See the latest version"}
        </button>
      )}
    </div>
  );
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function asEventItem(raw: unknown): LowConfidenceEventItem | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  return r.source_kind === "event" ? (r as unknown as LowConfidenceEventItem) : null;
}

function asConflictItem(raw: unknown): EdgeConflictItem | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  return "current" in r && "proposed" in r ? (r as unknown as EdgeConflictItem) : null;
}

/** 一条提案里那些 id 在屏幕上叫什么。
 *
 *  **名字是后端连着提案一起给的**（`proposal.node_refs`，§10.3「闸门只出 `NodeRef`」）。
 *  认不出的 id **不会**出现在 `node_refs` 里（后端不编假名字），那时说「—」——
 *  和这一格里「没有在场的人」「没有可信程度」用的是同一个说法。 */
function nameLookup(proposal: ProposalRecord): (id: string) => string {
  const byId = new Map(proposal.node_refs.map((ref) => [ref.id, ref.name]));
  return (id) => byId.get(id) ?? "—";
}

/** 一行「当前」或「提议」的事实。**整句模板，不是拼片段**：中文的「A 与 B」/
 *  「A 在 B」和英文的 "A and B"/"A is at B" 语序不一样，同 `CanonEdgeEditor.tsx`
 *  那条纪律。`update_kind` 只有 "relationship" 特殊处理——"location"/"state" 都落
 *  进「在」那一支，这是既有行为，这一批只翻译不改判据。 */
/** 冲突卡上那一行事实。**四种 kind 各一句，别再让两种共用一句。**
 *
 *  ⚠️ **`state` / `death` 从前掉了值**（2026-09-06 修）。这个函数原来只分两支：
 *  `relationship` 带上 `value`，**其余一律渲染成「A 在 B」**。而「其余」里除了位置
 *  还有状态和死亡，它们的 `target` 是**维度**（健康 / 修为 / 政治立场），值在 `value` 里。
 *  于是一张状态冲突卡长这样：
 *
 *      当前：贾政 在 政治立场
 *      提议：贾政 在 政治立场
 *
 *  两行一模一样、零信息，而作者要按着它决定接受还是驳回——**后端两个值都发过来了**
 *  （`find_conflict` 给 `current.value`、`proposed.value` 都填了），是这一层扔的。
 *
 *  它在真书上一直存在，只是 2026-09-06 那次全书回填把状态类冲突从个别几条变成一摞，
 *  才被看见（作者原话：「这个是哪个模块来得错误」）。 */
function factLine(
  subject: string,
  updateKind: EdgeConflictItem["update_kind"],
  target: string,
  value: string | null,
  language: Language,
): string {
  if (updateKind === "location") {
    return language === "zh" ? `${subject} 在 ${target}` : `${subject} is at ${target}`;
  }
  if (updateKind === "relationship") {
    return language === "zh"
      ? `${subject} 与 ${target} ${value ?? ""}`.trimEnd()
      : `${subject} and ${target}${value ? ` — ${value}` : ""}`;
  }
  // state / death：`target` 是维度（健康 / 修为 / 政治立场），值在 `value` 里。
  // 值缺了就只说维度——**不编一个值**，那是这块屏幕最不该做的事。
  if (language === "zh") {
    return value ? `${subject} 的 ${target} 是 ${value}` : `${subject} 的 ${target}`;
  }
  return value ? `${subject}'s ${target}: ${value}` : `${subject}'s ${target}`;
}

/** 「改一改再收下」那一格。**它改的是一条还没生效的事实**（提案），所以它和
 *  `CanonEventCast` 的编辑器是同一件事的前后两步，用的也是同一份控件（`CastPicker`）。 */
function ProposalEditor({
  item,
  view,
  people,
  pending,
  onCancel,
  onSubmit,
}: {
  item: LowConfidenceEventItem;
  view: EventView;
  people: NodeRef[];
  pending: boolean;
  onCancel: () => void;
  onSubmit: (edit: ProposalEditInput) => void;
}) {
  const language = useLanguage((s) => s.language);
  const [summary, setSummary] = useState(item.summary);
  const [picked, setPicked] = useState<Record<Dimension, string[]>>({
    knowers: idsOf(view.knowers),
    participants: idsOf(view.participants),
  });

  const toggle = (dim: Dimension, id: string) =>
    setPicked((prev) => ({
      ...prev,
      [dim]: prev[dim].includes(id) ? prev[dim].filter((x) => x !== id) : [...prev[dim], id],
    }));

  const changed: Dimension[] = DIMENSIONS.filter((dim) =>
    dim === "knowers"
      ? !same(picked.knowers, idsOf(view.knowers))
      : !same(picked.participants, idsOf(view.participants)),
  );
  const blank = summary.trim() === "";
  const summaryChanged = !blank && summary.trim() !== item.summary;
  const nothing = !summaryChanged && changed.length === 0;

  const submit = () => {
    if (blank || nothing || pending) return;
    onSubmit({
      edited_summary: summaryChanged ? summary.trim() : null,
      knower_ids: changed.includes("knowers") ? picked.knowers : null,
      participant_ids: changed.includes("participants") ? picked.participants : null,
    });
  };

  return (
    <div className="cast-editor">
      <label className="row cast-summary">
        <span>{language === "zh" ? "事件概要" : "Event summary"}</span>
        <input value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      {/* 空概要后端会拒（422）。**在按下按钮之前就说**，别让作者去撞一次拒绝
          （同角色册抽屉里 1 字别名那条）。 */}
      {blank && (
        <div className="row dim">
          {language === "zh" ? (
            <>概要不能为空；如不需要此事件，请使用「驳回」。</>
          ) : (
            <>The summary cannot be empty; to discard the event, use “Reject”.</>
          )}
        </div>
      )}

      <CastPicker people={people} picked={picked} onToggle={toggle} />

      <div className="actions">
        <button disabled={blank || nothing || pending} onClick={submit}>
          {language === "zh"
            ? pending ? "保存中…" : "修改后接受"
            : pending ? "Accepting…" : "Accept with edits"}
        </button>
        <button className="link" onClick={onCancel}>
          {language === "zh" ? "取消" : "Cancel"}
        </button>
      </div>
    </div>
  );
}

function ProposalNotificationRow({
  notification,
  proposal,
  roster,
  pending,
  onReview,
}: {
  notification: SystemNotification;
  proposal: ProposalRecord;
  roster: NodeRef[];
  pending: boolean;
  onReview: (action: ProposalAction, edit?: ProposalEditInput) => void;
}) {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const nameOf = nameLookup(proposal);
  const [editing, setEditing] = useState(false);
  // Hooks 不许跟着 `proposal.kind` 有条件地调用——两支都先取了再各自决定用不用。
  // `edge_conflict` 那支拿不到 chapter_number 时用 1 占位也无所谓，反正结果没人读。
  const events = useEvents(projectId, proposal.chapter_number ?? 1, "PROVISIONAL");

  const head = (
    <div className="notice-head">
      <span className="lab">{KIND_TITLE[notification.kind]?.[language] ?? notification.kind}</span>
      {proposal.chapter_number !== null && (
        <span className="row dim">
          {language === "zh" ? `第 ${proposal.chapter_number} 章` : `Chapter ${proposal.chapter_number}`}
        </span>
      )}
    </div>
  );

  if (proposal.kind === "edge_conflict") {
    const items = proposal.items.map(asConflictItem).filter((x): x is EdgeConflictItem => !!x);
    return (
      <div className="notice-card statecard proposal-card conflict">
        {head}
        {items.map((item, i) => (
          <div key={i}>
            <div className="row">
              {language === "zh" ? "当前：" : "Current: "}
              {factLine(nameOf(item.current.subject_id), item.update_kind, nameOf(item.current.target_id), item.current.value, language)}
            </div>
            <div className="row">
              {language === "zh" ? "提议：" : "Proposed: "}
              {factLine(nameOf(item.proposed.subject_id), item.update_kind, nameOf(item.proposed.target_id), item.proposed.value, language)}
            </div>
            <div className="row quote">{item.proposed.quote}</div>
          </div>
        ))}
        <div className="actions">
          <button onClick={() => onReview("accept")}>{language === "zh" ? "接受" : "Accept"}</button>
          <button onClick={() => onReview("reject")}>{language === "zh" ? "驳回" : "Reject"}</button>
        </div>
      </div>
    );
  }

  const items = proposal.items.map(asEventItem).filter((x): x is LowConfidenceEventItem => !!x);
  // 后端只对「恰好 1 个 event、没有 edge」的提案开放 edit（`extract/proposal_validation.py`，
  // 前端这条判据 = `proposal_notifications.py::_editable`，两边不许各判一次不同的话）。
  const only = items.length === 1 ? items[0] : null;
  const editable = notification.actions.includes("edit") && !!only;
  // 「在场」「知道这件事的」不在 `LowConfidenceEventItem` 上（它只是抽取当时的
  // 快照：概要/可信度/引语）——跟原来「待确认」一样，靠上面已经取好的这一章
  // PROVISIONAL 事件视图查这两维，`ProposalEditor` 的初始勾选也吃同一份数据。
  const eventById = new Map((events.data ?? []).map((v) => [v.event.id, v]));

  return (
    <div className="notice-card statecard proposal-card low-confidence">
      {head}
      {items.map((item, i) => {
        const each = eventById.get(item.event_id);
        const sep = language === "zh" ? "、" : ", ";
        return (
          <div key={i}>
            <div className="row">{item.summary}</div>
            <div className="row dim">
              {language === "zh" ? "在场：" : "Present: "}
              {each ? each.participants.map((n) => n.name).join(sep) || "—" : "—"}
            </div>
            <div className="row dim">
              {language === "zh" ? "知道这件事的：" : "Knew about it: "}
              {each ? each.knowers.map((n) => n.name).join(sep) || "—" : "—"}
            </div>
            <div className="row dim">
              {language === "zh" ? "可信程度" : "Confidence"} {pct(item.confidence)}
            </div>
            <div className="row quote">{item.quote}</div>
          </div>
        );
      })}
      {editing && only && eventById.get(only.event_id) ? (
        <ProposalEditor
          item={only}
          view={eventById.get(only.event_id)!}
          people={candidates(roster, eventById.get(only.event_id)!)}
          pending={pending}
          onCancel={() => setEditing(false)}
          onSubmit={(edit) => onReview("edit", edit)}
        />
      ) : (
        <div className="actions">
          <button onClick={() => onReview("accept")}>{language === "zh" ? "接受" : "Accept"}</button>
          {editable && (
            <button onClick={() => setEditing(true)}>{language === "zh" ? "修改" : "Edit"}</button>
          )}
          <button onClick={() => onReview("reject")}>{language === "zh" ? "驳回" : "Reject"}</button>
        </div>
      )}
    </div>
  );
}


/** 「从正文发现的情节」收成**一条**（作者 2026-09-05：「这种类型的通知可以是一条
 *  形式，当有新的进来就默认和旧的那个归到一块」）。
 *
 *  ── 为什么这一档非收不可，而普通通知要攒到 4 条 ─────────────────────────
 *  这一格问的不是「这一章新读出来的」，是**「到这一章为止还没确认的全部」**
 *  （`GET /chapters/{n}/events?scope=PROVISIONAL` 走的是 `[valid_from, valid_to)`
 *  那个时态窗）。真书上第 146 章 27 条、156 章 69 条、158 章 73 条，**一路只增
 *  不减**——它没有「少的时候」，所以不设阈值，从第一条起就是一行。
 *
 *  壳子照抄 `CollapsedKind`（`notice-card` + 「逐条看/收起」），**不是第二套折叠**：
 *  收的东西不同（那边 `SystemNotification[]` 逐条忽略，这边 `EventView[]` 批量
 *  确认），但作者眼里的形状必须是同一个。
 *
 *  **批量勾选原样留着**，只是默认收起来——它是这一格唯一能一次处理掉几十条的
 *  办法，拆成一条一行就没了。 */
function ProvisionalEventsRow({ views }: { views: EventView[] }) {
  const { projectId, chapter } = useCoords();
  const language = useLanguage((s) => s.language);
  const pid = projectId;
  const projects = useProjects();
  const confirm = useConfirmProvisional(pid!, chapter);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const canonVersion = projects.data?.find((p) => p.id === pid)?.canon_version ?? 0;

  // 按章分组，章号升序（顺着读书的方向）。**章内不重排**——那是后端给的顺序，
  // 而它就是情节在正文里出现的先后。
  const groups = useMemo(() => {
    const byChapter = new Map<number, EventView[]>();
    for (const view of views) {
      const list = byChapter.get(view.event.chapter_number);
      if (list) list.push(view);
      else byChapter.set(view.event.chapter_number, [view]);
    }
    return [...byChapter.entries()].sort((a, b) => a[0] - b[0]);
  }, [views]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const confirmSelected = () => {
    if (!pid || selected.size === 0) return;
    confirm.mutate(
      {
        fact_kind: "event",
        fact_ids: [...selected],
        expected_canon_version: canonVersion,
      },
      { onSuccess: () => setSelected(new Set()) },
    );
  };

  if (views.length === 0) return null;

  return (
    // **和下面那些提案卡同一张脸**（作者 2026-09-05：「换成和下边的同一个卡片外表，
    // 然后内容里边一条一条地放着」「就是统一形象，卡片内部内容」）。
    // 三样跟着一起改了，都不是美化：
    //   ① 壳子从光板 `notice-card` 换成 `statecard proposal-card`——它和提案是**同一类
    //      事**（等作者做一个决定），长得不一样就读成了两类；
    //   ② 「逐条看」那颗折叠去掉，内容直接摊在卡里。折叠这条规矩本来就写着
    //      「提案不参与折叠：要做决定的队列藏起来等于多按一次才能开始干活」，
    //      而这一格就是那种队列；
    //   ③ 「确认后用于后续写作」和那一长串章号删掉。前者是把标题再说一遍，后者在
    //      每条自己带着章号之后是第二份同样的账。
    <div className="notice-card statecard proposal-card low-confidence">
      <div className="notice-head">
        <span className="lab">
          {language === "zh" ? "从正文发现的情节" : "Events found in the text"}
        </span>
        <span className="row dim">
          {language === "zh"
            ? `${views.length} 条`
            : `${views.length} item${views.length === 1 ? "" : "s"}`}
        </span>
      </div>
      {groups.map(([number, list]) => (
        <div className="ev-group" key={number}>
          {/* **章号是这一堆的抬头，不是每一条前面的一个前缀**（作者 2026-09-06）。
              类名和「事件」那一栏共用（`ev-group` / `ev-chapter` / `ev-count`）：
              同一件事在两块屏幕上长一个样，作者不用学两遍。
              856 条摊在一张卡里时，这个抬头是唯一能让人找到位置的东西。 */}
          <div className="ev-chapter">
            {language === "zh" ? `第 ${number} 章` : `Chapter ${number}`}
            <span className="ev-count">
              {language === "zh" ? `${list.length} 条` : `${list.length}`}
            </span>
          </div>
          {list.map((view) => (
            <label className="event-card provisional" key={view.event.id}>
              <input
                type="checkbox"
                checked={selected.has(view.event.id)}
                onChange={() => toggle(view.event.id)}
              />
              <span>
                {view.event.summary}
                <span className="dim">
                  {" "}
                  ·{" "}
                  {view.participants.map((n) => n.name).join(language === "zh" ? "、" : ", ") ||
                    "—"}
                </span>
              </span>
            </label>
          ))}
        </div>
      ))}
      <div className="actions">
        <button disabled={selected.size === 0 || confirm.isPending} onClick={confirmSelected}>
          {language === "zh"
            ? confirm.isPending ? "确认中…" : `确认所选（${selected.size}）`
            : confirm.isPending ? "Confirming…" : `Confirm selected (${selected.size})`}
        </button>
      </div>
      {confirm.error && (
        <ReviewRefusal error={confirm.error} onStale={() => projects.refetch()} />
      )}
    </div>
  );
}

/** 通知流里的一项。**顺序 = 后端给的顺序**（`created_at` 归并），这一层不发明
 *  第二个序；一档的位置 = 它第一条出现的位置（折叠那条既有规矩，原样保留）。 */
type StreamEntry =
  | { type: "group"; kind: string; items: SystemNotification[] }
  | { type: "proposal"; item: SystemNotification };

function buildStream(items: SystemNotification[]): StreamEntry[] {
  const out: StreamEntry[] = [];
  const at = new Map<string, number>();
  for (const item of items) {
    // 提案不并档、不折叠（2026-08-31 的既有裁决）：它是要做决定的队列，
    // 藏进「逐条看」背后等于多按一次才能开始干活。
    if (item.kind === "proposal_conflict" || item.kind === "proposal_low_confidence") {
      out.push({ type: "proposal", item });
      continue;
    }
    const seen = at.get(item.kind);
    if (seen === undefined) {
      at.set(item.kind, out.length);
      out.push({ type: "group", kind: item.kind, items: [item] });
    } else {
      (out[seen] as { items: SystemNotification[] }).items.push(item);
    }
  }
  return out;
}

/** 一条流：普通通知（按档折叠）和待确认提案**混在同一个列表里**，不再各画一块。
 *
 *  提案那一档在通知里只是一句摘要（`proposal_notifications.py` 现读现拼），完整
 *  卡片靠 `useAllProposals` 单独取一份项目全量的 `ProposalRecord[]`，按
 *  `subject_id === record.id` 对上再画。配不上（两条缓存还没对齐的那一拍）就先
 *  不画那一条，不猜一份假的出来。 */
function NotificationStream({
  items,
  onHandled,
}: {
  items: SystemNotification[];
  onHandled: () => void;
}) {
  const { projectId } = useCoords();
  const proposals = useAllProposals(projectId);
  const roster = useRoster(projectId);
  const projects = useProjects();
  const review = useReviewProposal(projectId!);
  const roll = useMemo(() => (roster.data ?? []) as NodeRef[], [roster.data]);
  const canonVersion = projects.data?.find((p) => p.id === projectId)?.canon_version ?? 0;
  const byId = useMemo(() => {
    const m = new Map<string, ProposalRecord>();
    for (const p of proposals.data ?? []) m.set(p.id, p);
    return m;
  }, [proposals.data]);

  const stream = useMemo(() => buildStream(items), [items]);

  return (
    <>
      {stream.map((entry) => {
        if (entry.type === "proposal") {
          const proposal = byId.get(entry.item.subject_id);
          if (!proposal) return null;
          return (
            <ProposalNotificationRow
              key={entry.item.id}
              notification={entry.item}
              proposal={proposal}
              roster={roll}
              pending={review.isPending}
              onReview={(action, edit) => {
                review.mutate(
                  { proposalId: proposal.id, action, expected_canon_version: canonVersion, edit },
                  { onSuccess: onHandled },
                );
              }}
            />
          );
        }
        return entry.items.length >= COLLAPSE_AT ? (
          <CollapsedKind key={entry.kind} items={entry.items} onHandled={onHandled} />
        ) : (
          entry.items.map((item) => (
            <NotificationRow key={item.id} item={item} onHandled={onHandled} />
          ))
        );
      })}
      {review.error && <ReviewRefusal error={review.error} onStale={() => projects.refetch()} />}
    </>
  );
}

/** 右栏「通知」：一条流。
 *
 *  **2026-09-05：三块并成一块**（作者原话「上边两种也纳入到系统通知这边」）。
 *  在此之前这一格是三段并排——从正文发现的情节、待确认提案、系统通知——而只有
 *  最后一段带空态判断，于是上面堆着 3 张卡时底下照样写着「现在没有需要你注意的」。
 *  那句话对它自己是真的，对作者是假的：**空态的判据必须和标题的范围一样宽。** */
export function SystemNotifications() {
  const language = useLanguage((s) => s.language);
  const { projectId, chapter } = useCoords();
  const notifications = useNotifications(projectId);
  const provisional = useEvents(projectId, chapter, "PROVISIONAL");
  const items = notifications.data ?? [];
  const views = provisional.data ?? [];

  const onHandled = () => {
    // 本地立刻从当前打开的通知里拿掉（后端已落 IGNORED/RESOLVED，或提案已经
    // 不在 pending() 里了）。
    notifications.refetch();
  };

  const loading = notifications.isLoading || provisional.isLoading;
  // 整格都空才说空——**不是「system_notification 表空」**，那是 2026-09-05 之前
  // 那句假空话的来源。
  const empty = items.length === 0 && views.length === 0;

  return (
    <div>
      <div className="mnr">
        <div className="lab">{language === "zh" ? "系统通知" : "Notifications"}</div>
        {loading && (
          <span className="empty">{language === "zh" ? "读取中…" : "Loading…"}</span>
        )}
        {!loading && empty && (
          <span className="empty">
            {language === "zh"
              ? "没有待处理的通知"
              : "No notifications to handle"}
          </span>
        )}
        <ProvisionalEventsRow views={views} />
        <NotificationStream items={items} onHandled={onHandled} />
      </div>
    </div>
  );
}
