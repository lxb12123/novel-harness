import { useEffect } from "react";
import { useProjects } from "./api/hooks";
import { useCoords } from "./store";
import { TopBar } from "./components/TopBar";
import { LeftRail } from "./components/LeftRail";
import { CenterEditor } from "./components/CenterEditor";
import { RightPanel } from "./components/RightPanel";
import { BottomBar } from "./components/BottomBar";
import { ChapterPrepPage } from "./components/ChapterPrepPage";
import { Setup } from "./components/Setup";

export default function App() {
  const projects = useProjects();
  const { projectId, page, setProject, setChapter } = useCoords();

  // v1 单机单库（ADR 0007）：默认打开第一个项目。换项目 = 整棵 query 树失效（store 里
  // projectId 是所有 queryKey 的第一坐标）。
  useEffect(() => {
    if (!projectId && projects.data && projects.data.length > 0) {
      setProject(projects.data[0].id);
    }
  }, [projects.data, projectId, setProject]);

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
          <main>
            <LeftRail onOpenChapter={setChapter} />
            <CenterEditor />
            <RightPanel />
          </main>
          <BottomBar />
        </>
      )}
    </div>
  );
}
