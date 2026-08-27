import { create } from "zustand";
import { ApiError } from "./api/client";
import { saidToTheAuthor } from "./correctionError";
import { useLanguage } from "./language";

// 侧栏书架的状态：**哪几本书摆在左边、哪几本折叠着**。
//
// 这不是坐标（`useCoords` 只放坐标），是作者的桌面摆法，所以另起一个 store + localStorage。
//
// **记的是「拿掉了哪几本」，不是「摆着哪几本」。** 反过来存的话，别处新建的书（另一个
// 窗口、`nh import`）不在白名单里就永远不出现——作者会以为书没建成。存黑名单则相反：
// 库里多一本就自动出现在架子上，只有他亲手拿掉的那几本才不见。

const STORAGE_KEY = "nh.bookshelf.v1";

export interface Shelf {
  /** 被作者从侧栏拿掉的书（project id）。**只影响侧栏，不动库里的书。** */
  hidden: string[];
  /** 折叠着的书（project id）——只是收起章目录，跟摆不摆在架子上无关。 */
  collapsed: string[];
}

export const EMPTY_SHELF: Shelf = { hidden: [], collapsed: [] };

function ids(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

export function readShelf(): Shelf {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return EMPTY_SHELF;
    const parsed = JSON.parse(raw) as Partial<Shelf>;
    return { hidden: ids(parsed.hidden), collapsed: ids(parsed.collapsed) };
  } catch {
    return EMPTY_SHELF; // 存坏了就当作「一本都没拿掉」——空架子比坏架子更糟
  }
}

function writeShelf(shelf: Shelf): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(shelf));
  } catch {
    // 隐私模式下 setItem 会抛。记不住摆法不该让侧栏崩掉。
  }
}

export interface Book {
  id: string;
  name: string;
}

/**
 * 架子上该显示哪几本，按库里的顺序。
 *
 * **当前正在看的那本永远在架子上**：它要是被藏起来，右栏显示着它的内容、左边却找不到它，
 * 而「⋯ → 移除」的入口也跟着消失——作者会卡在一个自己弄不回来的状态里。
 */
export function shelved<T extends Book>(all: T[], hidden: string[], activeId: string | null): T[] {
  return all.filter((b) => !hidden.includes(b.id) || b.id === activeId);
}

/** 这本能不能从架子上拿掉。**最后一本不许拿**：拿掉就没有书名行，也就没有切书的入口了。 */
export function removable<T extends Book>(
  all: T[],
  hidden: string[],
  activeId: string | null,
): boolean {
  return shelved(all, hidden, activeId).length > 1;
}

/** 拿掉 `id` 之后该切到哪一本（它是当前那本时才用得上）。没有下一本 → `null`。 */
export function nextAfterRemoving<T extends Book>(
  all: T[],
  hidden: string[],
  activeId: string | null,
  id: string,
): string | null {
  const left = shelved(all, hidden, activeId).filter((b) => b.id !== id);
  return left[0]?.id ?? null;
}

interface ShelfStore extends Shelf {
  /** 从侧栏拿掉一本（**只是拿下架子，书还在库里**）。 */
  remove: (id: string) => void;
  /** 把拿掉的全放回来。**这是「移除」唯一的回头路**，所以只要 `hidden` 非空，
   *  界面上就必须有一个地方能调到它——否则移除等于把书弄丢了。 */
  restoreAll: () => void;
  toggleCollapsed: (id: string) => void;
}

export const useShelf = create<ShelfStore>((set) => {
  const persist = (next: Shelf): Shelf => {
    writeShelf(next);
    return next;
  };
  return {
    ...readShelf(),
    remove: (id) =>
      set((s) =>
        s.hidden.includes(id)
          ? s
          : persist({ hidden: [...s.hidden, id], collapsed: s.collapsed }),
      ),
    restoreAll: () =>
      set((s) => (s.hidden.length === 0 ? s : persist({ hidden: [], collapsed: s.collapsed }))),
    toggleCollapsed: (id) =>
      set((s) =>
        persist({
          hidden: s.hidden,
          collapsed: s.collapsed.includes(id)
            ? s.collapsed.filter((x) => x !== id)
            : [...s.collapsed, id],
        }),
      ),
  };
});

/**
 * 「＋ 新起一章」没成时，屏幕上该说哪句话。
 *
 * ── 为什么这儿允许有一句前端自己的话 ──────────────────────────────────────
 *
 * 这个仓库删过一份「码 → 中文」的映射表：措辞有两个源，后端改了一句话，前端那份
 * 还在说旧的。规矩因此是**措辞的源只能有一个**——国际化第四批之后那个源是
 * `saidToTheAuthor`（`correctionError.ts`）：认得的码在 `backendMessages.ts` 里
 * 整句渲染，不认得的退回后端的 `message`（过渡期）。这两个函数现在都先问它。
 *
 * **这儿只剩一个真正的例外**，而它恰恰是 `saidToTheAuthor` 管不到的地方：
 * **服务上根本没有这条路**（旧版进程还开着，前端已经是新的——2026-08-14 作者就
 * 撞上了这一档）。那时后端说不出话，它不认识这个端点，`error.body` 里连 `error`
 * 码都没有——判据精确：404 **且** body 里没有 `error`。带 `error` 的 404
 * （`project_not_found`）已经是后端在说话，走上面那条共享路径。
 *
 * 那句话里**不许出现任何一条命令**：产品的最终用户是那位「用 WPS、不想碰命令行」
 * 的作者，对他说「去重启某个命令」等于让他卡死（`test/screenGuard.ts` 第五张网收这个）。
 */
export function newChapterError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404 && !error.body.error) {
      const language = useLanguage.getState().language;
      return language === "zh"
        ? "这台电脑上的程序是旧的一版，还不认得「新起一章」。把工作台关掉、重新打开一次就好。"
        : "The app on this computer is an older version and doesn't recognize \"new chapter\" yet. Close the workbench and reopen it.";
    }
    const said = saidToTheAuthor(error);
    if (said) return said;
  }
  const language = useLanguage.getState().language;
  return language === "zh" ? "没能新起一章，再点一次试试。" : "Couldn't create a new chapter — try again.";
}

/**
 * 删一章没成时，屏幕上该说哪句话。
 *
 * 同 `newChapterError`：**只有「服务上根本没这条路」那一档由前端说**（判据一样精确：
 * 404 且 body 里没有 `error` 码），别的一律走 `saidToTheAuthor` 那条共享路径。
 *
 * 后端那句话尤其不许在这儿改写：拒绝的时候它带着**数出来的明细**
 *（「证据 3 / 关系 2 / 情节 1 ⋯」），而那串数字正是作者判断「这一章到底还连着什么」
 * 的唯一依据。前端替它换一句笼统的「删不掉」，就等于把他赶去文件夹里自己动手删——
 * 那条路上引擎的记忆一条都不会被清理。
 */
export function deleteChapterError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404 && !error.body.error) {
      const language = useLanguage.getState().language;
      return language === "zh"
        ? "这台电脑上的程序是旧的一版，还不认得「删除本章」。把工作台关掉、重新打开一次就好。"
        : "The app on this computer is an older version and doesn't recognize \"delete this chapter\" yet. Close the workbench and reopen it.";
    }
    const said = saidToTheAuthor(error);
    if (said) return said;
  }
  const language = useLanguage.getState().language;
  return language === "zh" ? "没能删除本章，再点一次试试。" : "Couldn't delete this chapter — try again.";
}
