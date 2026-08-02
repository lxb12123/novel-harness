import { useState } from "react";
import { useProjects } from "../api/hooks";
import { useCoords } from "../store";
import { DraftDrawer } from "./DraftDrawer";
import { SettingsDrawer } from "./SettingsDrawer";
import { Setup } from "./Setup";

// 顶栏：项目切换 + ＋新书/导入 + AS OF 章号 + 在场 cast（称呼原文）。
// **章号是查询参数「看第几章的面板」，不是声明**——它不写进任何数据（约束 10）。
export function TopBar() {
  const projects = useProjects();
  const { projectId, chapter, cast, page, setProject, setChapter, setCast, setPage } = useCoords();
  const [setupOpen, setSetupOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const list = projects.data ?? [];

  return (
    <header>
      <span className="title">
        <b>Novel Harness</b> 工作台
      </span>

      {list.length > 1 ? (
        <select value={projectId ?? ""} onChange={(e) => setProject(e.target.value)}>
          {list.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      ) : (
        <span className="hint">{list[0]?.name ?? "（没有项目）"}</span>
      )}
      <button onClick={() => setSetupOpen(true)}>＋新书 / 导入</button>

      <span style={{ width: 8 }} />
      <button className={page === "workbench" ? "on" : ""} onClick={() => setPage("workbench")}>
        工作台
      </button>
      <button className={page === "prep" ? "on" : ""} onClick={() => setPage("prep")}>
        章节准备
      </button>

      {/* AI 规划仍是 M2 stub（灰着）；AI 起草已于 2026-08-02 按修正案 7 开放（实验状态）。 */}
      <span className="hint">AI</span>
      <button disabled title="M2 能力尚未开放（规划）">
        AI 规划
      </button>
      <button title="AI 起草（实验状态，修正案 7）" onClick={() => setDraftOpen(true)}>
        AI 起草
      </button>
      <button title="AI 设置" onClick={() => setSettingsOpen(true)}>
        ⚙
      </button>

      <span className="spacer" />
      <label>看第</label>
      <input
        className="ch"
        type="number"
        min={1}
        value={chapter}
        onChange={(e) => setChapter(Math.max(1, Number(e.target.value) || 1))}
      />
      <label>章 · 在场</label>
      <input
        className="cast"
        placeholder="萧决,顾清音,李管家"
        value={cast}
        onChange={(e) => setCast(e.target.value)}
      />
      <span className="hint">章号是「看第几章」，系统从不让你填 valid_from</span>

      {setupOpen && <Setup onClose={() => setSetupOpen(false)} />}
      {settingsOpen && <SettingsDrawer onClose={() => setSettingsOpen(false)} />}
      {draftOpen && <DraftDrawer onClose={() => setDraftOpen(false)} />}
    </header>
  );
}
