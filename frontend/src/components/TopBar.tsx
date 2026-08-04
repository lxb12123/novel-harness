import { useEffect, useState } from "react";
import { useChapters, useProjects } from "../api/hooks";
import { useCoords } from "../store";
import { DraftDrawer } from "./DraftDrawer";
import { SettingsDrawer } from "./SettingsDrawer";
import { Setup } from "./Setup";

// 顶栏：项目切换 + ＋新书/导入 + AS OF 章号 + 在场 cast（称呼原文）。
// **章号是查询参数「看第几章的面板」，不是声明**——它不写进任何数据（约束 10）。
export function TopBar() {
  const projects = useProjects();
  const { projectId, chapter, cast, page, setProject, setChapter, setCast, setPage } = useCoords();
  const chapters = useChapters(projectId);
  const [setupOpen, setSetupOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const list = projects.data ?? [];
  const chapterList = chapters.data ?? [];

  useEffect(() => {
    if (chapterList.length > 0 && !chapterList.some((item) => item.number === chapter)) {
      setChapter(chapterList[0].number);
    }
  }, [chapter, chapterList, setChapter]);

  return (
    <header>
      <span className="title">
        <b>Novel Harness</b> 工作台
      </span>

      {list.length > 1 ? (
        <select
          value={projectId ?? ""}
          onChange={(e) => {
            setProject(e.target.value);
            setChapter(1);
          }}
        >
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

      <span className="hint">AI</span>
      <button title="使用 AI 辅助起草本章" onClick={() => setDraftOpen(true)}>
        AI 起草
      </button>
      <button title="AI 设置" onClick={() => setSettingsOpen(true)}>
        ⚙
      </button>

      <span className="spacer" />
      <label htmlFor="chapter-picker">章节</label>
      <select
        id="chapter-picker"
        aria-label="当前章节"
        value={chapterList.some((item) => item.number === chapter) ? String(chapter) : ""}
        disabled={chapterList.length === 0}
        onChange={(e) => setChapter(Number(e.target.value))}
      >
        {chapterList.length === 0 && <option value="">暂无章节</option>}
        {chapterList.map((item) => (
          <option key={item.number} value={item.number}>
            {item.title || `第 ${item.number} 章`}
          </option>
        ))}
      </select>
      <label htmlFor="chapter-cast">出场人物</label>
      <input
        id="chapter-cast"
        aria-label="本章出场人物"
        className="cast"
        placeholder="输入本章出场人物"
        value={cast}
        onChange={(e) => setCast(e.target.value)}
      />
      <span className="hint">用逗号或顿号分隔</span>

      {setupOpen && <Setup onClose={() => setSetupOpen(false)} />}
      {settingsOpen && <SettingsDrawer onClose={() => setSettingsOpen(false)} />}
      {draftOpen && <DraftDrawer onClose={() => setDraftOpen(false)} />}
    </header>
  );
}
