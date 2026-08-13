import { useEffect, useState } from "react";
import { useChapters, useProjects } from "../api/hooks";
import { useCoords } from "../store";
import { nextAfterRemoving, removable, shelved, useShelf } from "../bookshelf";
import { Setup } from "./Setup";

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
  const { chapter } = useCoords();
  const chapters = useChapters(pid);
  if (!chapters.data) return <div className="empty">加载中…</div>;
  if (chapters.data.length === 0) {
    return <div className="empty">这本书还没有章节。用上面的「＋ 新书 / 导入」导入一份 TXT。</div>;
  }
  return (
    <>
      {chapters.data.map((c) => (
        <div
          key={c.number}
          // 「在读的就是这一章」只画在**当前那本**上：另一本书里同号的那一章
          // 不是作者正看着的东西，画上高亮就是一句假话。
          className={"ch" + (active && c.number === chapter ? " on" : "")}
          onClick={() => onOpen(c.number)}
        >
          <span className="n">{String(c.number).padStart(3, "0")}</span>
          {c.title || "（无题）"}
        </div>
      ))}
    </>
  );
}

export function BookShelf({ onOpenChapter }: { onOpenChapter: (n: number) => void }) {
  const projects = useProjects();
  const { projectId, setProject, openBookAt } = useCoords();
  const { hidden, collapsed, remove, restoreAll, toggleCollapsed } = useShelf();
  const [setup, setSetup] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const all = projects.data ?? [];
  const books = shelved(all, hidden, projectId);

  // 点别处关掉「⋯」菜单。不做的话它会一直挂在那儿，而作者以为自己已经点掉了。
  useEffect(() => {
    if (!menuFor) return;
    const close = () => setMenuFor(null);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [menuFor]);

  function removeBook(id: string) {
    const next = nextAfterRemoving(all, hidden, projectId, id);
    // 先切走再拿掉：顺序反了的话，被拿掉的那本还是当前书，`shelved` 会把它留在架子上
    // （它护着「当前那本永远可见」），于是这一下看起来什么都没发生。
    if (id === projectId && next) setProject(next);
    remove(id);
    setMenuFor(null);
  }

  return (
    <>
      <button className="shelf-add" onClick={() => setSetup(true)}>
        ＋ 新书 / 导入
      </button>

      {books.length === 0 && (
        <div className="empty">
          左边还没有摆书。<a onClick={restoreAll}>把移除的书放回来</a>
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
                <span className="book-kind">章目录</span>
              </button>
              <span className="book-more">
                <button
                  className="book-act"
                  aria-label={`《${p.name}》的更多操作`}
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
                    <button
                      disabled={!removable(all, hidden, projectId)}
                      title={
                        removable(all, hidden, projectId)
                          ? "只是从左边拿走，书和稿子都还在"
                          : "左边就剩这一本了"
                      }
                      onClick={() => removeBook(p.id)}
                    >
                      从左边移除
                    </button>
                    <div className="book-menu-note">书和稿子都不会被删掉，随时能再打开。</div>
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

      {/* 「移除」唯一的回头路。只在真有书被拿掉时出现——**没有它，移除就等于把书弄丢了**
          （切书弹窗撤掉之后，这是仅剩的恢复入口）。 */}
      {hidden.length > 0 && (
        <div className="shelf-hidden">
          有 {hidden.length} 本没摆在左边 · <a onClick={restoreAll}>放回来</a>
        </div>
      )}

      {setup && <Setup onClose={() => setSetup(false)} />}
    </>
  );
}
