import { useState } from "react";
import { ApiError } from "../api/client";
import { useCreateAlias, useCreateNode, useRoster } from "../api/hooks";
import { AUTHORED_LABELS, LABEL_ZH } from "../api/types";
import type { AliasKind, NodeLabel, NodeRef, StoredAlias } from "../api/types";

// 花名册的写入口 —— **补的是工作台上最硬的那个洞**。
//
// 导入 TXT 只切章、不抽实体（ADR 0004：秘密和伏笔是作者的意图，不是文本特征，
// 抽取器只能猜）。所以首次导入一本书之后花名册必然是空的，而认知矩阵的行、
// 约束的列、declare 的「填称呼」全都要先在图里有节点才算得起来——在这个抽屉之前，
// 作者到这一步只能回终端敲 `nh declare character`。
//
// 这里**没有章号输入框**，但理由和 DeclareDrawer 不同：那边是边（边才是时态的，
// 章号由引语算），这边是节点，节点根本不在时间轴上——一个人不会「从第 88 章起是人物」。

type Tab = "node" | "alias";
const ALIAS_KINDS: { kind: AliasKind; zh: string; hint: string }[] = [
  { kind: "alias", zh: "别名", hint: "另一个正经称呼" },
  { kind: "nickname", zh: "小名", hint: "熟人才这么叫" },
  { kind: "title", zh: "称号", hint: "魔尊 / 城主这类" },
];

export function RosterDrawer({ pid, onClose }: { pid: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("node");
  const roster = useRoster(pid);

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="drawer">
        <h3>花名册</h3>
        <div className="sub">
          导入只切章、不抽实体（ADR 0004）—— 谁是人、哪个是秘密，只有你知道。
          在这儿建出来之后，认知矩阵和「划一句话声明」才有得算。
        </div>

        <div className="field">
          <div className="row">
            <button className={tab === "node" ? "on" : ""} onClick={() => setTab("node")}>
              建条目
            </button>
            <button className={tab === "alias" ? "on" : ""} onClick={() => setTab("alias")}>
              加称呼
            </button>
          </div>
        </div>

        {/* 已有的名字喂给两个 tab 的 input 做原生补全：作者不用记他上次把人叫什么 */}
        <datalist id="roster-names">
          {(roster.data ?? []).map((n) => (
            <option key={n.id} value={n.name} />
          ))}
        </datalist>

        {tab === "node" ? <NodeForm pid={pid} /> : <AliasForm pid={pid} />}

        <div className="row" style={{ marginTop: 12 }}>
          <button onClick={onClose}>关闭</button>
        </div>
      </div>
    </>
  );
}

// ── 建条目 ──────────────────────────────────────────────────────────────────

function NodeForm({ pid }: { pid: string }) {
  const [label, setLabel] = useState<NodeLabel>("Character");
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const [description, setDescription] = useState("");
  const [subOf, setSubOf] = useState("");
  const [made, setMade] = useState<NodeRef | null>(null);

  const create = useCreateNode(pid);
  const hint = AUTHORED_LABELS.find((l) => l.label === label)?.hint ?? "";

  function submit() {
    setMade(null);
    create.mutate(
      {
        label,
        name: name.trim(),
        // 半角逗号 / 全角逗号 / 顿号 —— 中文作者三种都会打（同 cli 的 _CAST_SEP_RE）
        aliases: aliases.split(/[,，、]/).map((s) => s.trim()).filter(Boolean),
        ...(label === "Secret" ? { description, sub_of: subOf.trim() || null } : {}),
      },
      { onSuccess: (n) => { setMade(n); setName(""); setAliases(""); setDescription(""); setSubOf(""); } },
    );
  }

  return (
    <>
      <div className="field">
        <span>是什么</span>
        <div className="row wrap">
          {AUTHORED_LABELS.map((l) => (
            <button key={l.label} className={label === l.label ? "on" : ""} onClick={() => setLabel(l.label)}>
              {LABEL_ZH[l.label]}
            </button>
          ))}
        </div>
        <div className="hint">{hint}</div>
      </div>

      <div className="field">
        <span>本名（花名册和面板上显示的那个）</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="萧决" />
      </div>

      <div className="field">
        <span>别名（可选，逗号分隔。别名不合并实体——「顾姑娘」和「魔尊」的差别是 canon）</span>
        <input value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="萧公子、决儿" />
      </div>

      {label === "Secret" && (
        <>
          <div className="field">
            <span>秘密的内容（存进 secret 扩展表，不进节点属性）</span>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="萧决是魔尊之子"
            />
          </div>
          <div className="field">
            <span>父秘密（可选，称呼原文。拆子事实用；歧义时服务端拒绝，不替你挑）</span>
            <input
              list="roster-names"
              value={subOf}
              onChange={(e) => setSubOf(e.target.value)}
              placeholder="血脉秘密"
            />
          </div>
        </>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <button disabled={!name.trim() || create.isPending} onClick={submit}>
          {create.isPending ? "建中…" : "建"}
        </button>
        <span className="hint">同名重复提交不会建出两个（幂等，键是本名）</span>
      </div>

      <Failure error={create.error} />

      {made && (
        <div className="receipt">
          <div className="vf">✓ {LABEL_ZH[made.label]}「{made.name}」已进花名册</div>
          <div className="note">
            {made.label === "Secret"
              ? "它现在是认知矩阵的一列。下一步：在正文里划一句话，声明谁在那儿知道了它。"
              : "它现在能出现在场景的在场角色里，也能被「划一句话声明」认出来了。"}
          </div>
        </div>
      )}
    </>
  );
}

// ── 加称呼 ──────────────────────────────────────────────────────────────────

function AliasForm({ pid }: { pid: string }) {
  const [of, setOf] = useState("");
  const [surface, setSurface] = useState("");
  const [kind, setKind] = useState<AliasKind>("alias");
  const [usable, setUsable] = useState(true);
  const [made, setMade] = useState<StoredAlias | null>(null);

  const create = useCreateAlias(pid);
  // ADR 0004：1 字别名（「音」「决」）拿去正文里匹配 = 满篇误报。服务端会拒（422），
  // 但**在他按下按钮之前就说**更省事。注意拒的是 usable_for_rules 不是别名本身——
  // 1 字名的人物真书里有，存得下，只是规则不拿它开火。这里也不替他改，只提示。
  const tooShort = surface.trim().length === 1 && usable;

  function submit() {
    setMade(null);
    create.mutate(
      { of: of.trim(), surface: surface.trim(), kind, usable_for_rules: usable },
      { onSuccess: (a) => { setMade(a); setSurface(""); } },
    );
  }

  return (
    <>
      <div className="field">
        <span>给谁加（称呼原文，系统自己解析）</span>
        <input list="roster-names" value={of} onChange={(e) => setOf(e.target.value)} placeholder="萧决" />
      </div>

      <div className="field">
        <span>新称呼</span>
        <input value={surface} onChange={(e) => setSurface(e.target.value)} placeholder="魔尊" />
      </div>

      <div className="field">
        <span>哪一类</span>
        <div className="row wrap">
          {ALIAS_KINDS.map((k) => (
            <button key={k.kind} className={kind === k.kind ? "on" : ""} onClick={() => setKind(k.kind)}>
              {k.zh}
            </button>
          ))}
        </div>
        <div className="hint">
          {ALIAS_KINDS.find((k) => k.kind === kind)?.hint}
          　·　改本名不走这儿（本名是节点自己的，重建同名条目即可）
        </div>
      </div>

      <div className="field">
        <label className="row" style={{ cursor: "pointer" }}>
          <input type="checkbox" checked={usable} onChange={(e) => setUsable(e.target.checked)} />
          <span style={{ textTransform: "none", letterSpacing: 0 }}>
            规则可以拿它去正文里匹配
          </span>
        </label>
        {tooShort && (
          <div className="hint warn">
            「{surface.trim()}」只有 1 个字 —— 拿它去正文里匹配会满篇误报（ADR 0004），服务端会拒。
            要留着这个称呼就把上面那个勾去掉：**存得下，只是规则不拿它开火**。
          </div>
        )}
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <button disabled={!of.trim() || !surface.trim() || create.isPending} onClick={submit}>
          {create.isPending ? "加中…" : "加"}
        </button>
      </div>

      <Failure error={create.error} />

      {made && (
        <div className="receipt">
          <div className="vf">✓ 「{made.surface}」已挂到那个条目上</div>
          <div className="note">
            {made.usable_for_rules
              ? "规则会拿它去正文里找人。"
              : "只存不匹配 —— 面板认得它，规则不会拿它开火。"}
          </div>
        </div>
      )}
    </>
  );
}

// ── 拒绝 ────────────────────────────────────────────────────────────────────

/** 歧义一律列候选，**绝不替作者挑**（同 DeclareDrawer / 服务端 DeclarationRefused 的纪律：
 *  挑错 = 一条挂在错人身上的边，而它在面板上长得完全正常）。 */
function Failure({ error }: { error: unknown }) {
  const err = error instanceof ApiError ? error : null;
  if (!err) return null;

  const candidates =
    err.code === "ambiguous_name" ? (err.body.candidates as { name: string; label: NodeLabel }[]) : null;

  if (!candidates) return <div className="err-box">{err.message}</div>;
  return (
    <div className="err-box">
      这个称呼指向多个东西，系统不替你挑。用本名或只属于一个的称呼：
      <div className="candidates">
        {candidates.map((c, i) => (
          <div className="cand" key={i}>
            {c.name}（{LABEL_ZH[c.label] ?? c.label}）
          </div>
        ))}
      </div>
    </div>
  );
}
