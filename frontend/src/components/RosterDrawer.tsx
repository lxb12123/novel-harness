import { useState } from "react";
import { ApiError } from "../api/client";
import { useCreateAlias, useCreateNode, useRoster } from "../api/hooks";
import { AUTHORED_LABELS, LABEL_ZH } from "../api/types";
import type { AliasKind, NodeLabel, NodeRef, StoredAlias } from "../api/types";

type Tab = "node" | "alias";
const ALIAS_KINDS: { kind: AliasKind; zh: string; hint: string }[] = [
  { kind: "alias", zh: "别名", hint: "另一个正经称呼" },
  { kind: "nickname", zh: "小名", hint: "熟人才这么叫" },
  { kind: "title", zh: "称号", hint: "身份、职务等称呼" },
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
          把故事中的人物、地点和设定记在这里，写作时可以快速引用。
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
      },
      { onSuccess: (n) => { setMade(n); setName(""); setAliases(""); } },
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
        <span>名称</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="输入名称" />
      </div>

      <div className="field">
        <span>其他称呼（可选，用逗号分隔）</span>
        <input value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="输入其他称呼" />
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <button disabled={!name.trim() || create.isPending} onClick={submit}>
          {create.isPending ? "建中…" : "建"}
        </button>
        <span className="hint">同名条目只会保留一份</span>
      </div>

      <Failure error={create.error} fallback="无法添加这个条目。请检查填写内容后重试。" />

      {made && (
        <div className="receipt">
          <div className="vf">✓ {LABEL_ZH[made.label]}「{made.name}」已进花名册</div>
          <div className="note">现在可以在场景和相关设置中使用这个条目。</div>
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
        <span>为哪个条目添加</span>
        <input list="roster-names" value={of} onChange={(e) => setOf(e.target.value)} placeholder="输入已有名称" />
      </div>

      <div className="field">
        <span>新称呼</span>
        <input value={surface} onChange={(e) => setSurface(e.target.value)} placeholder="输入新的称呼" />
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
          　·　名称请在建条目时填写
        </div>
      </div>

      <div className="field">
        <label className="row" style={{ cursor: "pointer" }}>
          <input type="checkbox" checked={usable} onChange={(e) => setUsable(e.target.checked)} />
          <span style={{ textTransform: "none", letterSpacing: 0 }}>
            允许用这个称呼识别正文中的角色
          </span>
        </label>
        {tooShort && (
          <div className="hint warn">
            「{surface.trim()}」只有 1 个字，可能会误认正文中的其他文字。
            如果只想保存这个称呼，请取消上方勾选。
          </div>
        )}
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <button disabled={!of.trim() || !surface.trim() || create.isPending} onClick={submit}>
          {create.isPending ? "加中…" : "加"}
        </button>
      </div>

      <Failure error={create.error} fallback="无法添加这个称呼。请检查填写内容后重试。" />

      {made && (
        <div className="receipt">
          <div className="vf">✓ 「{made.surface}」已挂到那个条目上</div>
          <div className="note">
            {made.usable_for_rules
              ? "写作时也会用这个称呼识别对应条目。"
              : "这个称呼已保存，但不会用于识别正文中的角色。"}
          </div>
        </div>
      )}
    </>
  );
}

// ── 拒绝 ────────────────────────────────────────────────────────────────────

/** **故意不查 `saidToTheAuthor`。** 这一格上至少一档拒绝（`errorShortAlias`）
 *  今天后端的 `.message` 里就带着 `usable_for_rules`/`ADR 0004` 这类写给维护者的
 *  内部术语——`saidToTheAuthor` 的过渡期兜底（认不出码就信后端的 `message`）会把
 *  它原样透出去，`test_the_frontend_keeps_no_second_glossary` 那套「不许暴露内部
 *  术语」的验收专门钉着这一格（`RosterDrawer.test.tsx`）。国际化第四批推进这一刀
 *  时在这儿撞过一次真的回归，教训是：**只有确认某个码的后端消息本身对作者安全，
 *  才把它接进 `saidToTheAuthor`**，不能因为「统一一下」就无差别接。这一格在后端
 *  把 `usable_for_rules` 这类术语从 `.message` 里清掉之前，继续用自己的 `fallback`。 */
function Failure({ error, fallback }: { error: unknown; fallback: string }) {
  const err = error instanceof ApiError ? error : null;
  if (!err) return null;

  const candidates =
    err.code === "ambiguous_name" ? (err.body.candidates as { name: string; label: NodeLabel }[]) : null;

  if (!candidates) return <div className="err-box">{fallback}</div>;
  return (
    <div className="err-box">
      找到多个匹配项，请改用名称或更明确的称呼：
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
