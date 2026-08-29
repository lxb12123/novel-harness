import { useMemo, useState } from "react";
import {
  useConfirmProvisional,
  useEvents,
  useExtractionRun,
  useProposals,
  useProjects,
  useReviewProposal,
  useRoster,
  useStartExtraction,
} from "../api/hooks";
import { messageForCode } from "../backendMessages";
import { useLanguage, type Language } from "../language";
import type {
  EdgeConflictItem,
  EventView,
  ExtractionRun,
  LowConfidenceEventItem,
  NodeRef,
  ProposalAction,
  ProposalEditInput,
  ProposalRecord,
} from "../api/types";
import { useCoords } from "../store";
import { readCorrectionError } from "../correctionError";
import { CanonEventCast } from "./CanonEventCast";
import { CastPicker, DIMENSIONS, candidates, idsOf, same } from "./CastPicker";
import type { Dimension } from "./CastPicker";

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
function Refusal({ error, onStale }: { error: unknown; onStale: () => void }) {
  const language = useLanguage((s) => s.language);
  const failure = readCorrectionError(error);
  return (
    <div className="err-box">
      <div>{failure.message}</div>
      {failure.kind === "stale" && (
        <button className="link" onClick={onStale}>
          {language === "zh" ? "看看最新的" : "See the latest version"}
        </button>
      )}
    </div>
  );
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

const RUN_STATUS_LABEL: Record<string, { zh: string; en: string }> = {
  PENDING: { zh: "等待中", en: "Pending" },
  RUNNING: { zh: "分析中", en: "Analyzing" },
  SUCCEEDED: { zh: "已完成", en: "Done" },
  FAILED: { zh: "失败", en: "Failed" },
  // 020 / Task 9：晚到的旧快照结果。它没跑错，只是不再适用——把它归在「失败」
  // 那一档最诚实（和日志页 `_RUN_STATUS` 的合并同一件事，别在这里再造一档）。
  SUPERSEDED: { zh: "失败", en: "Failed" },
};

/** 工具栏上那句「分析：...」。**整句拼装，不是拼片段**：两个附加小句都带着数，
 *  英文那半各自要处理单复数（1 event / 2 events，1 item / 2 items pending review）。 */
function analysisLine(run: ExtractionRun, language: Language): string {
  const status = RUN_STATUS_LABEL[run.status]?.[language] ?? run.status;
  if (language === "zh") {
    let line = `分析：${status}`;
    if (run.valid_event_count > 0) line += ` · 发现 ${run.valid_event_count} 条情节`;
    if (run.proposal_count > 0) line += ` · ${run.proposal_count} 项待确认`;
    return line;
  }
  let line = `Analysis: ${status}`;
  if (run.valid_event_count > 0) {
    line += ` · found ${run.valid_event_count} event${run.valid_event_count === 1 ? "" : "s"}`;
  }
  if (run.proposal_count > 0) {
    line += ` · ${run.proposal_count} item${run.proposal_count === 1 ? "" : "s"} pending review`;
  }
  return line;
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
 *  这儿原先是 `rosterMap.get(id) ?? id.slice(-6)`——拿 id 去花名册里查，查不到就把
 *  内部编号截断了摆上屏（`n:ID22`）。**那条兜底不是边角**：花名册（`["roster", pid]`）
 *  和队列（`["proposals", pid, chapter]`）是两条独立缓存，后台整理造出的新节点会在
 *  前者里缺席一拍——而没有任何一条路径保证那一拍不会被作者看见。
 *  出参自足之后，这一整类失败在结构上就不存在了。
 *
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
function factLine(
  subject: string,
  updateKind: EdgeConflictItem["update_kind"],
  target: string,
  value: string | null,
  language: Language,
): string {
  if (language === "zh") {
    return updateKind === "relationship"
      ? `${subject} 与 ${target} ${value ?? ""}`
      : `${subject} 在 ${target}`;
  }
  return updateKind === "relationship"
    ? `${subject} and ${target}${value ? ` — ${value}` : ""}`
    : `${subject} is at ${target}`;
}

/** 「改一改再收下」那一格。**它改的是一条还没生效的事实**（提案），所以它和
 *  `CanonEventCast` 的编辑器是同一件事的前后两步，用的也是同一份控件（`CastPicker`）。
 *
 *  ── 为什么这一格必须存在 ──────────────────────────────────────────────────
 *
 *  抽取说「这场戏里李管家也知情」，其实他不知情。在这一格出现之前，作者只有两个动作：
 *  **整条驳回**（这一章的情节记录就空了，他得自己重新声明一遍，而证据链跟着丢）或者
 *  **整条收下**（把一条假事实放进这个产品唯一在卖的那张表）。**「就把李管家去掉」
 *  说不出口**——而 `knowers` 恰好是抽取里唯一靠推断得来的一维。
 *
 *  ── 三样至少改一样，且只发动过的那一维 ────────────────────────────────────
 *
 *  和 `CanonEventCast` 同一条纪律：名单是绝对集合，`null` = 这一维不动。没动过的
 *  也发过去，`decision_log` 里就多一条「改了在场」而其实一个人都没变——而它只增不改。 */
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
        <span>{language === "zh" ? "这件事怎么说" : "How to describe this event"}</span>
        <input value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      {/* 空概要后端会拒（422）。**在按下按钮之前就说**，别让作者去撞一次拒绝
          （同花名册抽屉里 1 字别名那条）。 */}
      {blank && (
        <div className="row dim">
          {language === "zh" ? (
            <>这件事总得有句话 —— 整条不要的话用「驳回」。</>
          ) : (
            <>This event needs some description — if you don’t want it at all, use “Reject” instead.</>
          )}
        </div>
      )}

      <CastPicker people={people} picked={picked} onToggle={toggle} />

      <div className="actions">
        <button disabled={blank || nothing || pending} onClick={submit}>
          {language === "zh"
            ? pending ? "收下中…" : "改完收下"
            : pending ? "Accepting…" : "Edit and accept"}
        </button>
        <button className="link" onClick={onCancel}>
          {language === "zh" ? "不改了" : "Cancel"}
        </button>
      </div>
    </div>
  );
}

function ProposalCard({
  proposal,
  eventById,
  roster,
  pending,
  onReview,
}: {
  proposal: ProposalRecord;
  eventById: Map<string, EventView>;
  roster: NodeRef[];
  pending: boolean;
  onReview: (action: ProposalAction, edit?: ProposalEditInput) => void;
}) {
  const language = useLanguage((s) => s.language);
  const nameOf = nameLookup(proposal);
  const [editing, setEditing] = useState(false);
  if (proposal.kind === "edge_conflict") {
    const items = proposal.items.map(asConflictItem).filter((x): x is EdgeConflictItem => !!x);
    return (
      <div className="statecard proposal-card conflict">
        <div className="nm">
          {language === "zh" ? "关系冲突" : "Conflicting fact"} · {" "}
          {language === "zh" ? `第 ${proposal.chapter_number} 章` : `Chapter ${proposal.chapter_number}`}
        </div>
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

  // ⚠️ **`new_character` 那一支 2026-08-25 删了**（ADR 0020 补记）：抽取现在
  // 认不出就直接建人物，不再攒成提案问作者，所以这类提案**不会再有新的**。
  //
  // 后端的审阅链路原样留着（validation / audit / apply / `bystander` 动作）——
  // 它是**历史行**的处理路径，同 `decision_log` 那批废弃 kind 的道理。真书里
  // 那 22 条 PENDING 还在，而它们指的那些人下一次抽取会被自动建出来，提案本身
  // 成了无主的：**要不要给它们一个了结是维护者的裁定，这一批没做**（ADR 0020 补记
  // 末尾那一节）。走到这儿的历史行会落到下面那个通用形态里。

  const items = proposal.items.map(asEventItem).filter((x): x is LowConfidenceEventItem => !!x);
  // 后端只对「恰好 1 个 event、没有 edge、没有新人物」的提案开放 edit
  // （`extract/proposal_validation.py`）。**条件不成立就不画那颗按钮**——同日志页
  // 「`endpoints` 空就不画编辑入口」那条：一个点下去只会撞 422 的按钮比没有按钮更糟。
  // `view` 也是硬条件：改名单得先知道现在名单是谁，猜一份出来会让作者按下保存的那一刻
  // 悄悄删掉几个人。
  const only = items.length === 1 ? items[0] : null;
  const view = only ? eventById.get(only.event_id) : undefined;
  const editable =
    !!only && !!view && proposal.event_ids.length === 1 && proposal.edge_ids.length === 0;

  return (
    <div className="statecard proposal-card low-confidence">
      <div className="nm">
        {language === "zh" ? "需要确认的情节" : "Event to confirm"} · {" "}
        {language === "zh" ? `第 ${proposal.chapter_number} 章` : `Chapter ${proposal.chapter_number}`}
      </div>
      {items.map((item, i) => {
        const each = eventById.get(item.event_id);
        const sep = language === "zh" ? "、" : ", ";
        return (
          <div key={i}>
            <div className="row">{item.summary}</div>
            <div className="row dim">
              {language === "zh" ? "在场：" : "Present: "}
              {each ? each.participants.map((n) => n.name).join(sep) : "—"}
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
      {editing && only && view ? (
        <ProposalEditor
          item={only}
          view={view}
          people={candidates(roster, view)}
          pending={pending}
          onCancel={() => setEditing(false)}
          onSubmit={(edit) => onReview("edit", edit)}
        />
      ) : (
        <div className="actions">
          <button onClick={() => onReview("accept")}>{language === "zh" ? "接受" : "Accept"}</button>
          {editable && (
            <button onClick={() => setEditing(true)}>{language === "zh" ? "改一改" : "Edit"}</button>
          )}
          <button onClick={() => onReview("reject")}>{language === "zh" ? "驳回" : "Reject"}</button>
        </div>
      )}
    </div>
  );
}

/** M4 审阅面板：待确认提案（冲突 / 低置信 / 新人物）+ 被动事件确认 + 显式抽取。 */
export function ProposalReviewTab() {
  const { projectId, chapter } = useCoords();
  const language = useLanguage((s) => s.language);
  const pid = projectId;
  const proposals = useProposals(pid, chapter);
  const provisionalEvents = useEvents(pid, chapter, "PROVISIONAL");
  const projects = useProjects();
  // 「改一改再收下」要摆一份候选人名单。**和已确认那一格读的是同一份花名册缓存**
  // （queryKey 相同），不另开第二个读源。
  const roster = useRoster(pid);
  const review = useReviewProposal(pid!);
  const confirm = useConfirmProvisional(pid!, chapter);
  const startExtraction = useStartExtraction(pid!, chapter);
  const [runId, setRunId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const canonVersion =
    projects.data?.find((p) => p.id === pid)?.canon_version ?? 0;

  const eventById = useMemo(() => {
    const map = new Map<string, EventView>();
    for (const view of provisionalEvents.data ?? []) map.set(view.event.id, view);
    return map;
  }, [provisionalEvents.data]);

  // 花名册出参的 label 是开放字符串（后端 `_narrow` 之后的 dict）；按 label 过滤那一步
  // 在 `candidates()` 里，同 `CanonEventCast`。
  const roll = useMemo(() => (roster.data ?? []) as NodeRef[], [roster.data]);

  const allPending = (proposals.data ?? []).filter((p) => p.status === "PENDING");
  // **`new_character` 那一支整个不画**（2026-08-25，ADR 0020 补记）：抽取认不出就直接
  // 建人物，这类提案不会再有新的，浏览器里也不再给它画卡片和那两颗按钮。
  //
  // 但真书里还有 22 条 2026-08-15 攒下来的 PENDING 行。**只是滤掉它们 = 静默的零**
  // （§10 约束 8）：作者会看到一个比实际短的队列，而没有一处说过差额去哪了。
  // 所以下面单独报一句数——**不是把那一支画回来**，是承认那儿还躺着东西。
  const pending = allPending.filter((p) => p.kind !== "new_character");
  const retiredKind = allPending.length - pending.length;
  const run = useExtractionRun(pid, runId);

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

  const onReview = (proposalId: string, action: ProposalAction, edit?: ProposalEditInput) => {
    if (!pid) return;
    review.mutate({ proposalId, action, expected_canon_version: canonVersion, edit });
  };

  return (
    <div>
      <div className="review-toolbar">
        <button
          disabled={!pid || startExtraction.isPending}
          onClick={() => {
            if (!pid) return;
            startExtraction.mutate(undefined, { onSuccess: (r) => setRunId(r.id) });
          }}
        >
          {language === "zh"
            ? startExtraction.isPending ? "提交中…" : "分析本章"
            : startExtraction.isPending ? "Submitting…" : "Analyze this chapter"}
        </button>
        {run.data?.status === "FAILED" && (
          <button
            disabled={!pid || startExtraction.isPending}
            onClick={() => {
              if (!pid) return;
              startExtraction.mutate({ force: true }, { onSuccess: (r) => setRunId(r.id) });
            }}
          >
            {language === "zh" ? "重新分析" : "Re-analyze"}
          </button>
        )}
        {run.data && (
          <span className="dim">{analysisLine(run.data, language)}</span>
        )}
        {/* `errors` 是 `ExtractionErrorCode` 的原始值，不是拼好的中文（国际化第四批·
            笔二起）：`messageForCode("run_error", ...)` 查 `backendMessages.ts` 的
            `RUN_ERROR_LABEL`，日志页那条读端（`activity.py`）查的是同一张表。
            这儿原先渲染的是 `e.message` —— 那是写给维护者的英文诊断，于是屏幕上是
            `chapter analysis provider failed`。根因在类型层：`ExtractionRun.errors`
            把字段名抄成了 `kind`/`message`，可翻译的那个 `code` 够不着。现在那句英文
            不出后端的门，`code` 本身也从不直接渲染——一律先过 `messageForCode`。 */}
        {run.data?.status === "FAILED" && (
          <div className="err-box">
            {run.data.errors.map((code, i) => (
              <div key={i}>{messageForCode("run_error", language, { code }) ?? code}</div>
            ))}
          </div>
        )}
      </div>

      {retiredKind > 0 && (
        <div className="mnr">
          <span className="empty">
            {language === "zh" ? (
              <>
                还有 {retiredKind} 条旧的「新人物」待确认。系统现在会自己把认不出的人记进
                花名册，这些不用再处理了 —— 花名册里删错的那一条就行。
              </>
            ) : (
              <>
                There {retiredKind === 1 ? "is" : "are"} still {retiredKind} old “new character”
                {retiredKind === 1 ? " item" : " items"} pending review. The system now adds
                unrecognized people to the roster on its own, so these don’t need any more action
                — just delete the wrong ones from the roster if needed.
              </>
            )}
          </span>
        </div>
      )}

      {pending.length > 0 && (
        <div className="mnr">
          <div className="lab">
            {language === "zh" ? `待确认内容（${pending.length}）` : `Pending review (${pending.length})`}
          </div>
          {pending.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              eventById={eventById}
              roster={roll}
              pending={review.isPending}
              onReview={(action, edit) => onReview(p.id, action, edit)}
            />
          ))}
        </div>
      )}
      {review.error && <Refusal error={review.error} onStale={() => projects.refetch()} />}

      <div className="mnr">
        <div className="lab">
          {language === "zh"
            ? "从正文发现的情节（确认后用于后续写作）"
            : "Events found in the text (confirmed ones are used for future writing)"}
        </div>
        {(provisionalEvents.data ?? []).length === 0 ? (
          <span className="empty">
            {language === "zh"
              ? "本章还没有发现需要确认的情节。"
              : "No events found in this chapter that need confirming yet."}
          </span>
        ) : (
          <div>
            {(provisionalEvents.data ?? []).map((view) => (
              <label className="event-card provisional" key={view.event.id}>
                <input
                  type="checkbox"
                  checked={selected.has(view.event.id)}
                  onChange={() => toggle(view.event.id)}
                />
                <span>
                  <span className="unconfirmed">{language === "zh" ? "未确认" : "Unconfirmed"}</span>{" "}
                  {view.event.summary}
                  <span className="dim">
                    {" "}
                    · {view.participants.map((n) => n.name).join(language === "zh" ? "、" : ", ") || "—"}
                  </span>
                </span>
              </label>
            ))}
            <button disabled={selected.size === 0 || confirm.isPending} onClick={confirmSelected}>
              {language === "zh"
                ? confirm.isPending ? "确认中…" : `确认所选（${selected.size}）`
                : confirm.isPending ? "Confirming…" : `Confirm selected (${selected.size})`}
            </button>
            {confirm.error && (
              <Refusal error={confirm.error} onStale={() => projects.refetch()} />
            )}
          </div>
        )}
      </div>

      {/* 确认过的那些**还能再改**（ADR 0020 的「可改」）：干净的抽取结果如今直接生效，
          作者第一次看见它时它已经生效了，所以退路必须落在这儿，而不是只在队列里。 */}
      <CanonEventCast canonVersion={canonVersion} />
    </div>
  );
}
