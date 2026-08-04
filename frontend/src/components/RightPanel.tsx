import { useConstraints, useMatrix, useStates, useCheck, useProposals, useRoster } from "../api/hooks";
import { useCoords, type Tab } from "../store";
import { MatrixView } from "./KnowledgeMatrix";
import { LocalGraph } from "./LocalGraph";
import { EvidenceTab } from "./EvidenceTab";
import { StateCards } from "./StateCards";
import { ProposalReviewTab } from "./ProposalReviewTab";
import type { CheckResult } from "../api/types";
import { useState } from "react";

const TABS: { key: Tab; label: string }[] = [
  { key: "matrix", label: "人物认知" },
  { key: "state", label: "人物状态" },
  { key: "graph", label: "人物关系" },
  { key: "evidence", label: "原文依据" },
  { key: "constraints", label: "写作提醒" },
  { key: "check", label: "检查" },
  { key: "review", label: "待确认" },
];

function ConstraintsView() {
  const { projectId, chapter, cast } = useCoords();
  const { data } = useConstraints(projectId, chapter, cast);
  if (!data) return <div className="empty">—</div>;
  return (
    <div>
      <div className="mnr">
        <div className="lab">暂时不能说破</div>
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
        <div className="lab">本章尚未登场</div>
        {data.forbidden_entities.length ? (
          data.forbidden_entities.map((e) => (
            <span className="tag" key={e.node.id}>
              {e.node.name}（第 {e.first_appears_chapter} 章登场）
            </span>
          ))
        ) : (
          <span className="empty">（无）</span>
        )}
      </div>
      {data.unresolved_cast.length > 0 && (
        <div className="warn">
          这些称呼未在花名册中找到：{data.unresolved_cast.join("、")}。请检查名称或补充称呼。
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
        {check.isPending ? "检查中…" : "检查本章"}
      </button>
      {check.error && <div className="err-box">{(check.error as Error).message}</div>}
      {result && (
        <div style={{ marginTop: 10 }}>
          <div className="row" style={{ color: "var(--dim)", fontSize: 12 }}>
            已检查 {result.scene_count} 个场景，
            {result.issues.length ? `发现 ${result.issues.length} 处需要留意` : "没有发现需要处理的问题"}。
          </div>
          {result.scene_count === 0 && (
            <div className="warn">这一章还没有场景信息，暂时无法进行内容检查。</div>
          )}
          {result.issues.map((iss, i) => (
            <div
              className="statecard clickable"
              key={i}
              title="点击 → 跳到正文那一段并高亮"
              onClick={() => setHighlight(iss.anchor)}
            >
              <div className="nm">需要留意</div>
              <div className="row">
                第 {iss.chapter} 章 · 第 {iss.anchor.para_index} 段 · 点击回到原文
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
  const roster = useRoster(projectId);
  const pendingCount = (proposals.data ?? []).filter((p) => p.status === "PENDING").length;
  const tabs = TABS.map((t) =>
    t.key === "review" ? { ...t, label: `待确认 ${pendingCount}` } : t,
  );

  if (!roster.data?.length) {
    return (
      <section className="pane">
        <div className="empty workbench-empty">
          添加人物或设定后，这里会显示他们在当前章节知道什么、身处何处，以及需要留意的内容。
          从左侧花名册的“＋”开始即可。
        </div>
      </section>
    );
  }

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
