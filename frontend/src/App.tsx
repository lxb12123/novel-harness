import { useEffect } from "react";
import { useChapters, useProjects } from "./api/hooks";
import { useOpenChapter } from "./chapterNavigation";
import { chapterOnOpen } from "./chapterCursor";
import { useReconcileOnFocus } from "./reconcile";
import { useHashRoute } from "./route";
import { useCoords } from "./store";
import { DraftCompare } from "./components/DraftCompare";
import { TopBar } from "./components/TopBar";
import { ActivityLog } from "./components/ActivityLog";
import { CanonEdgeEditor } from "./components/CanonEdgeEditor";
import { LeftRail } from "./components/LeftRail";
import { CenterEditor } from "./components/CenterEditor";
import { RightPanel } from "./components/RightPanel";
import { SplitPanes } from "./components/SplitPanes";
import { ChatPanel } from "./components/ChatPanel";
import { BottomBar } from "./components/BottomBar";
import { Setup } from "./components/Setup";

/**
 * 地址里那一条哈希决定的**只有一件事**：这个标签页是工作台，还是「并排比几稿」那一页。
 *
 * **别把「工作台 / 活动记录」也搬进地址**：那两个是同一块屏幕的两种排布，
 * 归 `useCoords.page`；搬过去等于让浏览器的前进后退键成为它们的入口，
 * 而作者按后退键想回到的是**上一段正文**，不是上一个面板。
 * 并排比那一页不一样——它开在另一个标签页里，**没有地址就没法开**（ADR 0022）。
 */
export default function App() {
  const route = useHashRoute();
  if (route.name === "compare") return <DraftCompare chapter={route.chapter} />;
  return <Workbench />;
}

function Workbench() {
  const projects = useProjects();
  const { projectId, chapter, page, chatOpen, cursorFor, setProject, setChapter, markCursor } =
    useCoords();
  const chapters = useChapters(projectId);
  // 开书时把库和磁盘深对一次，之后每次切回这个标签页快对一次（`reconcile.ts`）。
  // **挂在这儿而不是中栏**：对的是整本书，不是正在看的那一章。
  useReconcileOnFocus(projectId);
  // 作者点开另一章 = 他离开了当前这一章 = 那一章写完了 → 交给后台整理（`autopilot.ts`）。
  // **下面那个 `chapterOnOpen` 的 setChapter 故意不走它**：开书时把光标放到该停的那一章
  // 不是「写完了一章」，走它等于凭空发一次后台整理。
  const openChapter = useOpenChapter();

  // v1 单机单库（ADR 0007）：默认打开第一个项目。换项目 = 整棵 query 树失效（store 里
  // projectId 是所有 queryKey 的第一坐标）。
  useEffect(() => {
    if (!projectId && projects.data && projects.data.length > 0) {
      setProject(projects.data[0].id);
    }
  }, [projects.data, projectId, setProject]);

  // 打开 / 换一本书之后停在第几章 —— 规则在 `chapterOnOpen`（那儿有单测），这里只负责
  // 认出「这份章目录是不是新那本书的」：query key 带 projectId，所以数据到手时它必然对得上。
  //
  // 「已经替哪本书落过位」在 store 里（`cursorFor`）而不是这儿的一个 ref：
  // **左栏点另一本书的某一章时，光标是作者亲手定的**，那一下必须能提前把标记置上——
  // 否则这个 effect 会把它当成一次换书，转手顶成那本书的最后一章。
  useEffect(() => {
    const list = chapters.data;
    if (!projectId || !list || list.length === 0) return;
    const next = chapterOnOpen({
      switched: cursorFor !== projectId,
      chapter,
      numbers: list.map((c) => c.number),
    });
    if (next !== null) setChapter(next);
    if (cursorFor !== projectId) markCursor(projectId);
  }, [projectId, chapters.data, chapter, cursorFor, setChapter, markCursor]);

  if (projects.isLoading) return <div style={{ padding: 24 }}>加载中…</div>;
  if (projects.isError)
    // **这块屏幕上原来印着一条命令**（`NH_DB=book.db uvicorn …`）。它是写给维护者的：
    // 作者那一侧的工作台和后台是同一个程序（`nh serve`），后台没了 = 那个程序退出了，
    // 而他能做的只有重新打开一次。给他一条命令等于让他去一个从没打开过的窗口。
    // （维护者跑 L1 那两个进程时看到的也是这句话——`frontend` 5173 连不上 8000 那一档，
    //  而那时他知道自己在干什么，不需要屏幕提醒。）
    return (
      <div style={{ padding: 24, color: "var(--warn)" }}>
        连不上后台。工作台和后台是同一个程序，多半是它已经退出了 —— 重新打开一次就行。
      </div>
    );
  if (!projects.data || projects.data.length === 0) return <Setup />;

  return (
    <div className="app">
      <TopBar />
      {/* 活动记录换掉的是**中栏**：左栏书架和右栏面板照旧在原地——日志里点「去改这一格」
          跳的就是右边那一栏，两栏一起消失的话作者就得先跳一次再自己找回来。

          写作助手（模式二）也只吃中栏，而且是**对半分**（作者的原话：「文章那块对半分，
          左边是文章右边是 agent」）。它切的是中栏这一块，不是整行——所以它开着的时候
          左栏书架和右栏面板也照旧在原地：作者一边跟它说话，一边看得见这一章谁还不知道什么。
          日志页那一档也留着它：换去看记录不该把说到一半的对话收走。

          **这儿原先还有一档「章节准备」，它换掉的是整块中栏**（连左右栏一起）。
          那一页 2026-08-13 删了（`TopBar.tsx` 记着为什么），于是三栏骨架现在**永远在**：
          作者不管在哪一页，都看得见左边的章目录和右边「这一章谁还不知道什么」。 */}
      <SplitPanes
        left={<LeftRail onOpenChapter={openChapter} />}
        center={page === "log" ? <ActivityLog /> : <CenterEditor />}
        chat={chatOpen ? <ChatPanel /> : undefined}
        right={<RightPanel />}
      />
      <CanonEdgeEditor />
      <BottomBar />
    </div>
  );
}
