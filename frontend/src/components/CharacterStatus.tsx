import { useMemo } from "react";
import { useCharacterProfile, useCharacterState } from "../api/hooks";
import type { CharacterProfileView, StateValue } from "../api/types";
import { type Language, useLanguage } from "../language";
import { useCoords } from "../store";

// 角色卡里的「状态」——原「人物状态」tab（`StateCards`）按「这一章在场的所有人」
// 批量展示的那份数据，改成按「这一个人」单独取（`GET /characters/{id}/state`，
// `useCharacterState` 早就存在，之前只喂给了已经删掉的那个 tab）。
//
// `states: StateValue[]` 的维度名是模型/作者自由写的正文（同人物名、地点名，
// 不跟界面语言翻译）——`api/types.ts` 里 `StateValue.dim` 的注释举的例子就是
// 「武功境界」。这不是一张写死的「修为/年龄/出生地」表单：项目里声明过什么维度，
// 这里就列什么维度，没声明过的书这一格就是空的（下面空态说清楚是为什么）。
//
// ── 2026-09-04（二）：基础资料并进这一格 ────────────────────────────────────
//
// 作者裁定：**「基础是状态里面的一个子集」**——性别/性格/出身/备注不该另开一格站在
// 「状态」旁边，它们就是「他现在是什么样」的一部分。所以那四行现在排在这张卡的最
// 上面，`CharacterBasicInfo` 只剩别名。
//
// **它们和下面那些状态不是一种东西，这一点靠「有没有第 N 章起」区分，不靠分组**：
// 这四项存在人物节点的 props 上（一本书一份，抽取覆盖式写入，`character_profiles`），
// 没有时态、没有证据、也不会被新值 supersede；下面每一条是一条 `HAS_STATE` 边，
// 带生效章、带证据、新值盖旧值。所以四项基础后面**不印**「第 N 章起」，
// 「（最新）」也只在真状态之间比——拿一个没有章号的东西去争「最新」是假的。
//
// ⚠️ **这四项今天只读**：`event_store.update_profile` 存在，但没有任何一条路由通到
// 它。要能改就得开一条 PATCH（照 `characters.py` 改名那条的 `expected_canon_version`
// 形状），不在这次范围里。
//
// ── 2026-09-04：每条状态印出「第 N 章起」，最新那条打「（最新）」 ─────────────
//
// 作者原话：「这个状态有新有旧……你最新状态就要加那个最新的标签在括号标签」。
// 这一格拿到的**每一条都是在这一章仍然有效的值**（`state_at` 已经把被覆盖的旧值
// 滤掉了），所以「新旧」说的不是「哪条还算数」，而是**哪条是刚变的**：修为是第 18
// 章刚破的境，年龄还是第 3 章交代的那个数，两条都成立但份量完全不同。
//
// 排序键就是屏幕上印着的那个数（`since_chapter`）——**不是另一个只活在数据里的量**，
// 那条教训在 `RosterTab.tsx::bySignal` 的顶注里（作者当时的原话是「我看这个排序好像
// 不是按照这个顺序来」）。只有一条状态时不打标签：那时「最新」什么也没区分。
//
// **按「作者此刻在写的章」取，不做「看上一章」**：旧 `StateTab` 那套 lookback
// 是为了防止作者对着上一章的快照误改本章事实——这一格是纯只读展示，不接编辑
// 入口（编辑仍然只能从活动记录 → `CanonEdgeEditor` 走），那个风险不存在。
/** 有值的那几项基础资料，按「先说他是谁、再说他什么样」的顺序。
 *
 *  **空的一项不占一行**：四项全空是这本书的常态（没跑过抽取、或者正文真没交代），
 *  摆四行「未记录」等于给作者看一张空表单——那正是这个仓库禁掉「暂无数据」的理由。 */
function basicRows(
  profile: CharacterProfileView | null,
  language: Language,
): [string, string][] {
  if (!profile) return [];
  const labels: [keyof CharacterProfileView, string, string][] = [
    ["gender", "性别", "Gender"],
    ["personality", "性格", "Personality"],
    ["background", "出身", "Background"],
    ["character_notes", "备注", "Notes"],
  ];
  const rows: [string, string][] = [];
  for (const [key, zh, en] of labels) {
    const value = profile[key];
    if (typeof value === "string" && value.trim()) {
      rows.push([language === "zh" ? zh : en, value.trim()]);
    }
  }
  return rows;
}

export function CharacterStatus({ characterId }: { characterId: string }) {
  const { projectId, chapter } = useCoords();
  const language = useLanguage((s) => s.language);
  const state = useCharacterState(projectId, characterId, chapter);
  // 同一个 query key 已经被别名那一格取过一次（`useCharacterProfile`），
  // react-query 直接给缓存，不会因为这一格再多发一次请求。
  const profile = useCharacterProfile(projectId, characterId);
  const data = state.data;
  const basics = basicRows(profile.data?.profile ?? null, language);
  // 一个字段一组，组内新的在前；组与组之间按「这一格最近一次变化」排（改得最近的在上）。
  //
  // ⚠️ **拿的是 `state_history` 不是 `states`**（2026-09-06）。`states` 每格只有当前
  // 那一条，按它分组等于 40 个各含一条的组——屏幕上一个字都不会变。作者要的是
  // 「一个字段对应多个可填，最新的打个标记，都带章节」，那要的就是历史。
  // 待过的地方，新的在前。**兜底回当前那一条**：`location_history` 只有
  // `/characters/{id}/state` 这条路由带（批量那条没有），别让它在别处渲成空。
  const visits = useMemo(() => {
    const raw = data?.location_history;
    if (raw && raw.length > 0) return [...raw].sort((a, b) => b.since_chapter - a.since_chapter);
    return data?.location ? [{ place: data.location, since_chapter: data.chapter }] : [];
  }, [data]);

  const { setChapter, setPage } = useCoords();
  const openChapter = (n: number) => {
    setChapter(n);
    setPage("workbench");
  };

  const groups = useMemo(() => {
    const byDim = new Map<string, StateValue[]>();
    for (const v of data?.state_history ?? []) {
      const name = "name" in v.dim ? v.dim.name : "";
      const list = byDim.get(name);
      if (list) list.push(v);
      else byDim.set(name, [v]);
    }
    for (const list of byDim.values()) list.sort((a, b) => b.since_chapter - a.since_chapter);
    return [...byDim.entries()].sort((a, b) => b[1][0].since_chapter - a[1][0].since_chapter);
  }, [data]);

  return (
    <div className="grp">
      <div className="lab">{language === "zh" ? "状态" : "Status"}</div>
      {state.isLoading && (
        <span className="empty">{language === "zh" ? "读取中…" : "Loading…"}</span>
      )}
      {(data || basics.length > 0) && (
        <div className="statecard">
          {basics.map(([label, value]) => (
            <div className="row" key={label}>
              {label}
              {language === "zh" ? "：" : ": "}
              {value}
            </div>
          ))}
          {data && (
            // 「所在地」和下面那些字段**同一套画法**（作者 2026-09-06：「也用这种机制」）：
            // 一行、行内一串带章号的地方、最新那个打标记。位置是最爱变的一格，
            // 也是「同一处两个叫法」最多的一格——把走过的地方列出来，作者一眼就能看出
            // 「宁荣街」和「荣国府」是不是同一处。
            <div className="row st-dim st-loc">
              <span className="st-name">{language === "zh" ? "所在地：" : "Location: "}</span>
              {visits.length === 0 ? (
                <span className="st-val">
                  {language === "zh" ? "未记录" : "Not recorded"}
                </span>
              ) : (
                visits.map((v, i) => (
                  <span className="st-val" key={i}>
                    {v.place.name}
                    <span className="dim">
                      {language === "zh" ? ` 第 ${v.since_chapter} 章` : ` ch.${v.since_chapter}`}
                    </span>
                    {i === 0 && visits.length > 1 && (
                      <span className="fresh">{language === "zh" ? "（最新）" : " (latest)"}</span>
                    )}
                    {v.author_owned && (
                      <button
                        type="button"
                        className="mine"
                        title={
                          language === "zh"
                            ? `你改过这一格 · 去看第 ${v.since_chapter} 章`
                            : `You edited this · go to chapter ${v.since_chapter}`
                        }
                        onClick={() => openChapter(v.since_chapter)}
                      >
                        {language === "zh" ? "你改过" : "edited"}
                      </button>
                    )}
                  </span>
                ))
              )}
              {data.is_dead && (
                <span className="dead"> · {language === "zh" ? "已亡" : "Deceased"}</span>
              )}
            </div>
          )}
          {!data ? null : groups.length === 0 ? (
            <span className="empty">
              {language === "zh" ? (
                <>
                  还没有记录他的状态。修为、伤势、身上带着什么这一类会随剧情变的信息
                  要等系统整理正文时写下来，或者作者自己在活动记录里补——不是这本书
                  没有，是还没人写。
                </>
              ) : (
                <>
                  No status recorded yet. Things that change as the story goes on — cultivation
                  level, injuries, what they carry — get written here once the system processes
                  the text, or the author adds them from the activity log. It’s not that this
                  book has none, just that none have been recorded yet.
                </>
              )}
            </span>
          ) : (
            groups.map(([name, list]) => (
              <div className="row st-dim" key={name}>
                <span className="st-name">
                  {name}
                  {language === "zh" ? "：" : ": "}
                </span>
                {list.map((v, i) => (
                  <span className="st-val" key={i}>
                    {v.value || (language === "zh" ? "（空）" : "(empty)")}
                    <span className="dim">
                      {language === "zh" ? ` 第 ${v.since_chapter} 章` : ` ch.${v.since_chapter}`}
                    </span>
                    {/* **「最新」是每一格自己的最新**，不是全卡最新——作者问的是
                        「这个字段现在是什么」，那要跟同一格里的旧值比。
                        只有一个值时不打：那时它什么也没区分。 */}
                    {i === 0 && list.length > 1 && (
                      <span className="fresh">{language === "zh" ? "（最新）" : " (latest)"}</span>
                    )}
                    {/* **作者亲手改过的那一格要看得出来**（作者 2026-09-06）。
                        点它跳到那一章 —— 他改的时候看的是哪一章的原文，那一章就是
                        他自己该去核对的地方。判据是库里那一行 `canon_edge_override`
                        （`author_owned`），不是猜。 */}
                    {v.author_owned && (
                      <button
                        type="button"
                        className="mine"
                        title={
                          language === "zh"
                            ? `你改过这一格 · 去看第 ${v.since_chapter} 章`
                            : `You edited this · go to chapter ${v.since_chapter}`
                        }
                        onClick={() => openChapter(v.since_chapter)}
                      >
                        {language === "zh" ? "你改过" : "edited"}
                      </button>
                    )}
                  </span>
                ))}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
