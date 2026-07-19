import { useConstraints, useMatrix, useStates, useCheck } from "../api/hooks";
import { useCoords, type Tab } from "../store";
import { MatrixView } from "./KnowledgeMatrix";
import { LocalGraph } from "./LocalGraph";
import type { CheckResult, StateSnapshot } from "../api/types";
import { useState } from "react";

const TABS: { key: Tab; label: string }[] = [
  { key: "matrix", label: "认知矩阵" },
  { key: "state", label: "当前状态" },
  { key: "graph", label: "局部图" },
  { key: "constraints", label: "约束" },
  { key: "check", label: "一致性(R4)" },
];

function StateCards({ states }: { states?: StateSnapshot[] }) {
  if (!states || states.length === 0) return <div className="empty">无在场角色。</div>;
  return (
    <div>
      {states.map((s) => (
        <div className="statecard" key={s.node.id}>
          <div className="nm">
            {s.node.name}
            {s.is_dead && <span className="dead"> · 已亡</span>}
          </div>
          <div className="row">所在地：{s.location ? s.location.name : "未声明"}</div>
          {s.states.map((v, i) => (
            <div className="row" key={i}>
              {"name" in v.dim ? v.dim.name : ""}：{v.value || ""}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

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
  const { projectId, chapter } = useCoords();
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
            <div className="statecard" key={i}>
              <div className="nm">
                [{iss.rule}] {iss.issue_type}
              </div>
              <div className="row">
                第 {iss.chapter} 章 · 第 {iss.anchor.para_index} 段
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

  return (
    <section className="pane">
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.key} className={activeTab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {activeTab === "matrix" && <MatrixView matrix={matrix.data} constraints={constraints.data} />}
      {activeTab === "state" && <StateCards states={states.data} />}
      {activeTab === "graph" && <LocalGraph />}
      {activeTab === "constraints" && <ConstraintsView />}
      {activeTab === "check" && <CheckView />}
    </section>
  );
}
