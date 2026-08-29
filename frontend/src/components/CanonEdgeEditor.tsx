import { useEffect, useState } from "react";
import {
  useCanonEdge,
  useEditCanonEdge,
  useRoster,
  useRetractCanonEdge,
} from "../api/hooks";
import { edgeLabelText } from "../backendMessages";
import { readCorrectionError } from "../correctionError";
import { useLanguage, type Language } from "../language";
import { useCoords } from "../store";
import type { CanonEdgeEditRequest } from "../api/types";

// 自动升上去的地点 / 状态 / 关系边的纠错（Task 8 / ADR 0032）。
//
// 打开方式只有一条路：日志页（或人物当前事实）带着 `edge_id` 跳过来（store 的
// `focusEdgeId`）。**前端不从那行字里认哪条边**——坐标由后端给（`jump.edge_id`）。
//
// 三条纪律：
// 1. **不画章号输入框、不画自由 edge type 下拉**（约束 10 / §6.6）：后端按稳定
//    `edge_id` 校验类型，这里只按它返回的类型渲染对应的控件。
// 2. **每次修改都带 `expected_canon_version`**（从当前读到的这条边出参上取）。
//    409 = 「这条边在别处刚被改过」：保留表单、刷新当前事实，**不做静默重试**。
// 3. **「自动提取 · 作者已修改」要说得出口**：`view.source` 是起源（extractor/author），
//    `view.author_owned` 是当前归属。两个字段分开显示（不变量 21 / 27）。
const retryFailed = (language: Language): string =>
  language === "zh"
    ? "没能保存，而系统没能说清是为什么。过一会儿再试一次。"
    : "Couldn't save, and the system couldn't say why. Try again in a moment.";

/** 改归属 / 改目标那个人可以从花名册里挑。只挑后端那个 label 会收的类型。 */
function peers(roster: { id: string; label: string; name: string }[], label: string) {
  return roster.filter((n) => n.label === label);
}

/** id → 名字。「不从那行字认」的同一条规则反过来：**坐标是 id，名字是查表**。
 *  查不到退回一句人话——绝不把引擎的标识原样摆给作者（wording guard 钉着）。 */
function namesOf(roster: { id: string; name: string }[], ids: string[], language: Language): string[] {
  const byId = new Map(roster.map((n) => [n.id, n.name]));
  const fallback = language === "zh" ? "（已删掉的条目）" : "(deleted entry)";
  return ids.map((id) => byId.get(id) ?? fallback);
}

export function CanonEdgeEditor() {
  const { projectId, focusEdgeId, setEdgeFocus } = useCoords();
  const language = useLanguage((s) => s.language);
  const open = !!projectId && !!focusEdgeId;
  const edge = useCanonEdge(projectId, focusEdgeId);
  const edit = useEditCanonEdge(projectId ?? "");
  const retract = useRetractCanonEdge(projectId ?? "");
  const roster = useRoster(projectId);
  const [draft, setDraft] = useState<CanonEdgeEditRequest | null>(null);
  const [confirmRetract, setConfirmRetract] = useState(false);

  // 换了一条边（或重新取到）时重置草稿：上一回没提交的输入不能跟着跳到下一条边上。
  useEffect(() => {
    setDraft(null);
    setConfirmRetract(false);
  }, [focusEdgeId]);

  // Esc 关掉，同其它弹窗。
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setEdgeFocus(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setEdgeFocus]);

  if (!open) return null;

  const view = edge.data;
  const people = roster.data ?? [];

  const close = () => setEdgeFocus(null);
  const stale = (error: unknown) => {
    const parsed = readCorrectionError(error);
    return parsed?.kind === "stale";
  };

  const submit = () => {
    if (!projectId || !view || !draft) return;
    edit.mutate(
      { edgeId: view.edge_id, ...draft },
      {
        onSuccess: () => {
          setDraft(null);
          setConfirmRetract(false);
        },
      },
    );
  };

  const doRetract = () => {
    if (!projectId || !view) return;
    retract.mutate(
      { edgeId: view.edge_id },
      {
        onSuccess: () => {
          setConfirmRetract(false);
          setEdgeFocus(null); // 撤回了就没有可展示的当前边了——回调用方
        },
      },
    );
  };

  const failure = edit.error ? readCorrectionError(edit.error) : null;
  const retractFailure = retract.error ? readCorrectionError(retract.error) : null;

  // 来源怎么显示：一句话把「最初是谁」和「现在归谁」分开（不变量 21 / 27）。
  const origin = view
    ? view.source === "author"
      ? language === "zh" ? "作者声明" : "Author-declared"
      : language === "zh" ? "自动提取" : "Auto-extracted"
    : "";
  const ownership = view
    ? view.author_owned
      ? language === "zh" ? " · 作者已修改" : " · Edited by author"
      : ""
    : "";
  const typeName = view ? edgeLabelText(view.edge_type, language) : "";
  const dialogTitle = language === "zh" ? "改这条事实" : "Edit this fact";

  // **整句模板，不是拼片段**：三种边类型在中英文里语序都不一样
  // （「A 在 B」vs「A is at B」、「A 与 B」vs「A and B」），拼接会拼出病句。
  const factLine = (() => {
    if (!view) return "";
    const src = namesOf(people, [view.src], language).join("");
    const dst = namesOf(people, [view.dst], language).join("");
    if (view.edge_type === "LOCATED_AT") {
      return language === "zh" ? `${src} 在 ${dst}` : `${src} is at ${dst}`;
    }
    if (view.edge_type === "HAS_STATE") {
      const value = view.props.value ?? "—";
      return language === "zh" ? `${src} 的状态：${value}` : `${src}'s status: ${value}`;
    }
    // RELATED_TO
    return language === "zh" ? `${src} 与 ${dst}` : `${src} and ${dst}`;
  })();

  return (
    <div className="set-modal" role="dialog" aria-modal="true" aria-label={dialogTitle}>
      <div className="set-head">
        <div className="lab">
          {dialogTitle} · {typeName}
          {view && (
            <span className="row dim">
              {origin}
              {ownership}
              {language === "zh" ? ` · 第 ${view.valid_from_chapter} 章起` : ` · Since chapter ${view.valid_from_chapter}`}
            </span>
          )}
        </div>
        <button className="link" onClick={close} aria-label={language === "zh" ? "关闭" : "Close"}>
          ×
        </button>
      </div>

      {edge.isLoading && <div className="dim">{language === "zh" ? "读取中…" : "Loading…"}</div>}
      {edge.isError && (
        <div className="err-box">
          {language === "zh"
            ? "这条边读不出来 —— 它可能已被撤回或改掉了。"
            : "Couldn't load this fact — it may have been retracted or changed."}
        </div>
      )}

      {view && !edge.isError && (
        <>
          <div className="row dim">{factLine}</div>

          {view.edge_type === "LOCATED_AT" && (
            <LocationForm
              view={view}
              people={people}
              draft={draft}
              onDraft={setDraft}
              language={language}
            />
          )}
          {view.edge_type === "HAS_STATE" && (
            <StateForm view={view} people={people} draft={draft} onDraft={setDraft} language={language} />
          )}
          {view.edge_type === "RELATED_TO" && (
            <RelationForm view={view} people={people} draft={draft} onDraft={setDraft} language={language} />
          )}

          <div className="actions">
            <button
              disabled={!draft || edit.isPending}
              onClick={submit}
            >
              {edit.isPending
                ? language === "zh" ? "保存中…" : "Saving…"
                : language === "zh" ? "保存修改" : "Save changes"}
            </button>
            {confirmRetract ? (
              <>
                <button className="danger" disabled={retract.isPending} onClick={doRetract}>
                  {retract.isPending
                    ? language === "zh" ? "撤回中…" : "Retracting…"
                    : language === "zh" ? "确认撤回" : "Confirm retraction"}
                </button>
                <button className="link" onClick={() => setConfirmRetract(false)}>
                  {language === "zh" ? "算了" : "Cancel"}
                </button>
              </>
            ) : (
              <button className="link warn" onClick={() => setConfirmRetract(true)}>
                {language === "zh" ? "撤回这条事实" : "Retract this fact"}
              </button>
            )}
            <button className="link" onClick={close}>
              {language === "zh" ? "收起" : "Dismiss"}
            </button>
          </div>

          {(failure || retractFailure) && (
            <div className="err-box">
              <div>{failure?.message ?? retractFailure?.message ?? retryFailed(language)}</div>
              {(stale(edit.error) || stale(retract.error)) && (
                <button
                  className="link"
                  onClick={() => {
                    setDraft(null);
                    edge.refetch();
                  }}
                >
                  {language === "zh" ? "看看最新的" : "See the latest version"}
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function LocationForm({
  view,
  people,
  draft,
  onDraft,
  language,
}: {
  view: NonNullable<ReturnType<typeof useCanonEdge>["data"]>;
  people: { id: string; label: string; name: string }[];
  draft: CanonEdgeEditRequest | null;
  onDraft: (d: CanonEdgeEditRequest) => void;
  language: Language;
}) {
  const characters = peers(people, "Character");
  const locations = peers(people, "Location");
  const locationId = draft?.kind === "location" ? draft.location_id ?? null : null;
  const characterId = draft?.kind === "location" ? draft.character_id ?? null : null;
  return (
    <div className="set-field">
      <label>
        {language === "zh" ? "改归属（省略 = 保持原人物）" : "Change owner (leave blank to keep the current character)"}
        <select
          value={characterId ?? ""}
          onChange={(e) =>
            onDraft({
              kind: "location",
              character_id: e.target.value || null,
              location_id: locationId,
              expected_canon_version: view.canon_version,
            })
          }
        >
          <option value="">
            {language === "zh" ? "保持 " : "Keep "}
            {namesOf(people, [view.src], language).join("")}
          </option>
          {characters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        {language === "zh" ? "地点" : "Location"}
        <select
          value={locationId ?? view.dst}
          onChange={(e) =>
            onDraft({
              kind: "location",
              character_id: characterId,
              location_id: e.target.value,
              expected_canon_version: view.canon_version,
            })
          }
        >
          {locations.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function StateForm({
  view,
  people,
  draft,
  onDraft,
  language,
}: {
  view: NonNullable<ReturnType<typeof useCanonEdge>["data"]>;
  people: { id: string; label: string; name: string }[];
  draft: CanonEdgeEditRequest | null;
  onDraft: (d: CanonEdgeEditRequest) => void;
  language: Language;
}) {
  const characters = peers(people, "Character");
  const subjectId = draft?.kind === "state" ? draft.subject_id ?? null : null;
  const dimKey = draft?.kind === "state" ? draft.dim_key : view.props.dim_key ?? "";
  const value = draft?.kind === "state" ? draft.value : view.props.value ?? "";
  return (
    <div className="set-field">
      <label>
        {language === "zh" ? "人物" : "Character"}
        <select
          value={subjectId ?? view.src}
          onChange={(e) =>
            onDraft({
              kind: "state",
              subject_id: e.target.value,
              dim_key: dimKey,
              value,
              value_key: null,
              expected_canon_version: view.canon_version,
            })
          }
        >
          {characters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        {language === "zh" ? "维度" : "Dimension"}
        <select
          value={dimKey}
          onChange={(e) =>
            onDraft({
              kind: "state",
              subject_id: subjectId,
              dim_key: e.target.value,
              value,
              value_key: null,
              expected_canon_version: view.canon_version,
            })
          }
        >
          <option value="health">{language === "zh" ? "生死" : "Life status"}</option>
          <option value="location">{language === "zh" ? "所在" : "Location"}</option>
        </select>
      </label>
      <label>
        {language === "zh" ? "状态值" : "Value"}
        <input
          value={value}
          onChange={(e) =>
            onDraft({
              kind: "state",
              subject_id: subjectId,
              dim_key: dimKey,
              value: e.target.value,
              value_key: null,
              expected_canon_version: view.canon_version,
            })
          }
        />
      </label>
    </div>
  );
}

function RelationForm({
  view,
  people,
  draft,
  onDraft,
  language,
}: {
  view: NonNullable<ReturnType<typeof useCanonEdge>["data"]>;
  people: { id: string; label: string; name: string }[];
  draft: CanonEdgeEditRequest | null;
  onDraft: (d: CanonEdgeEditRequest) => void;
  language: Language;
}) {
  const characters = peers(people, "Character").filter((c) => c.id !== view.src);
  const peerId = draft?.kind === "relation" ? draft.peer_id ?? null : null;
  const display =
    draft?.kind === "relation"
      ? (draft.display ?? view.props.display ?? "")
      : (view.props.display ?? "");
  return (
    <div className="set-field">
      <label>
        {language === "zh" ? "关系另一端" : "Other person"}
        <select
          value={peerId ?? view.dst}
          onChange={(e) =>
            onDraft({
              kind: "relation",
              peer_id: e.target.value,
              display: display || null,
              expected_canon_version: view.canon_version,
            })
          }
        >
          {characters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        {language === "zh" ? "关系怎么称呼" : "How to describe the relationship"}
        <input
          value={display}
          onChange={(e) =>
            onDraft({
              kind: "relation",
              peer_id: peerId,
              display: e.target.value || null,
              expected_canon_version: view.canon_version,
            })
          }
        />
      </label>
    </div>
  );
}