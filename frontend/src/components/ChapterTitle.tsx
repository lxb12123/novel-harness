import { useEffect, useRef, useState } from "react";
import { useChapters } from "../api/hooks";
import { useOpenChapter } from "../chapterNavigation";
import { useLanguage } from "../language";
import { useCoords } from "../store";
import { findChapters, joinTitle, splitTitle } from "../chapterTitle";

// 中栏顶栏上的章标题。在作者眼里它是一样东西，所以这儿也只放一样东西——
// 而它同时回答三个问题：**这一章叫什么**（读）、**换一章**（点开挑）、**改标题**（双击）。
//
// ── 它是从顶栏右上角搬下来的 ────────────────────────────────────────────────
//
// 原先那儿挂着「章节 [第722章 从昴日星官开始（大…]」一个 <select>。搬下来的理由：
// 「这块屏幕上正在编辑的是哪一章」是**中栏的事**，不是一条横跨三栏的顶栏的事——
// 顶栏那一行讲的是整个工作台（换页、设置），章号却只对中栏成立。
//
// **那个白框没有跟着搬下来**（作者原话：背景不要带过来）：`<select>` 自带边框和底色，
// 放在这条面板色的窄条上是一个格格不入的盒子，而这一行本来就是「第 N 章」那个标题的位置。
// 现在它平时是一行纯文字，鼠标移上去才浮出一个可点的底——**长得像标题，用起来是控件**。
export function ChapterTitle({
  line,
  onRename,
}: {
  /** 编辑器手上这一章正文的**第一行**（章标题就是它，见 `chapterTitle.ts`）。
   *
   *  `null` = 编辑器现在拿的不是这一章的字（换章途中），或者这块屏幕上**根本没有编辑器**
   *  （章节准备页）。这时**不许改标题**：改了就是把新标题写进上一章的正文里，
   *  而屏幕上什么都不会提示。 */
  line: string | null;
  /** 作者改完了：把新的第一行交回去（写进正文、标脏，由「保存」落盘）。
   *  不给 = 这块屏幕只挑章、不改名。 */
  onRename?: (title: string) => void;
}) {
  const { projectId, chapter } = useCoords();
  const language = useLanguage((s) => s.language);
  const chapters = useChapters(projectId);
  // 换章走 `useOpenChapter`（`chapterNavigation.ts`），不是裸 setChapter：它要上报
  // 「作者现在在第几章」这个免费心跳，后端靠它做当前章防抖（正在写的那章不排总结）。
  // **这条随控件一起搬下来**——换章的入口只有这一个，它掉了就没有别的地方会上报。
  // 注意它**不发付费工作**：付费的总结/抽取只由保存触发（2026-08-17 起）。
  const openChapter = useOpenChapter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  /** 正在改的那行字；`null` = 没在改。 */
  const [editing, setEditing] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const list = chapters.data ?? [];
  // 显示优先用编辑器手上那份的第一行：作者刚改完标题、还没按保存时，
  // 章目录（磁盘）里还是旧的那个——那一段时间里念旧标题就是在说一句假话。
  const shown =
    (line ?? "") ||
    list.find((c) => c.number === chapter)?.title ||
    (language === "zh" ? `第 ${chapter} 章` : `Chapter ${chapter}`);

  // 点别处关掉（同左栏那个「⋯」菜单）。不做的话它会一直挂着，而作者以为已经点掉了。
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);

  // 打开时把当前这一章滚进视野：722 章的书里，从头翻到第 722 章要滚很久。
  // （`?.` 不是防御式编程：jsdom 里根本没有 scrollIntoView，而这一行不该让测试炸。）
  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector(".on")?.scrollIntoView?.({ block: "center" });
  }, [open]);

  /** 标题行拆成「章号 + 名字」。**章号不进输入框**，作者只改得动名字那一段。 */
  const parts = line === null ? null : splitTitle(line);

  function commit(next: string) {
    setEditing(null);
    if (!parts) return;
    const line2 = joinTitle(parts, next);
    // 认不出章号那种行（`marker` 空）**清空 = 什么都不做**：那一行整个没了的话，
    // 下一行正文就顶上来当章标题了。有章号的那种可以清空——剩个「第22章」照样成立。
    if (!line2 || line2 === line) return;
    onRename?.(line2);
  }

  const hits = findChapters(list, query);

  return (
    // 改名时这一格要撑开（`.editing`）：中文章标动辄二十几个字，
    // 而一个 input 的默认宽度只放得下十来个——作者会在一个看不见开头的框里改标题。
    <div className={"chtitle" + (editing !== null ? " editing" : "")}>
      {editing !== null ? (
        <>
          {/* **章号原地不动**（作者的原话）：它留在原来那个位置上，是一段死字，
              双击改的只有它后面的名字。这不只是个界面偏好——章号是后端切章认的那一段
              （`CHAPTER_RE`），改坏它这一章存不回去。让它进不了输入框，
              这条路就**在结构上**破坏不了切章。 */}
          {parts?.marker && <span className="chtitle-fixed">{parts.marker}</span>}
          <input
            className="chtitle-edit"
            aria-label={language === "zh" ? "改这一章的名字" : "Edit this chapter's name"}
            placeholder={language === "zh" ? "这一章的名字（可以空着）" : "This chapter's name (can be left blank)"}
            value={editing}
            autoFocus
            onChange={(e) => setEditing(e.target.value)}
            onBlur={() => commit(editing)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit(editing);
              // Esc 原样退出：**改一半反悔时唯一的退路**（这一行下面就是正文，
              // 提交出去就成了一次「未保存」的改动，作者得自己想起来撤回）。
              if (e.key === "Escape") setEditing(null);
            }}
          />
        </>
      ) : (
        <button
          className="chtitle-name"
          aria-label={language === "zh" ? "当前章节" : "Current chapter"}
          aria-haspopup="listbox"
          aria-expanded={open}
          // 收成「…」之后，这是作者唯一能读到完整章标的地方（同左栏那一行书名）。
          title={
            line !== null && onRename
              ? `${shown}\n${
                  language === "zh"
                    ? "双击可以改这一章的名字（章号不动）"
                    : "Double-click to edit this chapter's name (the number stays put)"
                }`
              : shown
          }
          // 上面那个「点别处关掉」是挂在 window 的 pointerdown 上，而 pointerdown 早于
          // click：不挡住的话，单子开着时点这颗按钮会先被关掉、再被 onClick 开回来，
          // 于是它看起来**按不动**。
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            // 双击的第二下也会先走这儿（`detail === 2`）。不挡的话它会把刚点开的
            // 那张单子又合上，屏幕上闪一下——而作者要的只是改标题。
            if (e.detail > 1) return;
            setQuery("");
            setOpen((v) => !v);
          }}
          onDoubleClick={() => {
            if (!parts || !onRename) return;
            setOpen(false);
            // 进框的只有名字那一段（`第22章 女神？ 学姐？` → `女神？ 学姐？`）。
            setEditing(parts.name);
          }}
        >
          <span className="chtitle-text">{shown}</span>
          <span className="chtitle-caret" aria-hidden="true" />
        </button>
      )}

      {open && (
        <div className="chtitle-pop" onPointerDown={(e) => e.stopPropagation()}>
          {/* 722 章的书翻不动，所以挑章这件事必须能**搜**：章号和标题都算数
              （`findChapters`）。这是那个 <select> 原本给不了的东西——
              它只能靠滚，而滚 722 行等于没有这个功能。 */}
          <input
            className="chtitle-find"
            aria-label={language === "zh" ? "找章节" : "Find a chapter"}
            placeholder={language === "zh" ? "按章号或标题找…" : "Find by number or title…"}
            value={query}
            autoFocus
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
              // 只剩一条时回车直接开它：搜到唯一那一章还要再点一下是白点的。
              if (e.key === "Enter" && hits.length === 1) {
                setOpen(false);
                openChapter(hits[0].number);
              }
            }}
          />
          <div
            className="chtitle-list"
            role="listbox"
            aria-label={language === "zh" ? "章节" : "Chapters"}
            ref={listRef}
          >
            {list.length === 0 && (
              <div className="empty">
                {language === "zh" ? "这本书还没有章节。" : "This book doesn't have any chapters yet."}
              </div>
            )}
            {list.length > 0 && hits.length === 0 && (
              <div className="empty">
                {language === "zh" ? (
                  <>没有匹配「{query}」的章节。</>
                ) : (
                  <>No chapters match "{query}".</>
                )}
              </div>
            )}
            {hits.map((item) => (
              <div
                key={item.number}
                role="option"
                aria-selected={item.number === chapter}
                className={"ch" + (item.number === chapter ? " on" : "")}
                onClick={() => {
                  setOpen(false);
                  openChapter(item.number);
                }}
              >
                <span className="n">{String(item.number).padStart(3, "0")}</span>
                {item.title || (language === "zh" ? "（无题）" : "(Untitled)")}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
