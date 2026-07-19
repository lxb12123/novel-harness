import { useState } from "react";
import { useDeclare, useLocate } from "../api/hooks";
import { ApiError } from "../api/client";
import type { Declaration, Edge, QuoteCandidate } from "../api/types";

type Kind = "knows" | "believes" | "where";

// 选区声明 —— v1 的核心闭环，**零章号**：
// 划一句原文 → 选类型 + 填称呼 → 提交 → 系统 locate 这句落在第几章 → 自动写 valid_from。
// 作者从不填章号；回执里那个数字是产物，且明说「你没输」（约束 10 在 UI 上的兑现）。
export function DeclareDrawer({
  pid,
  quote,
  onClose,
}: {
  pid: string;
  quote: string;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<Kind>("knows");
  const [who, setWho] = useState("");
  const [target, setTarget] = useState(""); // secret（knows/believes）或 loc（where）
  const [believed, setBelieved] = useState("");
  const [q, setQ] = useState(quote);

  const locate = useLocate(pid);
  const declare = useDeclare(pid);
  const [receipt, setReceipt] = useState<Declaration | null>(null);

  const err = declare.error instanceof ApiError ? declare.error : null;
  const candidates =
    err?.code === "ambiguous_name" ? (err.body.candidates as { name: string; label: string }[]) : null;

  function submit() {
    setReceipt(null);
    const body =
      kind === "where"
        ? { kind: "where" as const, body: { who, loc: target, quote: q } }
        : kind === "believes"
          ? { kind: "believes" as const, body: { who, secret: target, believed_value: believed, quote: q } }
          : { kind: "knows" as const, body: { who, secret: target, quote: q } };
    declare.mutate(body, { onSuccess: setReceipt });
  }

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="drawer">
        <h3>声明</h3>
        <div className="sub">
          章号由这句引语算出来 —— 这里**没有**章号输入框，也永远不会有（约束 10）。
        </div>

        <div className="field">
          <span>类型</span>
          <div className="row">
            {(["knows", "believes", "where"] as Kind[]).map((k) => (
              <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>
                {k === "knows" ? "知道秘密" : k === "believes" ? "错误认知" : "在某地"}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <span>谁（称呼原文，系统自己解析）</span>
          <input value={who} onChange={(e) => setWho(e.target.value)} placeholder="萧决" />
        </div>

        <div className="field">
          <span>{kind === "where" ? "地点（称呼原文）" : "秘密（称呼原文）"}</span>
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder={kind === "where" ? "北荒" : "血脉秘密"}
          />
        </div>

        {kind === "believes" && (
          <div className="field">
            <span>他以为的是（面板直接渲染这句）</span>
            <input value={believed} onChange={(e) => setBelieved(e.target.value)} placeholder="已泄露" />
          </div>
        )}

        <div className="field">
          <span>引语（从正文复制，逐字精确匹配）</span>
          <textarea className="quotebox" rows={3} value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="row" style={{ marginTop: 4 }}>
            <button disabled={!q || locate.isPending} onClick={() => locate.mutate(q)}>
              测这条引语
            </button>
            <LocateHint pending={locate.isPending} hits={locate.data} />
          </div>
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <button disabled={!who || !target || !q || declare.isPending} onClick={submit}>
            {declare.isPending ? "声明中…" : "声明"}
          </button>
          <button onClick={onClose}>关闭</button>
        </div>

        {err && !candidates && <div className="err-box">{err.message}</div>}

        {candidates && (
          <div className="err-box">
            「{who}」指向多个东西，系统不替你挑。用本名或只属于一个人的称呼：
            <div className="candidates">
              {candidates.map((c, i) => (
                <div className="cand" key={i}>
                  {c.name}（{c.label}）
                </div>
              ))}
            </div>
          </div>
        )}

        {receipt && <Receipt decl={receipt} />}
      </div>
    </>
  );
}

function LocateHint({ pending, hits }: { pending: boolean; hits?: QuoteCandidate[] }) {
  if (pending) return <span className="locate-hits">定位中…</span>;
  if (!hits) return null;
  if (hits.length === 0)
    return <span className="locate-hits hit">0 处：正文里没有这句（先 sync，或别手打）。</span>;
  if (hits.length === 1)
    return <span className="locate-hits">✓ 唯一命中：第 {hits[0].chapter_number} 章，可声明。</span>;
  return (
    <span className="locate-hits hit">
      {hits.length} 处 → declare 会拒绝并不替你挑。把引语加长到只匹配一处。
    </span>
  );
}

function edgeLine(e: Edge) {
  const to = e.valid_to_chapter ?? "∞";
  return `${e.type} [${e.valid_from_chapter}, ${to})`;
}

// 回执 —— 招牌动作：valid_from 是系统算的（明说你没输）+ 自动闭合/撤回的旧边。
function Receipt({ decl }: { decl: Declaration }) {
  return (
    <div className="receipt">
      <div className="vf">
        ✓ 已声明 · valid_from = ch{decl.edge.valid_from_chapter}
      </div>
      <div className="note">
        这句话落在第 {decl.evidence.chapter_number} 章 —— 这个数字是**系统算的**，你从没输入过它。
      </div>
      {decl.closed.length > 0 && (
        <div className="closed">
          ↳ 自动闭合旧边：{decl.closed.map(edgeLine).join("；")}（那个上界同样是算出来的）
        </div>
      )}
      {decl.retracted.length > 0 && (
        <div className="closed">↳ 已撤回（同章更正）：{decl.retracted.map(edgeLine).join("；")}</div>
      )}
      <div className="note">已记入 decision_log：{decl.decision_id}</div>
    </div>
  );
}
