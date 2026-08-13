import { useState } from "react";
import { useDeclare, useLocate } from "../api/hooks";
import { ApiError } from "../api/client";
import type { Declaration, Edge, QuoteCandidate } from "../api/types";
import { SyncButton } from "./SyncButton";

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
  // 「这句话定位不到」的两个到达点：先按「检查原文位置」（0 命中），或者直接保存
  // （后端 422 `quote_not_found`）。两条路都可能是同一个原因——**这一章还没读回来**，
  // 所以两条路都把那颗按钮摆出来。后端那句话已经说了「让系统重新读一遍稿子」，
  // 这里不再复述一遍（同一句话说两遍会教作者跳过整段）。
  const quoteMissing = err?.code === "quote_not_found" || locate.data?.length === 0;

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
        <h3>记录设定</h3>
        <div className="sub">这条记录会自动关联到原文所在的章节。</div>

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
          <span>人物</span>
          <input aria-label="人物" value={who} onChange={(e) => setWho(e.target.value)} placeholder="输入人物名称" />
        </div>

        <div className="field">
          <span>{kind === "where" ? "地点" : "秘密"}</span>
          <input
            aria-label={kind === "where" ? "地点" : "秘密"}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder={kind === "where" ? "输入地点名称" : "输入秘密名称"}
          />
        </div>

        {kind === "believes" && (
          <div className="field">
            <span>人物相信的内容</span>
            <input value={believed} onChange={(e) => setBelieved(e.target.value)} placeholder="输入人物此刻相信的内容" />
          </div>
        )}

        <div className="field">
          <span>原文依据</span>
          <textarea className="quotebox" rows={3} value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="row" style={{ marginTop: 4 }}>
            <button disabled={!q || locate.isPending} onClick={() => locate.mutate(q)}>
              检查原文位置
            </button>
            <LocateHint pending={locate.isPending} hits={locate.data} />
          </div>
          {quoteMissing && (
            <div style={{ marginTop: 6 }}>
              <SyncButton pid={pid} />
            </div>
          )}
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <button disabled={!who || !target || !q || declare.isPending} onClick={submit}>
            {declare.isPending ? "保存中…" : "保存记录"}
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
  // 这句话以前是「正文中没有找到这句话，**请重新选择**」——一句甩锅的话。
  // 他的选择通常没有问题：正文这块屏幕直接读磁盘，所以他在 WPS 里写的字他看得见；
  // 而定位搜的是**库里的快照**，那一版可能还是他改之前的。让他回去改一个他没做错的
  // 操作，是这个产品最容易说出口的那句假话。所以现在只说事实 + 那颗按钮就在下面。
  if (hits.length === 0)
    return (
      <span className="locate-hits hit">
        在系统读到的那一版正文里没找到这句话。这一章你在别的软件里改过的话，先读回来再试。
      </span>
    );
  if (hits.length === 1)
    return <span className="locate-hits">✓ 已在第 {hits[0].chapter_number} 章找到</span>;
  return (
    <span className="locate-hits hit">
      正文中有 {hits.length} 处相同内容，请多选择一些文字以便准确定位。
    </span>
  );
}

function edgeLine(e: Edge) {
  const type = e.type === "KNOWS" ? "知道" : e.type === "BELIEVES" ? "以为" : e.type === "LOCATED_AT" ? "身处" : "关联";
  const to = e.valid_to_chapter ? `至第 ${e.valid_to_chapter} 章前` : "持续有效";
  return `${type} · 第 ${e.valid_from_chapter} 章起，${to}`;
}

// 回执 —— 招牌动作：valid_from 是系统算的（明说你没输）+ 自动闭合/撤回的旧边。
function Receipt({ decl }: { decl: Declaration }) {
  return (
    <div className="receipt">
      <div className="vf">
        ✓ 已记录 · 第 {decl.edge.valid_from_chapter} 章起
      </div>
      <div className="note">
        原文依据位于第 {decl.evidence.chapter_number} 章。
      </div>
      {decl.closed.length > 0 && (
        <div className="closed">
          ↳ 已结束上一条记录：{decl.closed.map(edgeLine).join("；")}
        </div>
      )}
      {decl.retracted.length > 0 && (
        <div className="closed">↳ 已替换同章旧记录：{decl.retracted.map(edgeLine).join("；")}</div>
      )}
    </div>
  );
}
