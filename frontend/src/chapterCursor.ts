// 「打开一本书，该停在第几章」——只此一处。
//
// 这条规则原先躺在 TopBar 的一个 effect 里（换书 → 第一章），而 TopBar 恰好也在渲染章节
// 下拉框，于是「换书去哪一章」变成了一个渲染组件的私事。抽出来是为了它能被单测钉住：
// 它决定作者每次打开书看到的第一屏，错了不会报错，只会天天让人多滚一次。

/** 章号在这本书里存不存在 + 是不是刚换的书 → 该落到哪一章。`null` = 不动。 */
export function chapterOnOpen(opts: {
  /** 这一次是不是换了一本书（含首次打开）。 */
  switched: boolean;
  chapter: number;
  /** 这本书现有的章号，升序（就是 `GET /chapters` 的顺序）。 */
  numbers: number[];
}): number | null {
  const { switched, chapter, numbers } = opts;
  // 一章都没有：没什么可落的。停在原地，由中栏的空状态去说明白。
  if (numbers.length === 0) return null;
  const tail = numbers[numbers.length - 1];
  // 换书 → 永远落到**最后一章**：日更作者打开书是为了接着写，不是从头读。
  if (switched) return tail;
  // 没换书，但当前章号在这本书里已经不存在了（稿子在别的编辑器里被删过）：
  // 与其停在一个不存在的章号上渲染空编辑器，不如落到看得见的那一章。
  return numbers.includes(chapter) ? null : tail;
}
