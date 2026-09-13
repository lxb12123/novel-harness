import { useEffect, useMemo, useState } from "react";
import {
  useAiSettings,
  useCharacterEvents,
  useDeleteNode,
  useProjects,
  useRenameNode,
  useResolveEventCastChanged,
  useRoster,
} from "../api/hooks";
import type { RosterEntry, SystemNotification } from "../api/types";
import { messageForCode, nodeLabelText } from "../backendMessages";
import { useOpenChapter } from "../chapterNavigation";
import { readCorrectionError } from "../correctionError";
import { type Language, useLanguage } from "../language";
import { useCoords } from "../store";
import { CharacterBasicInfo } from "./CharacterBasicInfo";
import { CharacterEvidence } from "./CharacterEvidence";
import { CharacterRelations } from "./CharacterRelations";
import { CharacterStatus } from "./CharacterStatus";
import { CloudFrame } from "./CloudFrame";
import { CollapseIcon, PlusIcon, SortIcon } from "./icons";
import { ModelGuide } from "./ModelGuide";
import { RosterDrawer } from "./RosterDrawer";

// 角色册：右栏的第一格，也是默认那一格。
//
// 它原先在左栏，和章目录挤在同一条 210px 里；书架进来之后那一栏是「书 → 章」的纵深，
// 而角色册问的是「这本书里有谁」——和右栏其余几格（这个人在哪 / 和谁有关系）是同一个
// 问题的不同切面，所以它属于右边。
//
// **2026-08-31：「人物状态」「人物关系」「原文依据」三个 tab 整个并进来了**
// （作者原话：「都合到这边来，这边就是角色卡的意思」）。点一个人的名字，他那一行
// 下面原地展开一张卡：本名/别名（`CharacterBasicInfo`）+ 状态（`CharacterStatus`）+
// 关系（`CharacterRelations`，图表 + 文字都有）+ 依据（`CharacterEvidence`，把
// 「✓知道 ch88」还原成当年那句原文）+ 参与过的事件（`CharacterTimeline`）。
// `focusNode` 因此改成落回这一格，不再切一个已经不存在的 tab（见 `store.ts`）。
// **这张卡只对人物展开**——地点/势力选中之后不再有专属的关系图/依据可看，
// 这是合并的直接后果，不在这次范围内另开一份「地点卡」。
//
// ── 2026-08-25 这一格多了三样，三样都是同一条裁定的产物 ────────────────────
//
// 抽取从这一天起**认不出就建**（ADR 0020 补记）。于是：
//
// 1. **出场章数 + 组内按它降序** —— 自动建出来的一次性称呼（「服务员」）会大量涌进来，
//    按名字排的话主角和路人混在一起。作者要的是「谁重要」，那个序就是出场频率。
// 2. **倒序切换** —— 反过来看正是「哪些是垃圾」：出场 0～1 章的那一批。
// 3. **每行能删** —— 模型会认错（真书上的「袭人」，满篇「寒气袭人」）。
//    **自动建 + 不能删 = 单向阀**，那个错会永远往上下文里塞噪声。

/** 后端那句话原样摆出来，**一个字不改**。
 *
 *  删除被拒时后端写的是「「北荒」还被引用着（关系 1 / 情节 0），先把那几条改掉再删他」
 *  ——那是给作者的话，不是给代码的码（`api/characters.py` 那两条路由）。
 *  在这儿写一句「删不掉这个条目」把它盖掉，就是又造出第二个措辞源，
 *  而那正是 `correctionError.ts` 顶上那段注释在讲的事。 */
function Refusal({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="err-box">{readCorrectionError(error).message}</div>;
}

/** 这一格的排序方向。**存在组件里不进 URL**：它是「我现在想怎么看」，不是坐标。 */
type Order = "desc" | "asc";

/** 组内排序：**出场章数优先，累计信息量兜底，名字保底。**
 *
 *  2026-08-31 之前主键是 `information_score`——**那是个 bug**：这一行右边显示的
 *  数字是 `appearance_chapters`（「N 章」），按钮上写的也是「写得多 → 少」，可
 *  真正决定顺序的是另一个不上屏的数。一本画像已经跑过的书两个数各走各的，
 *  屏幕上那一串「20 章、2 章、11 章、7 章…」自然看不出排序在哪
 *  （作者原话：「我看这个排序好像不是按照这个顺序来」）。**排序键必须是
 *  作者在屏幕上验证得了的那个数**，不能是一个只活在数据里的量。
 *
 *  `information_score` 退到第二级只当同分兜底：出场章数相等时，靠它分出
 *  「写得多但笔墨浅」和「写得少但笔墨深」——这一级排错了作者也感觉不到，
 *  因为两边显示的「N 章」本来就相等。
 *
 *  ── 为什么还是三级，而不是给作者一个「按哪个排」的下拉 ────────────────────
 *
 *  两个数各自会在一整类书上恒为 0：
 *
 *  - `appearance_chapters` 要等**总结**落地（没生成过总结的书全 0）；
 *  - `information_score` 要等**带画像的抽取**跑过（刚导进来的书全 0）。
 *
 *  给一个下拉的话，作者会撞上「换了个排法，一列全是 0，看起来像坏了」。
 *  三级排序自己就退化得对：出场章数分不出高下时按累计信息量，两个都分不出时按名字。
 *  **多一颗下拉不如少一种「看起来坏了」的样子。**
 *
 *  名字那一级不是装饰：没有它，同为 0 的那一大批每次渲染的顺序都不一样。 */
function bySignal(rows: RosterEntry[], order: Order): RosterEntry[] {
  const sign = order === "desc" ? -1 : 1;
  return [...rows].sort(
    (a, b) =>
      sign * (a.appearance_chapters - b.appearance_chapters) ||
      sign * (a.information_score - b.information_score) ||
      a.name.localeCompare(b.name, "zh"),
  );
}

/** 一件事掉了参与者时挂在它上面的那颗红点（Task 8 补记 / 034）。
 *
 *  **默认只是一个点**：删角色册条目 2026-08-28 起不再拒绝（维护者裁定），
 *  于是这件事上曾经在场/知情的某个人可能已经不在了——这颗点就是那件事
 *  「事后可见可改」的落点。点开才展开成一句人话 + 跳转 + 「知道了」，
 *  不常驻占地方（真书上这一档绝大多数时候不存在）。
 *
 *  **不摆机器词**：卡片上不出现 `event_cast_changed` 或者事件 id，
 *  展开的那句话来自 `title_code`/`title_params` 整句渲染（同
 *  `SystemNotifications.tsx` 的 `noticeBody`），坐标（`jump`）也是后端给的
 *  同一套锚，不另造第二份定位逻辑。 */
function EventCastAlert({
  notification,
  language,
}: {
  notification: SystemNotification;
  language: Language;
}) {
  const { projectId, setHighlight } = useCoords();
  const openChapter = useOpenChapter();
  const resolve = useResolveEventCastChanged(projectId ?? "");
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button
        type="button"
        className="cast-dot"
        onClick={() => setOpen(true)}
        aria-label={
          language === "zh"
            ? "此事件的参与者已变更，点击查看"
            : "The cast of this event changed; click to view"
        }
        title={language === "zh" ? "此事件的参与者已变更" : "The cast of this event changed"}
      />
    );
  }

  const message =
    messageForCode(notification.title_code ?? "", language, notification.title_params ?? {}) ??
    notification.title_code ??
    "";

  const goToQuote = () => {
    if (notification.chapter_number !== null) openChapter(notification.chapter_number);
    if (notification.jump) setHighlight(notification.jump);
  };

  return (
    <div className="cast-alert">
      <span>{message}</span>
      <div className="actions">
        <button type="button" className="link" onClick={goToQuote}>
          {language === "zh" ? "查看原句 →" : "Go to the sentence →"}
        </button>
        <button
          type="button"
          className="link"
          disabled={resolve.isPending}
          onClick={() => resolve.mutate(notification.id)}
        >
          {language === "zh"
            ? resolve.isPending ? "处理中…" : "已阅"
            : resolve.isPending ? "Marking as seen…" : "Mark as seen"}
        </button>
      </div>
    </div>
  );
}

/** 这个人的事件时间线 —— **事件是比较小的一条总结，挂在跟它相关的每个人下面。**
 *
 *  一件事跟三个人相关，这三个人的线上各出现一次（存储那一侧本来就是多对多）。
 *  后端全给 + 每条带章号，**切片是这一层的事**——今天不切，整条摊开。
 *
 *  ── 空态说清楚是哪一种空 ────────────────────────────────────────────────
 *
 *  这一格今天在真书上**必然是空的**：作者那本 158 章的书里事件 0 条，
 *  第一次真抽取跑完才会有。所以空态不许写「暂无数据」——那句话既不告诉作者
 *  发生了什么，也不告诉他下一步。这里分两种说：整本书还没整理过 vs
 *  整理过但这个人身上没落下事。
 *
 *  **超过 `EVENTS_SCROLL_CAP` 条封顶成固定高度 + 滚动**（作者原话：「如果是主角的话，
 *  比如说写了几千张，那就应该做固定高度……然后一个滑轮」）。不足这个数就跟内容
 *  一样高，不留一截空白的滚动区——封顶是为了保护卡片的高度，不是这一格本身的默认样子。 */
const EVENTS_SCROLL_CAP = 6;

function CharacterTimeline({ characterId, name }: { characterId: string; name: string }) {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const events = useCharacterEvents(projectId, characterId);
  const rows = events.data ?? [];

  const rowsEl = rows.map((row) => {
    // 「还有：…」= 这件事上**除他之外**的人。名单去重（一个人可能既在场又知情），
    // 顺序按后端给的来（这一层不发明第二个序）。
    const others = [...row.participants, ...row.knowers]
      .filter((n) => n.id !== characterId)
      .filter((n, i, all) => all.findIndex((m) => m.id === n.id) === i);
    return (
      <div className="item" key={row.event_id}>
        <span className="nm">
          {language === "zh" ? `第 ${row.chapter_number} 章` : `Chapter ${row.chapter_number}`} · {row.summary}
        </span>
        {others.length > 0 && (
          <span className="dim">
            {language === "zh" ? "还有：" : "Also: "}
            {others.map((n) => n.name).join(language === "zh" ? "、" : ", ")}
          </span>
        )}
        {row.cast_changed && (
          <EventCastAlert notification={row.cast_changed} language={language} />
        )}
      </div>
    );
  });

  return (
    <div className="grp">
      <div className="lab">{language === "zh" ? `${name}的事件` : `${name}'s events`}</div>
      {events.isLoading && (
        <span className="empty">{language === "zh" ? "读取中…" : "Loading…"}</span>
      )}
      {!events.isLoading && rows.length === 0 && (
        <span className="empty">
          {language === "zh" ? (
            <>
              尚无与{name}相关的事件。事件在整理正文时记录；本章尚未整理，或整理后没有涉及{name}的事件。
            </>
          ) : (
            <>
              No events involving {name} yet. Events are recorded when the text is processed;
              this chapter has not been processed, or none of its events involve {name}.
            </>
          )}
        </span>
      )}
      {rows.length > EVENTS_SCROLL_CAP ? (
        <div className="char-events-scroll">{rowsEl}</div>
      ) : (
        rowsEl
      )}
    </div>
  );
}

export function RosterTab() {
  const { projectId, selectedNodeId, focusNode, setTab } = useCoords();
  const language = useLanguage((s) => s.language);
  const roster = useRoster(projectId);
  const projects = useProjects();
  // 只为空态读：模型服务没配好的时候，空态的第一句是「先连接模型」（`ModelGuide`）。
  // 同一份缓存（`["settings"]`）设置抽屉和顶栏那盏灯也在读，不多一次请求。
  const ai = useAiSettings();
  const [adding, setAdding] = useState(false);
  const [order, setOrder] = useState<Order>("desc");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  // 「⋯」开在哪一行上（同左栏章目录/书架的 `menuFor`）。改名/删除的入口都藏在
  // 这颗菜单后面——常驻的两颗按钮墙 2026-09-01 换成了云纹描边 + 悬浮才现身的「⋯」。
  const [menuFor, setMenuFor] = useState<string | null>(null);

  const rename = useRenameNode(projectId ?? "");
  const remove = useDeleteNode(projectId ?? "");

  // 点别处关掉「⋯」。不做的话它会一直挂在那儿，而作者以为自己已经点掉了。
  useEffect(() => {
    if (!menuFor) return;
    const close = () => setMenuFor(null);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [menuFor]);

  const rows = useMemo(() => (roster.data ?? []) as RosterEntry[], [roster.data]);
  const groups: Record<string, RosterEntry[]> = {};
  rows.forEach((n) => (groups[n.label] ??= []).push(n));
  const empty = Object.keys(groups).length === 0;

  // 版本从**它正在渲染的那份项目出参**上取（同 `CanonEventCast` 那条注释）：
  // 作者一保存，后台整理那一章、干净结果直接升 CANON，版本就涨了一格。
  const version = projects.data?.find((p) => p.id === projectId)?.canon_version;

  function submitRename(id: string) {
    const name = draft.trim();
    if (!name || version === undefined) return;
    rename.mutate(
      { id, name, expected_canon_version: version },
      { onSuccess: () => setRenaming(null) },
    );
  }

  return (
    <div>
      {/* 两颗都是图标按钮，**悬浮说明用 `data-tip`，不用原生 `title`**——
          原生 title 要等约一秒，作者的原话是「以为没有」，图标按钮不能靠它撑住
          唯一的说明。`aria-label` 是名字，不随状态改；排序那颗按下去会怎样
          （而不是它现在是什么）写在 `data-tip` 里，同顶栏那两颗开关的做法。

          **两颗都带 `tip-right`**：右栏只有 400px 宽，这两颗又贴在 h2 最右边，
          默认居中的气泡（`.icon-btn::after`）右边会探出 `.pane` 的 `overflow: auto`
          之外被裁掉——实测过，`data-tip` 越长越明显（英文那颗「Add a character /
          location / faction…」裁得只剩半句）。同顶栏设置齿轮那颗用 `.tip-right`
          的理由一样：贴右边缘的图标按钮，气泡也得贴右边缘，不能居中。 */}
      <h2>
        {projectId && (
          <button
            className="icon-btn tip-right"
            aria-label={
              language === "zh" ? "建人物 / 地点 / 势力…" : "Add a character / location / faction…"
            }
            data-tip={
              language === "zh" ? "建人物 / 地点 / 势力…" : "Add a character / location / faction…"
            }
            onClick={() => setAdding(true)}
          >
            <PlusIcon />
          </button>
        )}
        {!empty && (
          <button
            className={"icon-btn tip-right" + (order === "asc" ? " on" : "")}
            aria-label={language === "zh" ? "排序方向" : "Sort order"}
            aria-pressed={order === "asc"}
            data-tip={
              language === "zh"
                ? order === "desc" ? "出场少的在前" : "出场多的在前"
                : order === "desc" ? "Sort least-written first" : "Sort most-written first"
            }
            onClick={() => setOrder(order === "desc" ? "asc" : "desc")}
          >
            <SortIcon order={order} />
          </button>
        )}
      </h2>

      {empty ? (
        // **空态要把两条路都说出来**（作者 2026-09-13：「如果添加 api 后，会自己扫描然后
        // 添加角色，应该加个 or」）：手动加一条，或者让分析从正文里整理——后者才是
        // 这一格平时被填满的方式（`upsert_node`），只说「添加第一个条目」等于告诉
        // 作者人物只能手填。分析的入口在「事件」那一格的工具栏上（作者 2026-09-05
        // 定的位置），这儿只指路，不再摆第二颗会花钱的按钮。钥匙没填就先指去填：
        // 没有钥匙那颗按钮按下去只会得到一句「未完成」。
        // 模型服务没配好：**先说怎么连模型**（`ModelGuide`），手动加那条路退成第二句——
        // 作者 2026-09-13：「这边的文字最应该首选是引导用户配置一个模型」。
        ai.data && !ai.data.model_configured ? (
          <div className="empty">
            <ModelGuide what="roster" />
            {language === "zh" ? "也可以" : "You can also "}
            <a onClick={() => projectId && setAdding(true)}>
              {language === "zh" ? "手动添加第一个条目" : "add the first entry by hand"}
            </a>
            {language === "zh" ? "。" : "."}
          </div>
        ) : (
        <div className="empty">
          {language === "zh" ? "尚无人物或设定。" : "No characters or settings yet. "}
          <a onClick={() => projectId && setAdding(true)}>
            {language === "zh" ? "添加第一个条目" : "Add the first entry"}
          </a>
          {language === "zh" ? "，或" : ", or "}
          <a onClick={() => setTab("review")}>
            {language === "zh" ? "在「事件」中分析本章" : "analyze this chapter under “Events”"}
          </a>
          {language === "zh"
            ? "。正文中的人物、地点、势力会自动整理到此处；保存正文后也会自动分析。"
            : ". Characters, locations and factions in the text are added here automatically; saving the text also runs an analysis."}
        </div>
        )
      ) : (
        <>
          {Object.keys(groups)
            .sort()
            .map((lab) => (
              <div className="grp" key={lab}>
                <div className="lab">{nodeLabelText(lab, language)}</div>
                {bySignal(groups[lab], order).map((n) => {
                  // 卡片**只对人物展开**：状态/关系/事件说的都是「这个人怎么样」，
                  // 地点/势力选中之后不再有专属视图（合并的直接后果，见文件顶注）。
                  const expanded = n.id === selectedNodeId && n.label === "Character";
                  return (
                    <div key={n.id}>
                      <div
                        className={
                          "item roster-row" +
                          (n.id === selectedNodeId ? " on" : "") +
                          (expanded ? " open" : "") +
                          (menuFor === n.id ? " menu-open" : "")
                        }
                      >
                        <CloudFrame />
                        {renaming === n.id ? (
                          <input
                            autoFocus
                            value={draft}
                            placeholder={language === "zh" ? "输入新的名称" : "Enter a new name"}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") submitRename(n.id);
                              if (e.key === "Escape") setRenaming(null);
                            }}
                            onBlur={() => setRenaming(null)}
                          />
                        ) : (
                          <>
                            <span
                              className="nm"
                              onClick={() => focusNode(expanded ? null : n.id)}
                              title={
                                language === "zh"
                                  ? expanded ? "收起卡片" : "查看状态、关系和事件"
                                  : expanded ? "Collapse this card" : "View their status, relationships, and events"
                              }
                            >
                              {n.name}
                            </span>
                            {/* 「出现在 N 章的总结里」而不是「出场 N 章」：这一层数的是总结，
                                不是正文。一本还没生成总结的书这一列全是 0，把它写成
                                「没出场」就是拿一个空表当结论（§10 约束 8）。 */}
                            <span
                              className="dim"
                              title={language === "zh" ? "出现在几章的总结里" : "Number of chapter summaries mentioning this"}
                            >
                              {language === "zh"
                                ? `${n.appearance_chapters} 章`
                                : `${n.appearance_chapters} ${n.appearance_chapters === 1 ? "chapter" : "chapters"}`}
                            </span>
                            {/* 「改名/删」原来是两颗常驻描边按钮，2026-09-01 收进这颗悬浮
                                才现身的「⋯」——同左栏章目录/书架那一套机制（`.ch-act`）。
                                改名/删除本身的逻辑一个字没动，只是入口从按钮墙搬进了菜单。 */}
                            <span className="roster-more">
                              {/* **卡片开着的时候，这个位置是「收起」，不是「⋯」**
                                  （作者 2026-09-04）。同一颗 28×28 的按钮换个符号：
                                  展开态最该点的一下就是收起它，而「改名/删」在卡片
                                  开着时本来也不是这一刻要干的事——收起来再点。
                                  它**常驻显形**（`.roster-row.open` 那条 CSS），
                                  不像「⋯」要悬浮才出来：卡片已经摊在屏幕上了，
                                  关它的那颗按钮不该还要作者先去找。 */}
                              {expanded ? (
                                <button
                                  type="button"
                                  className="roster-act"
                                  aria-label={
                                    language === "zh" ? `收起${n.name}的卡片` : `Collapse ${n.name}`
                                  }
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    focusNode(null);
                                  }}
                                >
                                  <CollapseIcon />
                                </button>
                              ) : (
                              <button
                                type="button"
                                className="roster-act"
                                aria-label={
                                  language === "zh" ? `${n.name}的更多操作` : `More actions for ${n.name}`
                                }
                                aria-expanded={menuFor === n.id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setMenuFor(menuFor === n.id ? null : n.id);
                                }}
                              >
                                ⋯
                              </button>
                              )}
                              {menuFor === n.id && (
                                <div className="roster-menu" onPointerDown={(e) => e.stopPropagation()}>
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setDraft(n.name);
                                      setRenaming(n.id);
                                      setMenuFor(null);
                                    }}
                                  >
                                    {language === "zh" ? "改名" : "Rename"}
                                  </button>
                                  <button
                                    type="button"
                                    className="danger"
                                    onClick={() => {
                                      setConfirming(n.id);
                                      setMenuFor(null);
                                    }}
                                  >
                                    {language === "zh" ? "删除" : "Delete"}
                                  </button>
                                </div>
                              )}
                            </span>
                          </>
                        )}

                        {/* **`.roster-row` 是 flex 行**（名字/章数/按钮排成一条）。
                            这一块「确认删除」不该挤进同一条线——CSS 里
                            `.roster-row{flex-wrap:wrap}` + `.roster-row > .row{flex-basis:100%}`
                            让它自己另起一行，不用为此把它挪出 `.item` 单独一个容器
                            （挪出去会撞 `within(row).getByText(...)` 那批用
                            `.closest(".item")` 找这一行的既有测试）。 */}
                        {confirming === n.id && (
                          <div className="row">
                            {/* **删是不可逆的**，所以问一句。2026-08-28 起删除不再因为
                                挂着关系/情节而拒绝——它参与过的事件会跟着一起消失，
                                剩下的人的事件时间线上会冒出红点（`cast_changed`），
                                那才是「事后可见可改」的落点，不在这句确认话里预警。 */}
                            <span>
                              {language === "zh" ? (
                                <>删除「{n.name}」及其全部别名？删除后无法恢复。</>
                              ) : (
                                <>Delete "{n.name}" and all its aliases? This cannot be undone.</>
                              )}
                            </span>
                            <button
                              disabled={remove.isPending || version === undefined}
                              onClick={() =>
                                version !== undefined &&
                                remove.mutate(
                                  { id: n.id, expected_canon_version: version },
                                  { onSuccess: () => setConfirming(null) },
                                )
                              }
                            >
                              {language === "zh"
                                ? remove.isPending ? "删除中…" : "删除"
                                : remove.isPending ? "Deleting…" : "Delete"}
                            </button>
                            <button onClick={() => setConfirming(null)}>
                              {language === "zh" ? "取消" : "Cancel"}
                            </button>
                          </div>
                        )}
                      </div>
                      {/* 原地展开的角色卡：本名/别名 → 状态 → 关系（图 + 文字）→ 依据 → 事件。
                          这个顺序不是随手排的——先说他是谁、再说他现在怎么样、跟谁有关系、
                          这些说法有什么原文撑着，事件时间线最长（超过 6 条会滚动）放最后，
                          翻到底不会先撞见它。 */}
                      {expanded && (
                        <div className="char-card">
                          {/* **收起在那一行右端那颗按钮上**（展开态把「⋯」换掉的那颗），
                              卡片里不再另放一颗 ✕：同一件事两个入口隔着 30px 上下
                              站着，作者 2026-09-04 点名撤掉了下面那个。 */}
                          <CharacterBasicInfo characterId={n.id} />
                          <CharacterStatus characterId={n.id} />
                          <CharacterRelations characterId={n.id} />
                          <CharacterEvidence characterId={n.id} />
                          <CharacterTimeline characterId={n.id} name={n.name} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          <Refusal error={remove.error} />
          <Refusal error={rename.error} />
        </>
      )}

      {adding && projectId && <RosterDrawer pid={projectId} onClose={() => setAdding(false)} />}
    </div>
  );
}
