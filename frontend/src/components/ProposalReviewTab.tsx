import { useMemo, useState } from "react";
import {
  useConfirmProvisional,
  useEvents,
  useExtractionRun,
  useProposals,
  useProjects,
  useReviewProposal,
  useStartExtraction,
} from "../api/hooks";
import type {
  EdgeConflictItem,
  EventView,
  LowConfidenceEventItem,
  NewCharacterItem,
  ProposalAction,
  ProposalRecord,
} from "../api/types";
import { useCoords } from "../store";
import { readCorrectionError } from "../correctionError";
import { CanonEventCast } from "./CanonEventCast";

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

function ProposalCard({
  proposal,
  eventById,
  onReview,
}: {
  proposal: ProposalRecord;
  eventById: Map<string, EventView>;
  onReview: (action: ProposalAction) => void;
}) {
  const nameOf = nameLookup(proposal);
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
            <div className="row">可信程度 {pct(item.confidence)}</div>
          </div>
        ))}
        <div className="actions">
          <button onClick={() => onReview("accept")}>接受为角色</button>
          <button onClick={() => onReview("bystander")}>标为路人</button>
        </div>
      </div>
    );
  }

  const items = proposal.items.map(asEventItem).filter((x): x is LowConfidenceEventItem => !!x);
  return (
    <div className="statecard proposal-card low-confidence">
      <div className="nm">需要确认的情节 · 第 {proposal.chapter_number} 章</div>
      {items.map((item, i) => {
        const view = eventById.get(item.event_id);
        return (
          <div key={i}>
            <div className="row">{item.summary}</div>
            <div className="row dim">
              在场：{view ? view.participants.map((n) => n.name).join("、") : "—"}
            </div>
            <div className="row dim">可信程度 {pct(item.confidence)}</div>
            <div className="row quote">{item.quote}</div>
          </div>
        );
      })}
      <div className="actions">
        <button onClick={() => onReview("accept")}>接受</button>
        <button onClick={() => onReview("reject")}>驳回</button>
      </div>
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

  const onReview = (proposalId: string, action: ProposalAction) => {
    if (!pid) return;
    review.mutate({ proposalId, action, expected_canon_version: canonVersion });
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
        {run.data?.status === "FAILED" && (
          <div className="err-box">
            {run.data.errors.map((e, i) => (
              <div key={i}>{e.message}</div>
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
              onReview={(action) => onReview(p.id, action)}
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
