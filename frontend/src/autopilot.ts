import { useRunAutopilot } from "./api/hooks";
import { useCoords } from "./store";

// 换章 = 作者离开上一章 = 「那一章写完了」。
//
// 所以**对刚离开的那一章**发一次后台整理（生成总结 / 抽取事件），不是对新进入的那一章：
// 新进入的那一章他还没写。这是异步的、不阻塞的、**不给作者任何提示的**——他不需要知道
// 后台在干活，他只需要下次按「起草」时发现资料已经齐了。
//
// 为什么挂在「打开某一章」这个动作上，而不是 watch store 里的 chapter：
// 开书时 `chapterOnOpen` 也会 setChapter（把光标放到该停的那一章），那**不是**作者
// 写完了第一章。watch 会把它误判成一次「离开」，凭空发一次后台整理。

/** 打开第 `next` 章。作者切章的所有入口都该走这里（顶栏下拉、左栏书架）。 */
export function useOpenChapter(): (next: number) => void {
  const { projectId, chapter, setChapter } = useCoords();
  const autopilot = useRunAutopilot(projectId ?? "");

  return (next: number) => {
    if (next === chapter) return;
    // 失败不管、不重试、不提示：端点可能还没上线，而这条路径永远不许打扰作者。
    // （`mutate` 不会 reject，错误进 mutation 状态，没人读。）
    if (projectId) autopilot.mutate(chapter);
    setChapter(next);
  };
}
