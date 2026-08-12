import { useEffect, useRef } from "react";
import { useChapters, useProjects } from "./api/hooks";
import { useOpenChapter } from "./autopilot";
import { chapterOnOpen } from "./chapterCursor";
import { useCoords } from "./store";
import { TopBar } from "./components/TopBar";
import { ActivityLog } from "./components/ActivityLog";
import { LeftRail } from "./components/LeftRail";
import { CenterEditor } from "./components/CenterEditor";
import { RightPanel } from "./components/RightPanel";
import { SplitPanes } from "./components/SplitPanes";
import { BottomBar } from "./components/BottomBar";
import { ChapterPrepPage } from "./components/ChapterPrepPage";
import { Setup } from "./components/Setup";

export default function App() {
  const projects = useProjects();
  const { projectId, chapter, page, setProject, setChapter } = useCoords();
  const chapters = useChapters(projectId);
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
  const settledFor = useRef<string | null>(null);
  useEffect(() => {
    const list = chapters.data;
    if (!projectId || !list || list.length === 0) return;
    const next = chapterOnOpen({
      switched: settledFor.current !== projectId,
      chapter,
      numbers: list.map((c) => c.number),
    });
    if (next !== null) setChapter(next);
    settledFor.current = projectId;
  }, [projectId, chapters.data, chapter, setChapter]);

  if (projects.isLoading) return <div style={{ padding: 24 }}>加载中…</div>;
  if (projects.isError)
    return (
      <div style={{ padding: 24, color: "var(--warn)" }}>
        连不上后端。先启动 uvicorn：<code>NH_DB=book.db uvicorn novel_harness.api.app:app</code>
      </div>
    );
  if (!projects.data || projects.data.length === 0) return <Setup />;

  return (
    <div className="app">
      <TopBar />
      {page === "prep" ? (
        <ChapterPrepPage />
      ) : (
        <>
          {/* 活动记录换掉的是**中栏**：左栏书架和右栏面板照旧在原地——日志里点「去改这一格」
              跳的就是右边那一栏，两栏一起消失的话作者就得先跳一次再自己找回来。 */}
          <SplitPanes
            left={<LeftRail onOpenChapter={openChapter} />}
            center={page === "log" ? <ActivityLog /> : <CenterEditor />}
            right={<RightPanel />}
          />
          <BottomBar />
        </>
      )}
    </div>
  );
}
