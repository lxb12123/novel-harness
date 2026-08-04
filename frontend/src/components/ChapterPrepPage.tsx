import { useEffect, useState } from "react";
import { useConstraints, useMatrix, useStates } from "../api/hooks";
import { useCoords } from "../store";
import { MatrixView } from "./KnowledgeMatrix";
import type { StateSnapshot } from "../api/types";
import { DraftLengthControls } from "./DraftLengthControls";

// 章节准备页（P2/P3）：写第 N 章之前的**确定性简报**。全部由现有读端拼出，纯聚合。
// 一页回答三件事：上一章结束时是什么局面、这一章不能说破/不能提前出现什么、现在谁知道什么。
// 「本章目标」是作者的笔记——**引擎不背书**，v1 存浏览器本地（无笔记落盘端点，诚实降级）。

function PrevStateCard({ s }: { s: StateSnapshot }) {
  const known = s.edges.filter((e) => e.type === "KNOWS" || e.type === "BELIEVES").length;
  return (
    <div className="statecard">
      <div className="nm">
        {s.node.name}
        {s.is_dead && <span className="dead"> · 已亡</span>}
      </div>
      <div className="row">所在地：{s.location ? s.location.name : "未声明"}</div>
      <div className="row">已知/误信秘密：{known} 条</div>
    </div>
  );
}

export function ChapterPrepPage() {
  const { projectId, chapter, cast, setCast, setPage } = useCoords();
  const prevCh = Math.max(1, chapter - 1);
  const prev = useStates(projectId, prevCh, cast);
  const matrix = useMatrix(projectId, chapter, cast);
  const constraints = useConstraints(projectId, chapter, cast);

  // 本章目标 brief：localStorage，按 项目+章 存。引擎不背书，纯作者笔记。
  const briefKey = `nh-brief:${projectId}:${chapter}`;
  const [brief, setBrief] = useState("");
  useEffect(() => {
    setBrief(localStorage.getItem(briefKey) ?? "");
  }, [briefKey]);

  const forbidden = constraints.data?.forbidden_entities ?? [];
  const mnr = constraints.data?.must_not_reveal ?? [];

  return (
    <div className="prep">
      <div className="prep-head">
        <h2 style={{ margin: 0 }}>第 {chapter} 章 · 写作前简报</h2>
        <label>在场</label>
        <input
          className="cast"
          placeholder="输入本章出场人物"
          value={cast}
          onChange={(e) => setCast(e.target.value)}
        />
        <span className="spacer" />
        <button onClick={() => setPage("workbench")}>← 回工作台写正文</button>
      </div>

      <div className="prep-grid">
        <DraftLengthControls />

        <section className="prep-card">
          <h3>本章目标（只保存在这台电脑上）</h3>
          <textarea
            className="brief"
            placeholder="记下这一章要发生什么，以及需要承接的情节"
            value={brief}
            onChange={(e) => {
              setBrief(e.target.value);
              localStorage.setItem(briefKey, e.target.value);
            }}
          />
        </section>

        <section className="prep-card">
          <h3>上一章（第 {prevCh} 章）结束时</h3>
          {chapter <= 1 ? (
            <div className="empty">这是第一章，没有上一章。</div>
          ) : !cast ? (
            <div className="empty">填「在场」后，这里显示他们上一章结束时的所在地与已知秘密。</div>
          ) : prev.data && prev.data.length ? (
            prev.data.map((s) => <PrevStateCard key={s.node.id} s={s} />)
          ) : (
            <div className="empty">没有找到这些人物在第 {prevCh} 章的状态。</div>
          )}
        </section>

        <section className="prep-card">
          <h3>不能提前出现（未来才首现）</h3>
          {forbidden.length ? (
            forbidden.map((e) => (
              <span className="tag" key={e.node.id}>
                {e.node.name}（第 {e.first_appears_chapter} 章登场）
              </span>
            ))
          ) : (
            <div className="empty">没有需要提醒的内容。</div>
          )}
          <h3 style={{ marginTop: 16 }}>本场不能说破</h3>
          {mnr.length ? (
            mnr.map((n) => (
              <span className="tag" key={n.id}>
                {n.name}
              </span>
            ))
          ) : (
            <div className="empty">（无）</div>
          )}
          {constraints.data?.unresolved_cast.length ? (
            <div className="warn">
              这些称呼未在花名册中找到：{constraints.data.unresolved_cast.join("、")}。
            </div>
          ) : null}
        </section>

        <section className="prep-card prep-wide">
          <h3>人物认知（第 {chapter} 章）</h3>
          <MatrixView matrix={matrix.data} constraints={constraints.data} />
        </section>
      </div>
    </div>
  );
}
