import { useProjects } from "../api/hooks";
import { useCoords } from "../store";

// 顶栏：项目名 + AS OF 章号 + 在场 cast（称呼原文）。
// **章号是查询参数「看第几章的面板」，不是声明**——它不写进任何数据（约束 10）。
export function TopBar() {
  const projects = useProjects();
  const { projectId, chapter, cast, setChapter, setCast } = useCoords();
  const current = projects.data?.find((p) => p.id === projectId);

  return (
    <header>
      <span className="title">
        <b>Novel Harness</b> 工作台
      </span>
      <span className="hint">{current ? current.name : "（没有项目）"}</span>
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
    </header>
  );
}
