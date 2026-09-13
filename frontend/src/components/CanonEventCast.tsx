import { useEffect, useMemo, useRef, useState } from "react";
import {
  useCorrectEventCast,
  useEvents,
  useExtractionRun,
  useProjects,
  useRoster,
  useStartExtraction,
} from "../api/hooks";
import { ApiError } from "../api/client";
import { readCorrectionError, saidToTheAuthor } from "../correctionError";
import type { EventView, ExtractionRun, NodeRef } from "../api/types";
import { useLanguage, type Language } from "../language";
import { useCoords } from "../store";
import { CastPicker, DIMENSIONS, candidates, idsOf, same } from "./CastPicker";
import { ScanIcon, SearchIcon, SortIcon } from "./icons";
import type { Dimension } from "./CastPicker";

// 已经生效的情节：谁在场、谁知道了 —— 以及**改它**（`POST /canon/events/{id}/cast`）。
//
// ── 为什么在「待确认」这一格里 ────────────────────────────────────────────
//
// 1. 后端的 `jump.target = event_cast` 早就指向这一格（`TARGET_TAB` 里那张表）。
//    另开一个 tab 就得改那张表，而它是前端唯一一处「跳去哪儿」的判断——为一个
//    新面板改它，等于多一份会漂的东西。
// 2. 同一章的「还没确认的情节」和「已经确认的情节」并排，作者一眼看得出确认前后
//    的差别；分到两格去，他得记住自己刚才确认了什么。
// 3. 三栏布局一个像素不动（顶栏 + 左 210 + 中 flex + 右 400 + 底栏）。
//
// ── 名单是**绝对集合**，UI 必须把这件事说出来 ────────────────────────────
//
// `knower_ids` / `participant_ids` 传谁就是谁，`null` = 这一维不动。控件和那句说明
// 在 `CastPicker.tsx` —— **提案那一格的「改一改再收下」用的是同一份**（同一件事的
// 前一步：那条还没生效）。只发作者真动过的那一维——把没动过的名单也发过去，
// 日志里就会多出一条「改了在场」而其实一个人都没变，而 `decision_log` 是只增不改的。

/** 「分析本章」——**这一栏工具栏上的第三颗图标**（作者 2026-09-05 定的位置：
 *  排序和放大镜中间）。它从「通知」那一格搬过来，那儿原来是一颗写着字的按钮
 *  加一句常驻的「分析：已完成 · 发现 7 条情节」。
 *
 *  搬家顺带改了两件事，都照这一栏已经立过的规矩：
 *  1. **图标 + 悬浮**，不写字（同排序 / 放大镜那两颗，`data-tip` 画名字，
 *     不用原生 `title`——那个要等约一秒，作者说「以为没有」）；
 *  2. **结果是一句飘一下就走的话**，不常驻（同「检验规则」那颗闪电，作者
 *     2026-09-05：「你这个文字不要一直留在这边过几秒消失」）。留得住的那一份
 *     本来就在这一栏的列表里（分析出来的情节）和「通知」那一格（待确认的提案）。
 *
 *  三态跟着那颗闪电的口径：**灰 = 没跑过 · 金黄 = 正在跑 · 绿 = 这一版正文跑过了**。
 *  绿不按秒退——「这一章分析过没有」是关于这一版正文的事实，不是动画；换章就回灰。 */
function AnalyzeButton() {
  const { projectId, chapter } = useCoords();
  const language = useLanguage((s) => s.language);
  const start = useStartExtraction(projectId!, chapter);
  const [runId, setRunId] = useState<string | null>(null);
  const run = useExtractionRun(projectId, runId);
  const [toast, setToast] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  // 换章 = 换一件事：上一章那次的运行号和那句话都不该跟着过来。
  useEffect(() => {
    setRunId(null);
    setToast(null);
  }, [chapter, projectId]);
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  const say = (line: string) => {
    setToast(line);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), 4200);
  };

  // 跑完（成功或失败）说一句就走。**失败也要说**——一次没有任何反馈的失败，
  // 在这块屏幕上和「它还在想」长得一模一样（§10 约束 8）。
  const status = run.data?.status;
  const said = useRef<string | null>(null);
  useEffect(() => {
    if (!run.data || !status || status === "PENDING" || status === "RUNNING") return;
    if (said.current === run.data.id) return;
    said.current = run.data.id;
    say(analysisToast(run.data, language));
  }, [run.data, status, language]);

  const busy = start.isPending || status === "PENDING" || status === "RUNNING";
  const done = status === "SUCCEEDED";
  const phase = busy ? "busy" : done ? "done" : "idle";

  return (
    <>
      <button
        type="button"
        className={`icon-btn scan-run ${phase}`}
        aria-label={language === "zh" ? "分析本章" : "Analyze this chapter"}
        data-tip={
          busy
            ? language === "zh" ? "分析中…" : "Analyzing…"
            : language === "zh" ? "分析本章" : "Analyze this chapter"
        }
        disabled={!projectId || busy}
        onClick={() => {
          if (!projectId) return;
          start.mutate(undefined, {
            onSuccess: (r) => setRunId(r.id),
            // **起不了步也要说一句**（作者 2026-09-13：「点了也没有反应，画面就闪一下，
            // 我都不知道现在是成功了还是失败了」）——那次是后端 500，按钮只从灰变
            // 金黄再变回灰。跑起来之后失败有上面那条 effect 说话，起步就失败这儿说。
            onError: (error) => say(startFailure(error, language)),
          });
        }}
      >
        <ScanIcon />
      </button>
      {toast && <div className="check-toast">{toast}</div>}
    </>
  );
}

/** 「分析本章」连 run 都没建出来（POST 本身失败）时说什么。后端的拒绝（4xx，带码或带
 *  作者读的那句话）走 `saidToTheAuthor`（同这份文件其余每一格）；5xx 一律落到通用那句——
 *  FastAPI 的 500 正文是 `Internal Server Error`，那是写给维护者的，不上屏；断网同理。 */
function startFailure(error: unknown, language: Language): string {
  if (error instanceof ApiError && error.status < 500) {
    const said = saidToTheAuthor(error);
    if (said) return said;
  }
  return language === "zh"
    ? "本章分析未能开始。请稍后重试。"
    : "Analysis of this chapter could not start. Try again shortly.";
}

/** 跑完那一句。**整句拼装，不是拼片段**：英文那半的两个附加小句各自要处理单复数。 */
function analysisToast(run: ExtractionRun, language: Language): string {
  if (run.status !== "SUCCEEDED") {
    return language === "zh"
      ? "本章分析未完成，内容未更改。请稍后重试。"
      : "Analysis of this chapter did not complete and nothing was changed. Try again shortly."
  }
  if (language === "zh") {
    if (run.valid_event_count === 0) return "本章未整理出新的情节";
    let line = `整理出 ${run.valid_event_count} 条情节`;
    if (run.proposal_count > 0) line += `，其中 ${run.proposal_count} 项待确认，已列入「通知」`;
    return line;
  }
  if (run.valid_event_count === 0) return "No new events found in this chapter";
  const events = `${run.valid_event_count} event${run.valid_event_count === 1 ? "" : "s"}`;
  if (run.proposal_count === 0) return `Found ${events}`;
  const items = `${run.proposal_count} item${run.proposal_count === 1 ? "" : "s"}`;
  return `Found ${events}; ${items} need your confirmation — see Notices`;
}

function CastEditor({
  view,
  people,
  canonVersion,
  onDone,
  onStale,
}: {
  view: EventView;
  people: NodeRef[];
  canonVersion: number;
  onDone: () => void;
  onStale: () => void;
}) {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const correct = useCorrectEventCast(projectId!);
  const [picked, setPicked] = useState<Record<Dimension, string[]>>({
    knowers: idsOf(view.knowers),
    participants: idsOf(view.participants),
  });

  // 后端把名单改了 → 勾选状态跟着走。不跟的话，界面上摆着的是一份**旧名单**，
  // 而它下一次会被原样发回去。
  //
  // **依赖是名单本身的指纹，不是 `view` 这个对象**：react-query 每次重取都给一个新对象
  // （切回窗口就会重取一次），照对象比的话，作者勾到一半会被一次内容完全相同的重取清空。
  const signature = `${idsOf(view.participants).join(",")}|${idsOf(view.knowers).join(",")}`;
  useEffect(() => {
    setPicked({ knowers: idsOf(view.knowers), participants: idsOf(view.participants) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  const changed: Dimension[] = DIMENSIONS.filter((dim) =>
    dim === "knowers"
      ? !same(picked.knowers, idsOf(view.knowers))
      : !same(picked.participants, idsOf(view.participants)),
  );
  const failure = correct.error ? readCorrectionError(correct.error) : null;

  const toggle = (dim: Dimension, id: string) =>
    setPicked((prev) => ({
      ...prev,
      [dim]: prev[dim].includes(id) ? prev[dim].filter((x) => x !== id) : [...prev[dim], id],
    }));

  const submit = () => {
    if (!projectId || changed.length === 0) return;
    correct.mutate(
      {
        eventId: view.event.id,
        // `null` = 这一维不动。没动过的那一维发过去，日志里就多一条什么都没改的记录。
        knower_ids: changed.includes("knowers") ? picked.knowers : null,
        participant_ids: changed.includes("participants") ? picked.participants : null,
        expected_canon_version: canonVersion,
      },
      { onSuccess: onDone },
    );
  };

  return (
    <div className="cast-editor">
      <CastPicker people={people} picked={picked} onToggle={toggle} />

      {failure && (
        <div className="err-box">
          <div>{failure.message}</div>
          {failure.kind === "stale" && (
            <button className="link" onClick={onStale}>
              {language === "zh" ? "查看最新版本" : "See the latest version"}
            </button>
          )}
        </div>
      )}

      <div className="actions">
        <button disabled={changed.length === 0 || correct.isPending} onClick={submit}>
          {language === "zh"
            ? correct.isPending ? "保存中…" : "保存名单"
            : correct.isPending ? "Saving…" : "Save list"}
        </button>
        <button className="link" onClick={onDone}>
          {language === "zh" ? "收起" : "Dismiss"}
        </button>
      </div>
    </div>
  );
}

/** 本章已经生效的情节 + 改它们的名单。日志页跳过来时自动展开那一条。 */
export function CanonEventCast({ canonVersion }: { canonVersion: number }) {
  const { projectId, chapter, focusEventId } = useCoords();
  const language = useLanguage((s) => s.language);
  const events = useEvents(projectId, chapter, "CANON");
  const roster = useRoster(projectId);
  // **版本住在这个读端上，不在名单里**：`canonVersion` 是调用方从 `GET /api/projects`
  // 的 `canon_version` 上取的（事件出参里没有它——`tests/test_canon_edit_loop.py::
  // test_the_event_cast_editor_has_no_such_carrier` 钉了这条事实）。
  //
  // 所以撞上「别处刚改过」时只重取 `/events` 等于**什么都没刷新**：作者再按一次保存，
  // 发出去的还是同一个旧版本号，还是 409，点几次都出不去。这里 `useProjects()` 和
  // 调用方共用同一份缓存（queryKey 相同），`refetch()` 刷的就是它读的那一个——
  // 这不是第二个读源，是让那唯一一个读源重读一遍。
  //
  // ⚠️ 这条路在这个产品里是常态不是边角：作者一保存，后台就去整理那一章
  //（`api/app.py::_trigger_refresh`），干净的抽取结果直接升 CANON（ADR 0020），
  // 版本就涨了一格，而浏览器里那份缓存一个字都没变。
  const projects = useProjects();
  const [openId, setOpenId] = useState<string | null>(null);

  // 从活动记录跳过来：后端给的是 `jump.event_id`，前端不从那行字里认哪条事件。
  useEffect(() => {
    if (focusEventId) setOpenId(focusEventId);
  }, [focusEventId]);

  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const [finding, setFinding] = useState(false);
  const [needle, setNeedle] = useState("");
  const all = events.data ?? [];
  /* 按章分组 + 排序 + 按章号筛。**分组键是 `event.chapter_number`**，不是列表顺序：
     后端按 (chapter, id) 出，但排序切成倒序之后就不能再靠「相邻两条章号相同」认组了。
     筛的是**章号前缀**（打「15」既中第 15 章也中第 158 章）——作者要的是「直接跳到
     那一章」，一位一位打进去的过程中列表就在收窄，比打完再回车少一步。 */
  const groups = useMemo(() => {
    const wanted = needle.trim();
    const kept = wanted
      ? all.filter((v) => String(v.event.chapter_number).startsWith(wanted))
      : all;
    const byChapter = new Map<number, typeof kept>();
    for (const view of kept) {
      const list = byChapter.get(view.event.chapter_number) ?? [];
      list.push(view);
      byChapter.set(view.event.chapter_number, list);
    }
    return [...byChapter.entries()].sort((a, b) =>
      order === "desc" ? b[0] - a[0] : a[0] - b[0],
    );
  }, [all, needle, order]);
  const views = all;
  // 角色册出参的 label 是开放字符串（后端 `_narrow` 之后的 dict），这里只按
  // `label === "Character"` 过滤——名单两维收的都是人物，别的 label 后端会拒。
  const roll = useMemo(() => (roster.data ?? []) as NodeRef[], [roster.data]);

  // 跳过来了，但这一章里没有那一条。**「这一章是空的」不能替它说话**：作者要知道的是
  // 「我刚才点的那条去哪了」，而不是「这里什么都没有」（§10 约束 8）。
  const missing =
    !!focusEventId && !events.isLoading && !views.some((v) => v.event.id === focusEventId);

  return (
    <div className="mnr">
      {/* ⚠️ **那行标题 2026-09-05 删了**（作者点名）。范围这件事没有丢，换了地方说：
          每一章的第一条上面有一行黑体的「第 N 章」，所以「这一屏跨了哪些章」是**看得见
          的**，不用一句话去交代。这一格读的仍然是 `TEMPORAL_WHERE`
          （`valid_from_chapter <= 当前章`），不是「这一章发生的」。 */}
      <div className="ev-bar">
        <span className="lab">{language === "zh" ? "每章事件概括" : "Events by chapter"}</span>
        {/* 两句概括：这一栏一共多少条、跨了几章。**数的是筛完之后的**——放大镜里打了
            章号，这两个数跟着缩，否则它说的是另一份单子。 */}
        {groups.length > 0 && (
          <span className="ev-count">
            {language === "zh"
              ? `共 ${groups.reduce((n, [, list]) => n + list.length, 0)} 条 · ${groups.length} 章`
              : `${groups.reduce((n, [, list]) => n + list.length, 0)} in ${groups.length} chapters`}
          </span>
        )}
        <button
          type="button"
          className="icon-btn"
          /* ⚠️ 这一颗的说法**作者定死了：正序 / 倒序**。两版被否掉的都记在这儿，
             省得下一个人再绕一圈：「从早到晚」——这块屏幕上「早晚」读成时间，而它排的
             是章号；「新章在前」——多一层解释，作者要的是排序本身那两个词。 */
          aria-label={
            language === "zh"
              ? order === "desc" ? "改成正序" : "改成倒序"
              : order === "desc" ? "Sort ascending" : "Sort descending"
          }
          /* **说的是「点下去会变成什么」，不是「现在是什么」**（作者 2026-09-05：
             「如果他原本就是正序的话，那这个提示词应该就是倒序」）——一颗按钮的说明
             是它的动作，不是它的状态；状态已经画在图标的箭头上了。 */
          data-tip={
            language === "zh"
              ? order === "desc" ? "正序" : "倒序"
              : order === "desc" ? "Ascending" : "Descending"
          }
          onClick={() => setOrder(order === "desc" ? "asc" : "desc")}
        >
          <SortIcon order={order} />
        </button>
        <AnalyzeButton />
        <button
          type="button"
          className="icon-btn tip-right"
          aria-label={language === "zh" ? "按章号找" : "Find by chapter"}
          data-tip={language === "zh" ? "按章号找" : "Find by chapter"}
          aria-expanded={finding}
          onClick={() => {
            setFinding(!finding);
            if (finding) setNeedle("");
          }}
        >
          <SearchIcon />
        </button>
        {finding && (
          <input
            className="ev-find"
            autoFocus
            inputMode="numeric"
            value={needle}
            onChange={(e) => setNeedle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setNeedle("");
                setFinding(false);
              }
            }}
            placeholder={language === "zh" ? "章号" : "Chapter"}
          />
        )}
      </div>

      {events.isLoading && (
        <span className="empty">{language === "zh" ? "读取中…" : "Loading…"}</span>
      )}
      {/* 空态把「怎么填满」说出来（作者 2026-09-13：每一格都该有这一句）：情节是
          「分析本章」从正文里整理出来的，保存正文后也会自动分析——不说这一句，作者
          对着一张空单子只知道「还没有」，不知道它从哪儿来。 */}
      {!events.isLoading && views.length === 0 && !missing && (
        <span className="empty">
          {language === "zh"
            ? `截至第 ${chapter} 章尚无已确认的情节。点击上方「分析本章」从正文整理（保存正文后也会自动分析），确认后的情节在此显示，参与者可随时修改。`
            : `No confirmed events through chapter ${chapter} yet. Click “Analyze this chapter” above to collect them from the text (saving the text also runs an analysis); confirmed events appear here, and their cast can be changed at any time.`}
        </span>
      )}
      {/* 跳过来却找不到那一条：**说出来**，不要安静地摆一张看起来正常的单子。
          它可能已经被撤回，或者压根不在这一章。 */}
      {missing && (
        <div className="warn">
          {language === "zh"
            ? "未找到该情节，可能已被修改或撤回。"
            : "That event was not found; it may have been changed or retracted."}
        </div>
      )}

      {finding && needle.trim() && groups.length === 0 && (
        <span className="empty">
          {language === "zh" ? `第 ${needle.trim()} 章没有情节` : "No events in that chapter"}
        </span>
      )}

      {groups.map(([number, list]) => (
        <div className="ev-group" key={number}>
          {/* **章号在卡片外面、黑体**（作者 2026-09-05）：它是这一堆卡片的抬头，
              不是其中一张卡片的一行字。 */}
          <div className="ev-chapter">
            {language === "zh" ? `第 ${number} 章` : `Chapter ${number}`}
            <span className="ev-count">
              {language === "zh" ? `${list.length} 条` : `${list.length}`}
            </span>
          </div>
          {list.map((view) => {
        const open = openId === view.event.id;
        return (
          <div
            className={"event-card canon" + (focusEventId === view.event.id ? " focus" : "")}
            key={view.event.id}
          >
            <div className="event-head">
              <button
                className="link"
                aria-expanded={open}
                onClick={() => setOpenId(open ? null : view.event.id)}
              >
                {view.event.summary}
              </button>
              <div className="row dim">
                {language === "zh" ? (
                  <>
                    在场：{view.participants.map((n) => n.name).join("、") || "—"} · 知道这件事的：
                    {view.knowers.map((n) => n.name).join("、") || "—"}
                  </>
                ) : (
                  <>
                    Present: {view.participants.map((n) => n.name).join(", ") || "—"} · Knew about
                    it: {view.knowers.map((n) => n.name).join(", ") || "—"}
                  </>
                )}
              </div>
            </div>
            {open && (
              <CastEditor
                view={view}
                people={candidates(roll, view)}
                canonVersion={canonVersion}
                onDone={() => setOpenId(null)}
                onStale={() => {
                  setOpenId(null);
                  // 顺序无所谓，缺一不可：版本在 projects 上，名单在 events 上。
                  projects.refetch();
                  events.refetch();
                }}
              />
            )}
          </div>
        );
          })}
        </div>
      ))}
    </div>
  );
}
