import {
  useConstraints,
  useMatrix,
  useMentioned,
  useStates,
  useCheck,
  useProposals,
  useRoster,
} from "../api/hooks";
import { useCoords, type Tab } from "../store";
import { MatrixView } from "./KnowledgeMatrix";
import { LocalGraph } from "./LocalGraph";
import { EvidenceTab } from "./EvidenceTab";
import { StateCards } from "./StateCards";
import { ProposalReviewTab } from "./ProposalReviewTab";
import { RosterTab } from "./RosterTab";
import { SummaryTab } from "./SummaryTab";
import type { CheckResult } from "../api/types";
import { useState } from "react";

/** 算的是「其中某个人怎么样」的那几格 —— 只有它们需要知道在场是谁。
 *  花名册（全项目）、原文依据、待确认、检查（读正文和场景块）都不吃 cast。 */
const CAST_TABS = new Set<Tab>(["matrix", "state", "constraints"]);

const TABS: { key: Tab; label: string }[] = [
  { key: "roster", label: "花名册" },
  { key: "matrix", label: "人物认知" },
  { key: "state", label: "人物状态" },
  { key: "graph", label: "人物关系" },
  { key: "evidence", label: "原文依据" },
  { key: "constraints", label: "写作提醒" },
  { key: "check", label: "检查" },
  { key: "review", label: "待确认" },
  { key: "summary", label: "章节总结" },
];

/** 花名册空着的时候仍然有话可说的那几格。
 *
 *  其余每一格都是「其中某个人怎么样」，没有人就没有料。**章节总结不是**：它是这一章
 *  正文压出来的一段字，和花名册里有没有人一点关系都没有。把它一起藏进那句「先去加人」
 *  里，作者就会对着一个能用的功能读到一句不相干的话。 */
const ROSTER_FREE_TABS = new Set<Tab>(["roster", "summary"]);

function ConstraintsView() {
  const { projectId, chapter, cast, castInclude } = useCoords();
  const { data } = useConstraints(projectId, chapter, cast, castInclude);
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

/** 这几格算的是「其中某个人怎么样」，所以它们都得知道「谁」。作者不填这个「谁」——
 *  引擎从本章正文里数出来（`mentioned.py`），这一条把数出来的结果显示给作者看。
 *
 *  **措辞是「提到」不是「在场」，这是有意的。** 引擎在数字符串，没有读懂剧情：
 *  回忆里的死人、信里写到的名字都会进来。说「在场」等于向作者承诺一件系统做不到的事。
 *  （多算是安全的那一侧——`must_not_reveal` 的判据是「至少有一个人还不知道」。） */
function CastLine() {
  const { projectId, chapter, cast, setCast } = useCoords();
  const mentioned = useMentioned(projectId, chapter);

  // **这一行只说 `cast`（过滤）那一种**，而 `cast` 只有一个来源：正文里点的场景块
  // （`SceneBar` / `BottomBar`）——收窄是作者自己要的。它**存在的全部理由**就是让他
  // 看见「现在只在看几个人」并且一键退出去。
  //
  // 日志页跳过来的那个坐标**不在这里**：它走 `castInclude`（只加不减），没有把任何人
  // 从表上拿掉，所以没有「退出去」这回事。系统要是能往 `cast` 里写，这一行就得替它
  // 认罪，而正确的做法是不让它写（ADR 0018 §3：少一个人 = 少一批禁令）。
  if (cast) {
    return (
      <div className="castline">
        <span className="lab">只看：</span>
        <span className="who">{cast}</span>
        <button className="link" onClick={() => setCast("")}>
          改回整章
        </button>
      </div>
    );
  }
  if (!mentioned.data) return null;
  if (!mentioned.data.has_text) {
    return <div className="castline dim">这一章还没有正文，下面按「全书秘密都不能说破」显示。</div>;
  }
  if (!mentioned.data.surfaces.length) {
    return (
      <div className="castline dim">
        这一章的正文里没有出现花名册中的人。到「花名册」那一格补上，这里就会认出他们。
      </div>
    );
  }
  return (
    <div className="castline">
      <span className="lab">这一章提到：</span>
      <span className="who">{mentioned.data.surfaces.join("、")}</span>
    </div>
  );
}

export function RightPanel() {
  const { projectId, chapter, cast, castInclude, activeTab, setTab } = useCoords();
  // 三格吃同一份在场：`cast` 过滤（场景块），`castInclude` 只加不减（日志页跳转坐标）。
  const matrix = useMatrix(projectId, chapter, cast, castInclude);
  const constraints = useConstraints(projectId, chapter, cast, castInclude);
  const states = useStates(projectId, chapter, cast, castInclude);
  const proposals = useProposals(projectId, chapter);
  const roster = useRoster(projectId);
  const pendingCount = (proposals.data ?? []).filter((p) => p.status === "PENDING").length;
  const tabs = TABS.map((t) =>
    t.key === "review" ? { ...t, label: `待确认 ${pendingCount}` } : t,
  );

  // 花名册空 = 其余每一格都没有料可显示（它们全都是「其中某个人怎么样」）。
  // **但不能整块 return 掉**：花名册自己就是那一格，连它一起藏起来的话，
  // 「＋」也跟着没了——作者会停在一个说着「先加人」却没有加人入口的面板上。
  const bare = !roster.data?.length;

  return (
    <section className="pane">
      <div className="tabs">
        {tabs.map((t) => (
          <button key={t.key} className={activeTab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {!bare && CAST_TABS.has(activeTab) && <CastLine />}
      {activeTab === "roster" && <RosterTab />}
      {activeTab === "summary" && <SummaryTab />}
      {bare && !ROSTER_FREE_TABS.has(activeTab) && (
        <div className="empty workbench-empty">
          添加人物或设定后，这里会显示他们在当前章节知道什么、身处何处，以及需要留意的内容。
          回到「花名册」那一格，点“＋”开始。
        </div>
      )}
      {!bare && activeTab === "matrix" && (
        <MatrixView
          matrix={matrix.data}
          constraints={constraints.data}
          // 「别处刚改过」时重新取一次——**让作者再看一眼**，不是替他重试一次
          // （静默重试 = 把他的改动盖到一份他没看过的状态上）。
          onRefresh={() => matrix.refetch()}
        />
      )}
      {!bare && activeTab === "state" && <StateCards states={states.data} />}
      {!bare && activeTab === "graph" && <LocalGraph />}
      {!bare && activeTab === "evidence" && <EvidenceTab />}
      {!bare && activeTab === "constraints" && <ConstraintsView />}
      {!bare && activeTab === "check" && <CheckView />}
      {!bare && activeTab === "review" && <ProposalReviewTab />}
    </section>
  );
}
