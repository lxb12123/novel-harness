import { useMutation } from "@tanstack/react-query";
import { api, proj } from "./api/client";
import { useCoords } from "./store";

// 打开第 `next` 章。作者切章的所有入口都该走这里（顶栏下拉、左栏书架）。
//
// **2026-08-17（Task 16）：它不再发后台整理。** 「那一章写完了」的信号现在是
// **保存**（`PUT …/text` 触发固定刷新，`api/app.py::_trigger_refresh`），不是换章。
// 换章发一次 autopilot 是「双重 autopilot」的旧设计——作者切走而已（也许根本没
// 保存）就凭空付费总结/抽取。删掉这半边之后，付费动作只由保存/Ctrl-S 触发。
//
// **2026-08-18（总结自治 §3）：它只上报位置，不发任何付费工作。** 换章时打一个
// 免费心跳「我现在在第 N 章」（`POST …/focus`）。后端靠它做「当前章防抖」——
// 正在写的那一章不排总结（正写的章总结用不上、且马上可能再变）。这不是恢复旧的
// autopilot：上报不触发任何总结/抽取，付费的生成仍由存/调度决定。
export function useOpenChapter(): (next: number) => void {
  const { projectId, chapter, setChapter } = useCoords();
  const report = useReportFocus(projectId ?? "");

  return (next: number) => {
    if (next === chapter) return;
    setChapter(next);
    // 上报「我现在在哪」——免费；失败当无事发生，不影响换章。
    if (projectId) report.mutate(next);
  };
}

/** 免费心跳：告诉后端作者现在正盯着哪一章（当前章防抖 §3）。 */
export function useReportFocus(pid: string) {
  return useMutation({
    mutationFn: (chapter: number) =>
      api.post<{ chapter: number; project_id: string }>(proj(pid, "/focus"), { chapter }),
  });
}
