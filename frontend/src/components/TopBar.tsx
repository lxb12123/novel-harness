import { useState } from "react";
import { useChapters } from "../api/hooks";
import { useOpenChapter } from "../autopilot";
import { useCoords } from "../store";
import { SettingsDrawer } from "./SettingsDrawer";

// 顶栏：页面切换 + AI 设置 + AS OF 章号。
// **章号是查询参数「看第几章的面板」，不是声明**——它不写进任何数据（约束 10）。
//
// 书名和「＋ 新书 / 导入」不在这儿：它们搬进左栏书架了（一个库可以有多本书，
// 那是一份**列表**，顶栏塞不下，也不该和「看第几章」抢同一行）。
//
// **「出场人物」也不在这儿了。** 它曾经是顶栏第一等公民，等于对作者说「写之前先填这个」——
// 而在场人物是**写出来的结果**，不是写之前的输入：没名字的配角进不了花名册，
// 新人物是写到那儿才需要的。现在引擎在写完之后自己去正文里数（`mentioned.py`），
// 右栏显示数出来的结果。作者要覆盖就点场景块，那是他在正文里亲手标的。
export function TopBar() {
  const { projectId, chapter, page, chatOpen, setPage, toggleChat } = useCoords();
  const chapters = useChapters(projectId);
  // 换章走 `useOpenChapter`，不是裸 setChapter：离开一章 = 那一章写完了，
  // 要把它交给后台整理（`autopilot.ts`）。作者看不到这件事，也不该看到。
  const openChapter = useOpenChapter();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const chapterList = chapters.data ?? [];

  return (
    <header>
      <span className="title">
        <b>Novel Harness</b> 工作台
      </span>

      <span style={{ width: 8 }} />
      <button className={page === "workbench" ? "on" : ""} onClick={() => setPage("workbench")}>
        工作台
      </button>
      <button className={page === "prep" ? "on" : ""} onClick={() => setPage("prep")}>
        章节准备
      </button>
      {/* 活动记录只换中栏（左栏书架、右栏面板不动）。**它是入口不是通知**：系统自己整理
          这本书的每一步都记在那儿，作者想看的时候去看——不弹、不红点、不推给他（约束 8）。 */}
      <button className={page === "log" ? "on" : ""} onClick={() => setPage("log")}>
        活动记录
      </button>

      {/* 写作助手（模式二）开合。**默认关着**，而且它开的是中栏的右半边——
          左栏书架和右栏面板不动（`App.tsx` 那段注释写着理由）。
          同「活动记录」那条：这是**入口不是通知**，不弹、不红点、不替作者打开。 */}
      <button className={chatOpen ? "on" : ""} onClick={toggleChat}>
        写作助手
      </button>

      {/* 「AI 起草」那个抽屉 2026-08-10 删了：它是**填表式**的（先填「这一场要写什么」+
          在场角色，再点按钮出一整章），而在场人物是写出来的结果不是写之前的输入（ADR 0018），
          「接下来写什么」也不该由一个表单承载。起草这件事归模式二的 agent 面板——
          在那儿它是一次工具调用，不是一个界面。**一个功能不留两个入口。**
          后端 `/draft` 一个字没动：它现在是 agent 的起草工具。 */}
      <button title="AI 设置" onClick={() => setSettingsOpen(true)}>
        ⚙
      </button>

      <span className="spacer" />
      <label htmlFor="chapter-picker">章节</label>
      <select
        id="chapter-picker"
        aria-label="当前章节"
        // 收成「…」之后，这是作者唯一能读到完整章标的地方（同左栏书名行）。
        title={chapterList.find((item) => item.number === chapter)?.title || undefined}
        value={chapterList.some((item) => item.number === chapter) ? String(chapter) : ""}
        disabled={chapterList.length === 0}
        onChange={(e) => openChapter(Number(e.target.value))}
      >
        {chapterList.length === 0 && <option value="">暂无章节</option>}
        {chapterList.map((item) => (
          <option key={item.number} value={item.number}>
            {item.title || `第 ${item.number} 章`}
          </option>
        ))}
      </select>

      {settingsOpen && <SettingsDrawer onClose={() => setSettingsOpen(false)} />}
    </header>
  );
}
