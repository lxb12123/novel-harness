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
import type {
  EdgeConflictItem,
  EventView,
  LowConfidenceEventItem,
  NewCharacterItem,
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
  const failure = readCorrectionError(error);
  return (
    <div className="err-box">
      <div>{failure.message}</div>
      {failure.kind === "stale" && (
        <button className="link" onClick={onStale}>
          看看最新的
        </button>
      )}
    </div>
  );
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

const RUN_STATUS_ZH = {
  PENDING: "等待中",
  RUNNING: "分析中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  // 020 / Task 9：晚到的旧快照结果。它没跑错，只是不再适用——把它归在「失败」
  // 那一档最诚实（和日志页 `_RUN_STATUS` 的合并同一件事，别在这里再造一档）。
  SUPERSEDED: "失败",
} as const;

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

function asNewCharacterItem(raw: unknown): NewCharacterItem | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  return "profile" in r ? (r as unknown as NewCharacterItem) : null;
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
        <span>这件事怎么说</span>
        <input value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      {/* 空概要后端会拒（422）。**在按下按钮之前就说**，别让作者去撞一次拒绝
          （同花名册抽屉里 1 字别名那条）。 */}
      {blank && (
        <div className="row dim">
          这件事总得有句话 —— 整条不要的话用「驳回」。
        </div>
      )}

      <CastPicker people={people} picked={picked} onToggle={toggle} />

      <div className="actions">
        <button disabled={blank || nothing || pending} onClick={submit}>
          {pending ? "收下中…" : "改完收下"}
        </button>
        <button className="link" onClick={onCancel}>
          不改了
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
  const nameOf = nameLookup(proposal);
  const [editing, setEditing] = useState(false);
  if (proposal.kind === "edge_conflict") {
    const items = proposal.items.map(asConflictItem).filter((x): x is EdgeConflictItem => !!x);
    return (
      <div className="statecard proposal-card conflict">
        <div className="nm">关系冲突 · 第 {proposal.chapter_number} 章</div>
        {items.map((item, i) => (
          <div key={i}>
            <div className="row">
              当前：{nameOf(item.current.subject_id)}
              {item.update_kind === "relationship"
                ? ` 与 ${nameOf(item.current.target_id)} ${item.current.value ?? ""}`
                : ` 在 ${nameOf(item.current.target_id)}`}
            </div>
            <div className="row">
              提议：{nameOf(item.proposed.subject_id)}
              {item.update_kind === "relationship"
                ? ` 与 ${nameOf(item.proposed.target_id)} ${item.proposed.value ?? ""}`
                : ` 在 ${nameOf(item.proposed.target_id)}`}
            </div>
            <div className="row quote">{item.proposed.quote}</div>
          </div>
        ))}
        <div className="actions">
          <button onClick={() => onReview("accept")}>接受</button>
          <button onClick={() => onReview("reject")}>驳回</button>
        </div>
      </div>
    );
  }

  if (proposal.kind === "new_character") {
    const items = proposal.items
      .map(asNewCharacterItem)
      .filter((x): x is NewCharacterItem => !!x);
    return (
      <div className="statecard proposal-card new-character">
        <div className="nm">新人物 · {items[0]?.surface ?? "?"}</div>
        {items.map((item, i) => (
          <div key={i}>
            <div className="row">设定：{item.profile.gender ?? "—"} / {item.profile.personality ?? "—"}</div>
            {item.profile.background && <div className="row">背景：{item.profile.background}</div>}
            {/* 「备注」这一行在这儿曾经**根本没画**，而它接受之后会跟着这个人进写作提示
                （`draft/product_assemble.py::_PROFILE_LABELS`，那儿的标签也是「备注」）
                ——也就是说作者在这道闸门上批准了一条他从没看见的东西。 */}
            {item.profile.character_notes && (
              <div className="row">备注：{item.profile.character_notes}</div>
            )}
            <div className="row">可信程度 {pct(item.confidence)}</div>
          </div>
        ))}
        <div className="row dim">收下之后，这些设定会跟着这个人进写作提示。</div>
        <div className="actions">
          <button onClick={() => onReview("accept")}>接受为角色</button>
          <button onClick={() => onReview("bystander")}>标为路人</button>
        </div>
      </div>
    );
  }

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
      <div className="nm">需要确认的情节 · 第 {proposal.chapter_number} 章</div>
      {items.map((item, i) => {
        const each = eventById.get(item.event_id);
        return (
          <div key={i}>
            <div className="row">{item.summary}</div>
            <div className="row dim">
              在场：{each ? each.participants.map((n) => n.name).join("、") : "—"}
            </div>
            <div className="row dim">
              知道这件事的：{each ? each.knowers.map((n) => n.name).join("、") || "—" : "—"}
            </div>
            <div className="row dim">可信程度 {pct(item.confidence)}</div>
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
          <button onClick={() => onReview("accept")}>接受</button>
          {editable && <button onClick={() => setEditing(true)}>改一改</button>}
          <button onClick={() => onReview("reject")}>驳回</button>
        </div>
      )}
    </div>
  );
}

/** M4 审阅面板：待确认提案（冲突 / 低置信 / 新人物）+ 被动事件确认 + 显式抽取。 */
export function ProposalReviewTab() {
  const { projectId, chapter } = useCoords();
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

  const pending = (proposals.data ?? []).filter((p) => p.status === "PENDING");
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
          {startExtraction.isPending ? "提交中…" : "分析本章"}
        </button>
        {run.data?.status === "FAILED" && (
          <button
            disabled={!pid || startExtraction.isPending}
            onClick={() => {
              if (!pid) return;
              startExtraction.mutate({ force: true }, { onSuccess: (r) => setRunId(r.id) });
            }}
          >
            重新分析
          </button>
        )}
        {run.data && (
          <span className="dim">
            分析：{RUN_STATUS_ZH[run.data.status]}
            {run.data.valid_event_count > 0 && ` · 发现 ${run.data.valid_event_count} 条情节`}
            {run.data.proposal_count > 0 && ` · ${run.data.proposal_count} 项待确认`}
          </span>
        )}
        {/* **一句已经翻好的中文，前端一个字都不拼**（`api/extraction.py::ExtractionRunView`
            ← `activity._RUN_ERROR_LABEL`）。这儿原先渲染的是 `e.message` —— 那是写给
            维护者的英文诊断，于是屏幕上是 `chapter analysis provider failed`。
            根因在类型层：`ExtractionRun.errors` 把字段名抄成了 `kind`/`message`，
            可翻译的那个 `code` 够不着。现在那句英文不出后端的门。 */}
        {run.data?.status === "FAILED" && (
          <div className="err-box">
            {run.data.errors.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        )}
      </div>

      {pending.length > 0 && (
        <div className="mnr">
          <div className="lab">待确认内容（{pending.length}）</div>
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
        <div className="lab">从正文发现的情节（确认后用于后续写作）</div>
        {(provisionalEvents.data ?? []).length === 0 ? (
          <span className="empty">本章还没有发现需要确认的情节。</span>
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
                  <span className="unconfirmed">未确认</span> {view.event.summary}
                  <span className="dim">
                    {" "}
                    · {view.participants.map((n) => n.name).join("、") || "—"}
                  </span>
                </span>
              </label>
            ))}
            <button disabled={selected.size === 0 || confirm.isPending} onClick={confirmSelected}>
              {confirm.isPending ? "确认中…" : `确认所选（${selected.size}）`}
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
