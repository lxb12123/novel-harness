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

// AI 起草（**实验状态**，修正案 7）：未经 kill-gate 裁决，图谱约束是否有效尚未证实。
// 放行 ≠ 验证——这个抽屉给作者用，但每个结果都带着实验标注。
export function DraftDrawer({ onClose }: { onClose: () => void }) {
  const { projectId, chapter, cast } = useCoords();
  const draft = useDraft(projectId ?? "", chapter);
  const [goal, setGoal] = useState("");
  const [castText, setCastText] = useState(cast);
  const [form, setForm] = useState("X1");
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
      form,
      ...(style.trim() ? { house_style: style.trim() } : {}),
    });
  }

  const card = (
    <div className="setup-card" style={{ width: 640 }}>
      <h3>AI 起草 · 第 {chapter} 章</h3>
      <div className="note" style={{ color: "var(--warn)" }}>
        ⚠ 实验状态：未经 kill-gate 裁决，图谱约束是否有效尚未证实（修正案 7）。
      </div>

      <div className="field">
        <span>这一场要写什么</span>
        <textarea
          rows={3}
          value={goal}
          placeholder="写苏挽回府后在藏书阁堵住萧决，追问祠堂香炉里那把新灰是怎么回事，萧决避而不答。"
          onChange={(e) => setGoal(e.target.value)}
          style={{ width: "100%", fontFamily: "inherit" }}
        />
      </div>
      <div className="field">
        <span>在场角色（逗号分隔）</span>
        <input
          value={castText}
          onChange={(e) => setCastText(e.target.value)}
          placeholder="萧决,苏挽"
        />
      </div>
      <div className="field">
        <span>提示形态</span>
        <select value={form} onChange={(e) => setForm(e.target.value)}>
          <option value="X0">X0 · 无图谱（对照）</option>
          <option value="X1">X1 · 事实清单（图谱约束）</option>
          <option value="X2">X2 · 叙事提示（图谱约束）</option>
        </select>
        <div className="note">字数在「起草长度」里设置（本机保存，默认 2,000–3,000 字）。</div>
      </div>
      <div className="field">
        <span>文风（可选）</span>
        <textarea
          rows={2}
          value={style}
          placeholder="留空 = 默认文风。例如：文白夹杂，多用短句，对白简洁。"
          onChange={(e) => setStyle(e.target.value)}
          style={{ width: "100%", fontFamily: "inherit" }}
        />
        <div className="note">
          三臂共用，不许出现「秘密 / 不知道 / 泄露 / 剧透 / 伏笔 / 设定」。
        </div>
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
          <div className="note" style={{ color: "var(--warn)" }}>
            {result.note}
          </div>
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", lineHeight: 1.7 }}>
            {result.text}
          </pre>
          <div className="note">
            {result.length.actual_units} {result.length.unit}（{result.length.status}）
            · attempts={result.attempts} · {result.model} · {result.finish_reason}
            {result.completion_tokens != null && ` · ${result.completion_tokens} tokens`}
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
