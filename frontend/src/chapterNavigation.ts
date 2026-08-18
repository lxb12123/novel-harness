import { useCoords } from "./store";

// 打开第 `next` 章。作者切章的所有入口都该走这里（顶栏下拉、左栏书架）。
//
// **2026-08-17（Task 16）：它不再发后台整理。** 「那一章写完了」的信号现在是
// **保存**（`PUT …/text` 触发固定刷新，`api/app.py::_trigger_refresh`），不是换章。
// 换章发一次 autopilot 是「双重 autopilot」的旧设计：作者切走而已（也许根本没
// 保存）就凭空付费总结/抽取，而保存那一下又要跑一遍。删掉这半边之后，付费动作
// 只由保存/Ctrl-S 触发，后台 dispatcher（`api/background_runtime.py`）把它转成
// 验证 → 总结 ∥ 抽取。
export function useOpenChapter(): (next: number) => void {
  const { chapter, setChapter } = useCoords();

  return (next: number) => {
    if (next === chapter) return;
    setChapter(next);
  };
}
