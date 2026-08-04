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
  ProposalAction,
  ProposalRecord,
} from "../api/types";
import { useCoords } from "../store";

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

function asNewCharacterItem(raw: unknown): NewCharacterItem | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  return "profile" in r ? (r as unknown as NewCharacterItem) : null;
}

function ProposalCard({
  proposal,
  nameOf,
  eventById,
  onReview,
}: {
  proposal: ProposalRecord;
  nameOf: (id: string) => string;
  eventById: Map<string, EventView>;
  onReview: (action: ProposalAction) => void;
}) {
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
            <div className="row">置信度 {pct(item.confidence)}</div>
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
      <div className="nm">低置信事件 · 第 {proposal.chapter_number} 章</div>
      {items.map((item, i) => {
        const view = eventById.get(item.event_id);
        return (
          <div key={i}>
            <div className="row">{item.summary}</div>
            <div className="row dim">
              在场：{view ? view.participants.map((n) => n.name).join("、") : "—"}
            </div>
            <div className="row dim">置信度 {pct(item.confidence)}</div>
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
  const roster = useRoster(pid);
  const review = useReviewProposal(pid!);
  const confirm = useConfirmProvisional(pid!, chapter);
  const startExtraction = useStartExtraction(pid!, chapter);
  const [runId, setRunId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const canonVersion =
    projects.data?.find((p) => p.id === pid)?.canon_version ?? 0;

  const rosterMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const n of roster.data ?? []) map.set(n.id, n.name);
    return map;
  }, [roster.data]);
  const nameOf = (id: string) => rosterMap.get(id) ?? id.slice(-6);

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
          {startExtraction.isPending ? "排队中…" : `跑第 ${chapter} 章抽取`}
        </button>
        {run.data?.status === "FAILED" && (
          <button
            disabled={!pid || startExtraction.isPending}
            onClick={() => {
              if (!pid) return;
              startExtraction.mutate({ force: true }, { onSuccess: (r) => setRunId(r.id) });
            }}
          >
            重跑本章
          </button>
        )}
        {run.data && (
          <span className="dim">
            抽取：{run.data.status}
            {run.data.valid_event_count > 0 && ` · ${run.data.valid_event_count} 事件`}
            {run.data.proposal_count > 0 && ` · ${run.data.proposal_count} 提案`}
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
          <div className="lab">待确认提案（{pending.length}）</div>
          {pending.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              nameOf={nameOf}
              eventById={eventById}
              onReview={(action) => onReview(p.id, action)}
            />
          ))}
        </div>
      )}
      {review.error && <div className="err-box">{(review.error as Error).message}</div>}

      <div className="mnr">
        <div className="lab">被动事件（未确认 · 灰显，不参与规则与写作）</div>
        {(provisionalEvents.data ?? []).length === 0 ? (
          <span className="empty">本章暂无抽取出的被动事件。</span>
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
            {confirm.error && <div className="err-box">{(confirm.error as Error).message}</div>}
          </div>
        )}
      </div>
    </div>
  );
}
