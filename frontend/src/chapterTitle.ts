// 章标题**就是正文的第一个非空行**。这不是这儿定的规矩，是后端定的：
// `importer.chapter_files()` 列章目录时「只读到首个非空行为止」，读到什么、目录上就写什么。
//
// 所以「改标题」不是改一个字段，而是**改正文的第一行**。没有第二个存放处，
// 也**没有一条 rename 端点**：有的话就是同一件事的第二个入口，而且它会和作者手上
// 那份还没保存的正文打架（后写的那次覆盖先写的那次，没有任何提示）。
// 改完照旧走「保存」那条老路：写磁盘 → `importer.sync`。
//
// ⚠️ **保存时后端会拿 `CHAPTER_RE` 重新切一次章**：第一行不再长得像「第N章…」的话
// 那次保存会被拒（422，顶栏那行状态会把拒绝理由原样说出来）。
// 这里**故意不抄一份章标正则来提前拦住**——那条正则是 `text/chapterize.py` 的独占物，
// 它自己的模块注释写着「正则是唯一真相，不留第二份副本」。抄过来的那份会漂，
// 而漂掉的那天，前端拦的和后端切的不是同一批标题。何况作者本来就能在正文里
// 直接把第一行改坏：这个入口**没有新增任何失败形态**，只是新增了一个改它的位置。

/** 这一份正文的章标题（首个非空行，已去掉两头空白）。整份都是空行 → `""`。 */
export function titleOf(doc: string): string {
  for (const line of doc.split("\n")) {
    const trimmed = line.trim();
    if (trimmed) return trimmed;
  }
  return "";
}

/**
 * 把正文的标题行换成 `title`，其余一个字节不动。
 *
 * **换的是「首个非空行」那一行，不是第 0 行**——两者在有前置空行的稿子里不是同一行，
 * 而 `titleOf` / 后端读的都是前者。改错行的后果是：目录上的标题没变，
 * 正文里却凭空多出一行看起来像标题的字。
 */
export function withTitle(doc: string, title: string): string {
  const lines = doc.split("\n");
  const at = lines.findIndex((line) => line.trim() !== "");
  if (at < 0) return doc ? `${title}\n${doc}` : title;
  lines[at] = title;
  return lines.join("\n");
}

/** 章目录里的一条，够不够得上作者敲的那几个字。 */
export interface ChapterHit {
  number: number;
  title: string;
}

/**
 * 按作者敲的字筛章目录。**章号和标题都算数**：722 章的书里，
 * 他记得住的往往是「第 300 章」这个号，而不是那一章叫什么。
 *
 * 空串 = 不筛（全列出来），不是「一条都不匹配」。
 */
export function findChapters<T extends ChapterHit>(list: T[], query: string): T[] {
  const q = query.trim();
  if (!q) return list;
  return list.filter(
    (item) => item.title.includes(q) || String(item.number).includes(q),
  );
}
