import { useState } from "react";
import { ApiError } from "../api/client";
import { useCreateAlias, useCreateNode, useRoster } from "../api/hooks";
import { AUTHORED_LABELS } from "../api/types";
import type { NodeLabel, NodeRef, StoredAlias } from "../api/types";
import { nodeLabelText } from "../backendMessages";
import { useLanguage } from "../language";

type Tab = "node" | "alias";

// ⚠️ **这儿原来有一个「哪一类」三选（别名 / 小名 / 称号），2026-09-04 整个删了。**
//
// 作者裁定：**这三个当作相等**——「齐天大圣」既是孙悟空的别名也是他的称号，
// 「斗战胜佛」也是；这一格存在的意义只有一个，「让系统认出这个说法指的也是这个人」。
//
// **引擎本来就是这么看的**，所以这不是把 UI 改得和后端不一致，是让 UI 追上后端：
// `AliasKind` 里唯一有行为差别的是 `CANONICAL`（本名盾：`add_alias` 拒收它、
// 出参把它滤掉），`alias` / `nickname` / `title` 三者在图层、匹配、规则里**一个
// 分支都没有**；抽取写进来的别名也一律是 `alias`（`extract/aliases.py`）。
// 真正决定「能不能用来认正文」的是下面那个勾（`usable_for_rules` + ADR 0004 的
// 一字守卫），不是这个类。
//
// **枚举留在后端不动**：老库里已经存着的 `title` / `nickname` 行照旧渲染
// （chip 印的是 `surface`，跟类无关），删枚举才需要迁移，而它没有收益。

export function RosterDrawer({ pid, onClose }: { pid: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("node");
  const language = useLanguage((s) => s.language);
  const roster = useRoster(pid);

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="drawer">
        <h3>{language === "zh" ? "角色册" : "Roster"}</h3>
        <div className="sub">
          {language === "zh"
            ? "把故事中的人物、地点和设定记在这里，写作时可以快速引用。"
            : "Keep track of characters, places, and settings from your story here, so you can reference them quickly while writing."}
        </div>

        <div className="field">
          <div className="row">
            <button className={tab === "node" ? "on" : ""} onClick={() => setTab("node")}>
              {language === "zh" ? "建条目" : "Add entry"}
            </button>
            <button className={tab === "alias" ? "on" : ""} onClick={() => setTab("alias")}>
              {language === "zh" ? "加别名" : "Add alias"}
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
          <button onClick={onClose}>{language === "zh" ? "关闭" : "Close"}</button>
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

  const language = useLanguage((s) => s.language);
  const create = useCreateNode(pid);
  const hint = AUTHORED_LABELS.find((l) => l.label === label)?.hint[language] ?? "";

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
        <span>{language === "zh" ? "是什么" : "What is it"}</span>
        <div className="row wrap">
          {AUTHORED_LABELS.map((l) => (
            <button key={l.label} className={label === l.label ? "on" : ""} onClick={() => setLabel(l.label)}>
              {nodeLabelText(l.label, language)}
            </button>
          ))}
        </div>
        <div className="hint">{hint}</div>
      </div>

      <div className="field">
        <span>{language === "zh" ? "名称" : "Name"}</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={language === "zh" ? "输入名称" : "Enter a name"}
        />
      </div>

      <div className="field">
        <span>{language === "zh" ? "其他别名（可选，用逗号分隔）" : "Other aliases (optional, comma-separated)"}</span>
        <input
          value={aliases}
          onChange={(e) => setAliases(e.target.value)}
          placeholder={language === "zh" ? "输入其他别名" : "Enter other aliases"}
        />
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <button disabled={!name.trim() || create.isPending} onClick={submit}>
          {language === "zh"
            ? create.isPending ? "建中…" : "建"
            : create.isPending ? "Adding…" : "Add"}
        </button>
        <span className="hint">
          {language === "zh" ? "同名条目只会保留一份" : "Entries with the same name are kept as one"}
        </span>
      </div>

      <Failure
        error={create.error}
        fallback={
          language === "zh"
            ? "无法添加这个条目。请检查填写内容后重试。"
            : "Couldn't add this entry. Check what you entered and try again."
        }
      />

      {made && (
        <div className="receipt">
          <div className="vf">
            {language === "zh" ? (
              <>✓ {nodeLabelText(made.label, language)}「{made.name}」已加入角色册</>
            ) : (
              <>✓ {nodeLabelText(made.label, language)} "{made.name}" is now in the roster</>
            )}
          </div>
          <div className="note">
            {language === "zh"
              ? "现在可以在场景和相关设置中使用这个条目。"
              : "This entry can now be recognized in your scenes and other settings."}
          </div>
        </div>
      )}
    </>
  );
}

// ── 加别名 ──────────────────────────────────────────────────────────────────

function AliasForm({ pid }: { pid: string }) {
  const [of, setOf] = useState("");
  const [surface, setSurface] = useState("");
  const [usable, setUsable] = useState(true);
  const [made, setMade] = useState<StoredAlias | null>(null);
  const language = useLanguage((s) => s.language);

  const create = useCreateAlias(pid);
  const tooShort = surface.trim().length === 1 && usable;

  function submit() {
    setMade(null);
    create.mutate(
      // `kind` 恒为 `alias`：三类当作相等（见文件顶上那段 ⚠️），不再问作者。
      { of: of.trim(), surface: surface.trim(), kind: "alias", usable_for_rules: usable },
      { onSuccess: (a) => { setMade(a); setSurface(""); } },
    );
  }

  return (
    <>
      <div className="field">
        <span>{language === "zh" ? "为哪个条目添加" : "Add to which entry"}</span>
        <input
          list="roster-names"
          value={of}
          onChange={(e) => setOf(e.target.value)}
          placeholder={language === "zh" ? "输入已有名称" : "Enter an existing name"}
        />
      </div>

      <div className="field">
        <span>{language === "zh" ? "新别名" : "New alias"}</span>
        <input
          value={surface}
          onChange={(e) => setSurface(e.target.value)}
          placeholder={language === "zh" ? "输入新的别名" : "Enter the new alias"}
        />
        <div className="hint">
          {language === "zh"
            ? "别名、小名、称号都填这里 · 名称请在建条目时填写"
            : "Aliases, nicknames and titles all go here · Set the primary name when adding the entry"}
        </div>
      </div>

      <div className="field">
        <label className="row" style={{ cursor: "pointer" }}>
          <input type="checkbox" checked={usable} onChange={(e) => setUsable(e.target.checked)} />
          <span style={{ textTransform: "none", letterSpacing: 0 }}>
            {language === "zh"
              ? "允许用这个别名识别正文中的角色"
              : "Let this name be used to recognize this character in the text"}
          </span>
        </label>
        {tooShort && (
          <div className="hint warn">
            {language === "zh" ? (
              <>
                「{surface.trim()}」只有 1 个字，可能会误认正文中的其他文字。
                如果只想保存这个别名，请取消上方勾选。
              </>
            ) : (
              <>
                "{surface.trim()}" is only 1 character long and might get confused with other
                text. If you just want to save this name without using it for recognition,
                uncheck the box above.
              </>
            )}
          </div>
        )}
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <button disabled={!of.trim() || !surface.trim() || create.isPending} onClick={submit}>
          {language === "zh"
            ? create.isPending ? "加中…" : "加"
            : create.isPending ? "Adding…" : "Add"}
        </button>
      </div>

      <Failure
        error={create.error}
        fallback={
          language === "zh"
            ? "无法添加这个别名。请检查填写内容后重试。"
            : "Couldn't add this name. Check what you entered and try again."
        }
      />

      {made && (
        <div className="receipt">
          <div className="vf">
            {language === "zh" ? (
              <>✓ 「{made.surface}」已添加为该条目的别名</>
            ) : (
              <>✓ "{made.surface}" has been linked to that entry</>
            )}
          </div>
          <div className="note">
            {language === "zh"
              ? made.usable_for_rules
                ? "写作时也会用这个别名识别对应条目。"
                : "这个别名已保存，但不会用于识别正文中的角色。"
              : made.usable_for_rules
                ? "This name will also be used to recognize that entry while you write."
                : "This name has been saved, but won't be used to recognize the character in the text."}
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
  const language = useLanguage((s) => s.language);
  const err = error instanceof ApiError ? error : null;
  if (!err) return null;

  const candidates =
    err.code === "ambiguous_name" ? (err.body.candidates as { name: string; label: NodeLabel }[]) : null;

  if (!candidates) return <div className="err-box">{fallback}</div>;
  return (
    <div className="err-box">
      {language === "zh"
        ? "找到多个匹配项，请改用名称或更明确的别名："
        : "Found multiple matches — try the primary name or a more specific alias instead:"}
      <div className="candidates">
        {candidates.map((c, i) => (
          <div className="cand" key={i}>
            {language === "zh" ? (
              <>{c.name}（{nodeLabelText(c.label, language)}）</>
            ) : (
              <>{c.name} ({nodeLabelText(c.label, language)})</>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
