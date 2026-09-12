import { useEffect, useState } from "react";
import {
  useChapters,
  useCreateChapter,
  useProjects,
  useSetProjectLanguage,
} from "../api/hooks";
import { useLanguage } from "../language";
import { useCoords } from "../store";
import {
  newChapterError,
  nextAfterRemoving,
  shelved,
  useShelf,
} from "../bookshelf";
import { Setup } from "./Setup";
import { PlusIcon } from "./icons";

// 侧栏书架：最上面是「＋ 新书 / 导入」，下面每本书一段（书名行 + 它的章目录）。
//
// 一个库里可以有多本书（`bootstrap` 是往当前库里加项目），所以「导入一本」是**多一段**，
// 不是换掉原来那本。换着看 = 直接点另一本那一行（整行都是命中区），没有中间那层弹窗。

/**
 * 一本书的章目录。**展开着就列出来，不管它是不是当前那本。**
 *
 * 原先这儿要求「是当前那本」才去拉，不是就摆一句「点一下书名，看这本书的章目录」——
 * 于是箭头明明朝下、里面却没有目录：**一次假的展开**，而作者已经点开它了。
 * 省下的那点请求换来的是「同一个东西要点两下才出来」（作者的原话：非常蠢的设计）。
 *
 * 「722 章的书别一次拉两份」这条顾虑还在，只是改由**折叠**来管：收起 = 这个组件根本
 * 不挂上来 = 不发请求，而收哪几本是作者自己按的。
 */
function ChapterList({
  pid,
  active,
  onOpen,
}: {
  pid: string;
  active: boolean;
  onOpen: (n: number) => void;
}) {
  const language = useLanguage((s) => s.language);
  const { chapter } = useCoords();
  const chapters = useChapters(pid);
  const create = useCreateChapter(pid);
  // 新起一章之后**直接打开它**：作者按这颗按钮就是为了往下写，停在原来那一章
  // 等于让他自己再去目录里找一次刚建的东西。
  const add = () => create.mutate(undefined, { onSuccess: (c) => onOpen(c.number) });

  if (!chapters.data)
    return <div className="empty">{language === "zh" ? "加载中…" : "Loading…"}</div>;
  return (
    <>
      {chapters.data.map((c) => (
        <div
          key={c.number}
          // 「在读的就是这一章」只画在**当前那本**上：另一本书里同号的那一章
          // 不是作者正看着的东西，画上高亮就是一句假话。
          className={
            "ch" +
            (active && c.number === chapter ? " on" : "")
          }
          onClick={() => onOpen(c.number)}
        >
          <span className="n">{String(c.number).padStart(3, "0")}</span>
          <span className="ch-name">{c.title || (language === "zh" ? "（无题）" : "(Untitled)")}</span>
        </div>
      ))}
      {/* 一章都没有的书也走这颗按钮（不再只摆一句「去导入 TXT」）：空白新书本来就该
          能直接开始写，而在此之前浏览器里根本没有「新起一章」这条路——引擎有
          （`chapters/NNNN.md` 摆在那儿），界面没有。 */}
      {chapters.data.length === 0 && (
        <div className="empty">
          {language === "zh" ? (
            <>这本书还没有章节。新起一章，或者用上面的「＋ 新书 / 导入」导入 TXT。</>
          ) : (
            <>
              This book doesn’t have any chapters yet. Start a new one, or use the “+ New Book /
              Import” above to import a TXT file.
            </>
          )}
        </div>
      )}
      {/* 屏幕上只有一个加号，**名字由 `data-tip` 悬浮画出来**（`.icon-btn::after`，
          .12s 就出来）。**不用原生 `title`**：它要等约一秒，而作者的原话是「以为没有」。
          `aria-label` 是这颗按钮的名字——一颗只有图标的按钮少了它，读屏就念不出它是干什么的。 */}
      <button
        className="icon-btn ch-add"
        aria-label={
          language === "zh"
            ? create.isPending ? "正在新起一章…" : "新起一章"
            : create.isPending ? "Starting a new chapter…" : "Start a new chapter"
        }
        data-tip={
          language === "zh"
            ? create.isPending ? "正在新起一章…" : "新起一章"
            : create.isPending ? "Starting a new chapter…" : "Start a new chapter"
        }
        disabled={create.isPending}
        onClick={add}
      >
        <PlusIcon />
      </button>
      {create.isError && <div className="ch-add-err">{newChapterError(create.error)}</div>}
    </>
  );
}

/**
 * 这本书的语言 + 改它的入口（国际化第一批 ②）。**默认从正文推，这儿只是覆盖口**：
 * 一本中文小说夹了大量英文引文会推错，作者点一下就能改回来——同「右栏 LLM 生成、
 * 作者可见可改」那条口径，机器猜的，人改了就听人的。
 *
 * 单独抽成组件是因为它要按每本书各调一次 `useSetProjectLanguage`（绑死 pid 的
 * mutation），而 hooks 不能在 `books.map()` 那个回调里直接调——同 `ChapterList`
 * 已经踩过的同一条 Rules of Hooks。
 */
function BookLanguageToggle({ pid, language: bookLanguage }: { pid: string; language: "zh" | "en" }) {
  const language = useLanguage((s) => s.language);
  const setLanguage = useSetProjectLanguage(pid);
  return (
    <div className="book-menu-lang" onClick={(e) => e.stopPropagation()}>
      {/* 叫「写作语言」不叫裸的「语言」：跟顶栏「界面语言」是两件事，字面撞了作者会
          以为切这颗顺带把界面也换了语言（`SettingsDrawer.test.tsx` 「跟书的语言不是
          同一个词」那条钉的就是这缝）。标题单独一行——跟两颗按钮挤一行在 180px 的
          菜单里会溢出。 */}
      <span className="book-menu-lang-label">
        {language === "zh" ? "写作语言" : "Writing language"}
      </span>
      <span className="book-menu-lang-buttons">
        {/* **这两个按钮的文字不跟着界面语言换**（同顶栏「界面语言 / Interface language」
            那颗开关的既有做法）：它们是语言的名字本身，不是这块界面此刻说的话——
            一个中文作者切到英文界面时，还是要认得出哪颗按钮能把书切回中文。 */}
        <button
          className={bookLanguage === "zh" ? "on" : ""}
          aria-pressed={bookLanguage === "zh"}
          disabled={setLanguage.isPending}
          onClick={() => setLanguage.mutate("zh")}
        >
          中文
        </button>
        <button
          className={bookLanguage === "en" ? "on" : ""}
          aria-pressed={bookLanguage === "en"}
          disabled={setLanguage.isPending}
          onClick={() => setLanguage.mutate("en")}
        >
          English
        </button>
      </span>
    </div>
  );
}

export function BookShelf({ onOpenChapter }: { onOpenChapter: (n: number) => void }) {
  const language = useLanguage((s) => s.language);
  const projects = useProjects();
  const { projectId, setProject, openBookAt } = useCoords();
  const { hidden, collapsed, remove, restoreAll, toggleCollapsed } = useShelf();
  const [setup, setSetup] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const all = projects.data ?? [];
  const books = shelved(all, hidden);

  // 点别处关掉「⋯」菜单。不做的话它会一直挂在那儿，而作者以为自己已经点掉了。
  useEffect(() => {
    if (!menuFor) return;
    const close = () => setMenuFor(null);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [menuFor]);

  function removeBook(id: string) {
    const next = nextAfterRemoving(all, hidden, id);
    // 有下一本可切才切：不切的话，作者刚移除的还是他正在看的那本，中栏/右栏会继续
    // 显示一本左边已经找不到的书——他分不清自己是不是真的移除成功了。
    // 拿掉的是最后一本时**不切**：架子空了，但中栏/右栏原样继续显示这本书，
    // 作者不会因为左边空了就看不见自己正在写的东西（`bookshelf.ts::shelved` 那条）。
    if (id === projectId && next) setProject(next);
    remove(id);
    setMenuFor(null);
  }

  return (
    <>
      <button className="shelf-add" onClick={() => setSetup(true)}>
        {language === "zh" ? "＋ 新书 / 导入" : "+ New Book / Import"}
      </button>

      {/* 只有这一截会滚，「＋ 新书 / 导入」钉在上面不跟着走（`styles.css` 的
          `.shelf-scroll` 那条注释）。 */}
      <div className="shelf-scroll">
        {books.length === 0 && (
          <div className="empty">
            {language === "zh" ? (
              <>
                左边还没有摆书。<a onClick={restoreAll}>把移除的书放回来</a>
              </>
            ) : (
              <>
                There are no books on the shelf yet.{" "}
                <a onClick={restoreAll}>Bring back the ones you removed</a>
              </>
            )}
          </div>
        )}

        {books.map((p) => {
          const active = p.id === projectId;
          const folded = collapsed.includes(p.id);
          return (
            <div className={"book" + (active ? " on" : "") + (folded ? "" : " open")} key={p.id}>
              {/* 悬浮底色挂在**整行**上（`.book-head`），所以右边那个 ⋯ 也被它罩着。
                  挂在 `.book-row` 上的话，底色会在 ⋯ 前面断掉一截。 */}
              <div className="book-head">
                {/* **整行是一个按钮**（图标/箭头、书名、「章目录」都在里面）。
                    只有一个 11px 的小箭头能点的话，作者得瞄准才打得开自己的书。
                    aria-label 只给书名：读屏念「111935，已展开」，不是把一行零件念一遍。 */}
                <button
                  className="book-row"
                  aria-expanded={!folded}
                  aria-label={p.name}
                  title={p.name}
                  onClick={() => {
                    // 点别的书 = 打开它（并保证章目录是展开的）；点当前这本 = 收起/展开。
                    if (!active) {
                      setProject(p.id);
                      if (folded) toggleCollapsed(p.id);
                    } else {
                      toggleCollapsed(p.id);
                    }
                  }}
                >
                  {/* 同一个格子里叠着书的图标和箭头，**谁显示由 CSS 决定**：
                      没悬浮也没展开 = 图标；悬浮或已展开 = 箭头。
                      用 hover 的 JS state 做这件事会让整行在鼠标经过时重渲染，
                      而且和 :hover 之间必然有一帧对不上。 */}
                  <span className="book-mark" aria-hidden="true">
                    <span className="book-icon" />
                    {/* 箭头是 CSS 画的三角形、靠旋转表示方向，不是 ▸/▾ 那两个字符：
                        那两个字形在 10–12px 下小得看不出朝哪边，而且各字体画得还不一样。 */}
                    <span className="book-fold" />
                  </span>
                  <span className="book-name">{p.name}</span>
                  <span className="book-kind">
                    {language === "zh" ? "章目录" : "Chapters"}
                  </span>
                </button>
                <span className="book-more">
                  <button
                    className="book-act"
                    aria-label={
                      language === "zh"
                        ? `《${p.name}》的更多操作`
                        : `More actions for ${p.name}`
                    }
                    aria-expanded={menuFor === p.id}
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuFor(menuFor === p.id ? null : p.id);
                    }}
                  >
                    ⋯
                  </button>
                  {menuFor === p.id && (
                    <div className="book-menu" onPointerDown={(e) => e.stopPropagation()}>
                      <BookLanguageToggle pid={p.id} language={p.language} />
                      <button
                        title={
                          language === "zh"
                            ? "只是从左边拿走，书和稿子都还在"
                            : "This only takes it off the shelf — the book and its drafts are still there"
                        }
                        onClick={() => removeBook(p.id)}
                      >
                        {language === "zh" ? "移除本书目录" : "Remove this book from the shelf"}
                      </button>
                    </div>
                  )}
                </span>
              </div>

              {!folded && (
                <ChapterList
                  pid={p.id}
                  active={active}
                  // 点另一本书的某一章 = **连书带章一起翻过去，一步到位**。
                  // 拆成「先切书、再换章」的话，中间那一下是一次纯粹的换书，
                  // 光标会先落到那本书的最后一章——他点第 1 章，屏幕上闪过第 722 章。
                  //
                  // 也**不走 `onOpenChapter`**：那条路会给刚离开的那一章发一次后台整理
                  //（「离开 = 那一章写完了」），而他只是翻去了另一本书。
                  onOpen={(n) => (active ? onOpenChapter(n) : openBookAt(p.id, n))}
                />
              )}
            </div>
          );
        })}

        {/* 「移除」的回头路。真有书被拿掉、且左边还有别的书显示着才出现——**架子空了
            不重复它**：那时最上面的空状态消息（`books.length === 0` 那段）已经带着
            同一个「放回来」，两条一起摆着是纯噪音。没有它，移除就等于把书弄丢了
            （切书弹窗撤掉之后，这是这一档下仅剩的恢复入口）。 */}
        {hidden.length > 0 && books.length > 0 && (
          <div className="shelf-hidden">
            {language === "zh" ? (
              <>有 {hidden.length} 本没摆在左边 · </>
            ) : (
              <>{hidden.length} book{hidden.length === 1 ? "" : "s"} not on the shelf · </>
            )}
            <a onClick={restoreAll}>{language === "zh" ? "放回来" : "Bring back"}</a>
          </div>
        )}
      </div>

      {setup && <Setup onClose={() => setSetup(false)} />}
    </>
  );
}
