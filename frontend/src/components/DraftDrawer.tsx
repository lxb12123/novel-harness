import { useState } from "react";
import { ApiError } from "../api/client";
import { useDraft } from "../api/hooks";
import { useCoords } from "../store";
import { DRAFT_LENGTH_STORAGE_KEY } from "./DraftLengthControls";
import type { DraftLengthSpec } from "../api/types";

const ZH_DEFAULT: DraftLengthSpec = {
  language: "zh",
  min_units: 2000,
  target_units: 2500,
  max_units: 3000,
};

function loadLength(): DraftLengthSpec {
  try {
    const raw = JSON.parse(
      window.localStorage.getItem(DRAFT_LENGTH_STORAGE_KEY) ?? "null"
    ) as Partial<DraftLengthSpec> | null;
    if (raw && raw.language === "zh" && typeof raw.min_units === "number") {
      return raw as DraftLengthSpec;
    }
  } catch {
    // 坏数据用默认档
  }
  return ZH_DEFAULT;
}

export function DraftDrawer({ onClose }: { onClose: () => void }) {
  const { projectId, chapter, cast } = useCoords();
  const draft = useDraft(projectId ?? "", chapter);
  const [goal, setGoal] = useState("");
  const [castText, setCastText] = useState(cast);
  const [style, setStyle] = useState("");
  const err = draft.error instanceof ApiError ? draft.error : null;
  const result = draft.data;

  function run() {
    draft.mutate({
      goal,
      cast: castText
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
      length: loadLength(),
      form: "X1",
      ...(style.trim() ? { house_style: style.trim() } : {}),
    });
  }

  const card = (
    <div className="setup-card" style={{ width: 640 }}>
      <h3>AI 起草 · 第 {chapter} 章</h3>

      <div className="field">
        <span>这一场要写什么</span>
        <textarea
          rows={3}
          value={goal}
          placeholder="描述这一场的目标、冲突和转折"
          onChange={(e) => setGoal(e.target.value)}
          style={{ width: "100%", fontFamily: "inherit" }}
        />
      </div>
      <div className="field">
        <span>在场角色（逗号分隔）</span>
        <input
          value={castText}
          onChange={(e) => setCastText(e.target.value)}
          placeholder="输入在场角色，用逗号分隔"
        />
        <div className="note">长度在「AI 起草长度」中设置。</div>
      </div>
      <div className="field">
        <span>文风（可选）</span>
        <textarea
          rows={2}
          value={style}
          placeholder="留空使用默认文风。如：文白夹杂，多用短句，对白简洁。"
          onChange={(e) => setStyle(e.target.value)}
          style={{ width: "100%", fontFamily: "inherit" }}
        />
      </div>

      {err && <div className="err-box">{err.message}</div>}
      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={onClose}>关闭</button>
        <button
          disabled={!goal.trim() || castText.trim() === "" || draft.isPending}
          onClick={run}
        >
          {draft.isPending ? "起草中…" : "起草"}
        </button>
      </div>

      {result && (
        <div className="receipt" style={{ marginTop: 12 }}>
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", lineHeight: 1.7 }}>
            {result.text}
          </pre>
          <div className="note">
            {result.length.actual_units} {result.length.language === "zh" ? "字" : "words"}
          </div>
        </div>
      )}
    </div>
  );

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="drawer">{card}</div>
    </>
  );
}
