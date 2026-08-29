import type { StateSnapshot } from "../api/types";
import { useLanguage, type Language } from "../language";

/** 右栏「人物状态」：在场角色卡。纯展示，数据来自 /state 的收窄出参。
 *
 *  **`undefined` 和 `[]` 说两句不一样的话**（§10 约束 8：静默的零和真的零不许长得一样）。
 *  「还没读回来」和「这一章一个人都没有」在屏幕上曾经是同一句「无在场角色」，而
 *  「看上一章」那个开关每按一次都会换一个查询键 —— 于是那半秒钟的空窗会稳定地
 *  告诉作者「上一章收尾时一个人都不在」，而那是一句假话。 */
export function StateCards({ states }: { states?: StateSnapshot[] }) {
  const language = useLanguage((s) => s.language);
  if (!states) return <div className="empty">{language === "zh" ? "正在读…" : "Loading…"}</div>;
  if (states.length === 0)
    return <div className="empty">{language === "zh" ? "无在场角色。" : "No one present."}</div>;
  return (
    <div>
      {states.map((s) => (
        <div className="statecard" key={s.node.id}>
          <div className="nm">
            {s.node.name}
            {s.is_dead && <span className="dead"> · {language === "zh" ? "已亡" : "Deceased"}</span>}
          </div>
          <div className="row">
            {language === "zh" ? "所在地：" : "Location: "}
            {s.location ? s.location.name : language === "zh" ? "未记录" : "Not recorded"}
          </div>
          {s.states.map((v, i) => (
            <div className="row" key={i}>
              {"name" in v.dim ? v.dim.name : ""}：{v.value || ""}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** 「人物状态」那一格的全部：一个「看上一章」开关 + 卡片本身。
 *
 *  ── 它做的事 ─────────────────────────────────────────────────────────────
 *
 *  作者写第 N 章时要回答的是「上一章收尾时谁在哪、谁知道了什么、谁已经不在了」。
 *  引擎一直答得出——`[valid_from, valid_to)` 那套时态查询把「第几章的状态」做成了
 *  路径参数（`GET …/chapters/{chapter}/state`，`/characters/{id}/state` 那条还专门
 *  注着「AS OF 第几章，**不写进任何数据**」）。**缺的只是一个开关**：右栏此前永远
 *  只问「当前章」，那台时光机一直停在同一个刻度上。所以这里后端一个字都没改，
 *  换的只是请求里那个数。
 *
 *  ── 为什么它不违反约束 10（作者永不填章号）────────────────────────────────
 *
 *  **它给不出「第几章」这个数。** 屏幕上只有「本章 / 上一章」两个位置，而「上一章」
 *  是从作者此刻停在第几章**减一**推出来的，他敲不进任何数字。约束 10 禁的是
 *  「作者的输入到得了 `valid_from`」（`tests/test_no_chapter_input.py` 的判据），
 *  而这条路径上根本没有作者的输入，也根本不写库。
 *
 *  **绝不能做成一个能输入任意章号的框**——那就越线了，而且会当场撞红
 *  `tests/test_canon_edit_boundary.py::test_no_screen_in_the_whole_workbench_posts_a_chapter`
 *  （零基线：全前端只许有一个 `<input type="number">`，就是起草长度那一个）。
 *  同一天维护者裁掉的 `first_appears_chapter` 输入框是同一条线上的事，
 *  理由写在 `ARCHITECTURE.md` 已知洞第 8 条。
 *
 *  ── 打开时屏幕上必须一眼看得出「这不是本章」──────────────────────────────
 *
 *  这是这块屏幕唯一真正的风险：作者对着**上一章**的局面去声明、去改本章的事实，
 *  那是引擎自己造出来的错——而且它造出来的是一条 `valid_from` 错了的 CANON 边，
 *  在面板上长得完全正常。所以「不是本章」不是一行小字：整块内容换底色 + 换边框，
 *  顶上一条横幅同时说出**两个**章号（在看第几章 / 你在写第几章），
 *  右栏那一格的标签也跟着改成「人物状态 · 第 N 章」——面板滚下去之后横幅看不见了，
 *  标签还在。 */
export function StateTab({
  states,
  chapter,
  lookingBack,
  onLookBack,
}: {
  states?: StateSnapshot[];
  /** 作者此刻停在第几章。**不是正在看的那一章**——看的是哪一章由 `lookingBack` 决定。 */
  chapter: number;
  lookingBack: boolean;
  onLookBack: (on: boolean) => void;
}) {
  const language = useLanguage((s) => s.language);
  const prev = chapter - 1;
  const first = prev < 1;

  if (!lookingBack) {
    return (
      <div>
        <div className="lookback-bar">
          <button disabled={first} onClick={() => onLookBack(true)}>
            {language === "zh" ? "看上一章结束时" : "See how the previous chapter ended"}
          </button>
          {/* 零带着一句理由（§10 约束 8）：一颗灰着的按钮不说为什么，作者只会以为它坏了。 */}
          <span className="lookback-why">{lookbackWhy(prev, first, language)}</span>
        </div>
        <StateCards states={states} />
      </div>
    );
  }

  return (
    <div className="lookback">
      <div className="lookback-head">
        <span className="lookback-badge">{language === "zh" ? "上一章" : "Previous chapter"}</span>
        <b>
          {language === "zh" ? `正在看第 ${prev} 章结束时` : `Looking at the end of chapter ${prev}`}
        </b>
        <span className="spacer" />
        <button onClick={() => onLookBack(false)}>
          {language === "zh" ? `回到第 ${chapter} 章` : `Back to chapter ${chapter}`}
        </button>
      </div>
      <div className="lookback-note">
        {language === "zh" ? (
          <>
            下面每一张卡说的都是第 {prev} 章收尾那一刻，<b>不是</b>你正在写的第 {chapter} 章。
            要改这一章的事实，先回到第 {chapter} 章。
          </>
        ) : (
          <>
            Every card below reflects the end of chapter {prev}, <b>not</b> chapter {chapter},
            which you’re currently writing. To edit facts for this chapter, go back to chapter{" "}
            {chapter} first.
          </>
        )}
      </div>
      <StateCards states={states} />
    </div>
  );
}

/** 「看上一章」按钮旁那句为什么（或者为什么按不动）。**整句模板**：中英文里
 *  「谁在哪、谁知道了什么、谁已经不在了」那半句嵌进主句的位置不一样。 */
function lookbackWhy(prev: number, first: boolean, language: Language): string {
  if (language === "zh") {
    return first
      ? "第 1 章是全书的开头，它前面没有一章可看。"
      : `第 ${prev} 章收尾那一刻谁在哪、谁知道了什么、谁已经不在了。`;
  }
  return first
    ? "Chapter 1 is the beginning of the book — there's no earlier chapter to look at."
    : `Who was where, who knew what, and who was already gone by the end of chapter ${prev}.`;
}
