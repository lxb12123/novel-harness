// 章标题**就是正文的第一个非空行**。这不是这儿定的规矩，是后端定的：
// `importer.chapter_files()` 列章目录时「只读到首个非空行为止」，读到什么、目录上就写什么。
//
// 所以「改标题」不是改一个字段，而是**改正文的第一行**。没有第二个存放处，
// 也**没有一条 rename 端点**：有的话就是同一件事的第二个入口，而且它会和作者手上
// 那份还没保存的正文打架（后写的那次覆盖先写的那次，没有任何提示）。
// 改完照旧走「保存」那条老路：写磁盘 → `importer.sync`。
//
// ⚠️ **保存时后端会拿 `CHAPTER_RE` 重新切一次章**：第一行不再长得像「第N章…」的话
// 那次保存会被拒（422，顶栏那行状态会把拒绝理由原样说出来）。改名这条路
// **在结构上撞不到它**：章标那一段（`第22章`）根本不进输入框，作者只改得动它后面的名字。
//
// ── 下面那条正则是**第二份副本**，而它有一道守卫钉着 ──────────────────────
//
// `text/chapterize.py` 的模块注释写着「正则是唯一真相，不留第二份副本」，这儿还是抄了一份，
// 因为「章标到哪儿结束」这个问题**必须在浏览器里当场回答**：输入框要按住的是作者
// 此刻正在改的那一行（还没保存、后端没见过），问不了后端。
//
// 代价按那条规矩付：`tests/test_chapterize.py::test_frontend_marker_regex_is_the_same_one`
// **逐字节**比对两份，改一处不改另一处必红——同 Vite 的 `outDir` 和 `api/app.py` 的 `_DIST`
// （`test_serve.py::test_vite_outdir_and_dist_agree`：两个必须同时改的字面量，靠一条测试钉那条缝）。
//
// 万一还是漂了，症状是**看得见的**，不是静默的：这边认不出章标 → 整行进输入框（就是从前的样子）；
// 这边多认出一个 → 存盘时后端切不出一章 → 422 摆在同一行上。两种都当场看得见。

/** `text/chapterize.py::CHAPTER_RE` 的逐字节副本，多括了一组「中间那截空白」。
 *
 *  上面那条守卫比对的就是它：`([ \t　]*)` 摊平成 `[ \t　]*` 之后必须和 Python 那份一字不差。
 *  没有 `m` 标志——这儿一次只看一行，而那边要在整份正文上找章界。 */
const MARKER =
  /^[ \t　]*(第[ \t　]*[0-9〇零一二三四五六七八九十百千两]+[ \t　]*[章节回])([ \t　]*)(.*?)[ \t　]*$/;

/** 标题那一行拆成两段：**章号原地不动，只有名字能改**。 */
export interface TitleParts {
  /** 章标（`第22章`）。空串 = 这一行不长得像章标（那时整行都归 `name`）。 */
  marker: string;
  /** 章标和名字之间那截空白，**原样留着**：真书里有人用全角空格，改个名不该顺手换掉它。 */
  gap: string;
  /** 章号后面那个名字（`女神？ 学姐？`）。空串 = 这一章还没起名。 */
  name: string;
}

/** 拆开标题行。认不出章标时 `marker` 给空串，整行落进 `name`（作者照旧改得动它）。 */
export function splitTitle(line: string): TitleParts {
  const hit = MARKER.exec(line);
  if (!hit) return { marker: "", gap: "", name: line.trim() };
  return { marker: hit[1], gap: hit[2], name: hit[3] };
}

/** 把改完的名字装回标题行。**章标原样带回去**，所以这条路改不坏切章。 */
export function joinTitle(parts: TitleParts, name: string): string {
  const next = name.trim();
  if (!parts.marker) return next;
  // 名字清空 = 这一章只剩章号（「第一章」本身就是个合法的第一行）。
  // 不清空章标是这儿全部的安全性：清了它，下一行正文就会顶上来当章标题。
  if (!next) return parts.marker;
  return parts.marker + (parts.gap || " ") + next;
}

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
