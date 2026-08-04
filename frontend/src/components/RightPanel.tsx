import { useConstraints, useMatrix, useStates, useCheck, useProposals } from "../api/hooks";
import { useCoords, type Tab } from "../store";
import { MatrixView } from "./KnowledgeMatrix";
import { LocalGraph } from "./LocalGraph";
import { EvidenceTab } from "./EvidenceTab";
import { StateCards } from "./StateCards";
import { ProposalReviewTab } from "./ProposalReviewTab";
import type { CheckResult } from "../api/types";
import { useState } from "react";

const TABS: { key: Tab; label: string }[] = [
  { key: "matrix", label: "认知矩阵" },
  { key: "state", label: "当前状态" },
  { key: "graph", label: "局部图" },
  { key: "evidence", label: "证据" },
  { key: "constraints", label: "约束" },
  { key: "check", label: "一致性(R4)" },
  { key: "review", label: "待确认" },
];

function ConstraintsView() {
  const { projectId, chapter, cast } = useCoords();
  const { data } = useConstraints(projectId, chapter, cast);
  if (!data) return <div className="empty">—</div>;
  return (
    <div>
      <div className="mnr">
        <div className="lab">must_not_reveal（在场有人还不知道的秘密）</div>
        {data.must_not_reveal.length ? (
          data.must_not_reveal.map((n) => (
            <span className="tag" key={n.id}>
              {n.name}
            </span>
          ))
        ) : (
          <span className="empty">（无）</span>
        )}
      </div>
      <div className="mnr">
        <div className="lab">forbidden_entities（未来才首现，本章不许出现）</div>
        {data.forbidden_entities.length ? (
          data.forbidden_entities.map((e) => (
            <span className="tag" key={e.node.id}>
              {e.node.name}（ch{e.first_appears_chapter} 首现）
            </span>
          ))
        ) : (
          <span className="empty">（无）</span>
        )}
      </div>
      {data.unresolved_cast.length > 0 && (
        <div className="warn">
          解析不出：{data.unresolved_cast.join("、")}。此时 must_not_reveal 是退化值（全部秘密，
          fail-closed），不是算出来的答案。
        </div>
      )}
    </div>
  );
}

function CheckView() {
  const { projectId, chapter, setHighlight } = useCoords();
  const check = useCheck(projectId!);
  const [result, setResult] = useState<CheckResult | null>(null);
  return (
    <div>
      <button
        disabled={!projectId || check.isPending}
        onClick={() => check.mutate(chapter, { onSuccess: setResult })}
      >
        {check.isPending ? "检查中…" : `对第 ${chapter} 章跑 R4`}
      </button>
      {check.error && <div className="err-box">{(check.error as Error).message}</div>}
      {result && (
        <div style={{ marginTop: 10 }}>
          {/* 静默的零和真的零分得开：印「几个场景块、跑了几条规则」，不只印 issue 数。 */}
          <div className="row" style={{ color: "var(--dim)", fontSize: 12 }}>
            {result.scene_count} 个场景块 · 跑了 {result.rules_run.length} 条规则（
            {result.rules_run.join(", ")}）· {result.issues.length} 条 issue
          </div>
          {result.scene_count === 0 && (
            <div className="warn">没有场景块 = R4 无事可做 = 必然零 issue，这不是「这章没问题」。</div>
          )}
          {result.issues.map((iss, i) => (
            <div
              className="statecard clickable"
              key={i}
              title="点击 → 跳到正文那一段并高亮"
              onClick={() => setHighlight(iss.anchor)}
            >
              <div className="nm">
                [{iss.rule}] {iss.issue_type}
              </div>
              <div className="row">
                第 {iss.chapter} 章 · 第 {iss.anchor.para_index} 段 → 点我回正文
              </div>
              <div className="row">{iss.message}</div>
              {iss.suggested_action && <div className="row">建议：{iss.suggested_action}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function RightPanel() {
  const { projectId, chapter, cast, activeTab, setTab } = useCoords();
  const matrix = useMatrix(projectId, chapter, cast);
  const constraints = useConstraints(projectId, chapter, cast);
  const states = useStates(projectId, chapter, cast);
  const proposals = useProposals(projectId, chapter);
  const pendingCount = (proposals.data ?? []).filter((p) => p.status === "PENDING").length;
  const tabs = TABS.map((t) =>
    t.key === "review" ? { ...t, label: `待确认 · ${pendingCount}` } : t,
  );

  return (
    <section className="pane">
      <div className="tabs">
        {tabs.map((t) => (
          <button key={t.key} className={activeTab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {activeTab === "matrix" && <MatrixView matrix={matrix.data} constraints={constraints.data} />}
      {activeTab === "state" && <StateCards states={states.data} />}
      {activeTab === "graph" && <LocalGraph />}
      {activeTab === "evidence" && <EvidenceTab />}
      {activeTab === "constraints" && <ConstraintsView />}
      {activeTab === "check" && <CheckView />}
      {activeTab === "review" && <ProposalReviewTab />}
    </section>
  );
}
