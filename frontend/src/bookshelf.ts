import { create } from "zustand";

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
