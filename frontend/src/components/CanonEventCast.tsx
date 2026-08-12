import { useEffect, useMemo, useState } from "react";
import { useCorrectEventCast, useEvents, useProjects, useRoster } from "../api/hooks";
import { readCorrectionError } from "../correctionError";
import type { EventView, NodeRef } from "../api/types";
import { useCoords } from "../store";

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
// `knower_ids` / `participant_ids` 传谁就是谁，`null` = 这一维不动。所以这里用勾选框
// 而不是「＋加一个人」：勾上的那些就是**改完之后的名单**，再点一次保存不会把人加两遍。
// 只发作者真动过的那一维——把没动过的名单也发过去，日志里就会多出一条
// 「改了在场」而其实一个人都没变，而 `decision_log` 是只增不改的。

/** 两维名单，措辞是作者的。**「知情」这一维是抽取里唯一靠推断得来的**（谁在场是文本里
 *  写着的，谁**因此知道了**是猜的），所以它也是最需要改的一维（ADR 0020 的代价那节）。 */
const DIMENSION = {
  knowers: "知道这件事的人",
  participants: "在场的人",
} as const;

type Dimension = keyof typeof DIMENSION;

const idsOf = (refs: NodeRef[]): string[] => refs.map((r) => r.id);
const same = (a: string[], b: string[]): boolean =>
  a.length === b.length && a.every((id) => b.includes(id));

/** 候选人 = 花名册里的人物 ∪ 这条情节上现有的名单。
 *
 *  并集那一半不是防御性编程：名单里出现一个花名册没有的人时，只按花名册画会让他
 *  **在界面上凭空消失**，而作者一按保存就把他从这条情节上删掉了——一次他没打算做的删除。 */
function candidates(roster: NodeRef[], view: EventView): NodeRef[] {
  const seen = new Map<string, NodeRef>();
  for (const n of roster) if (n.label === "Character") seen.set(n.id, n);
  for (const n of [...view.participants, ...view.knowers]) if (!seen.has(n.id)) seen.set(n.id, n);
  return [...seen.values()];
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

  const changed: Dimension[] = (["participants", "knowers"] as Dimension[]).filter((dim) =>
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
      {(["participants", "knowers"] as Dimension[]).map((dim) => (
        <fieldset className="cast-dim" key={dim}>
          <legend>{DIMENSION[dim]}</legend>
          {people.length === 0 ? (
            <span className="empty">花名册里还没有人物 —— 先去「花名册」那一格加人。</span>
          ) : (
            people.map((p) => (
              <label className="cast-pick" key={p.id}>
                <input
                  type="checkbox"
                  checked={picked[dim].includes(p.id)}
                  onChange={() => toggle(dim, p.id)}
                />
                <span>{p.name}</span>
              </label>
            ))
          )}
        </fieldset>
      ))}

      <div className="row dim">
        勾上的就是改完之后的名单 —— 再保存一次不会把同一个人加两遍。
      </div>

      {failure && (
        <div className="err-box">
          <div>{failure.message}</div>
          {failure.kind === "stale" && (
            <button className="link" onClick={onStale}>
              看看最新的
            </button>
          )}
        </div>
      )}

      <div className="actions">
        <button disabled={changed.length === 0 || correct.isPending} onClick={submit}>
          {correct.isPending ? "保存中…" : "保存名单"}
        </button>
        <button className="link" onClick={onDone}>
          收起
        </button>
      </div>
    </div>
  );
}

/** 本章已经生效的情节 + 改它们的名单。日志页跳过来时自动展开那一条。 */
export function CanonEventCast({ canonVersion }: { canonVersion: number }) {
  const { projectId, chapter, focusEventId } = useCoords();
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
  // ⚠️ 这条路在这个产品里是常态不是边角：作者一离开某一章，后台就去整理那一章
  //（`autopilot.ts`），干净的抽取结果直接升 CANON（ADR 0020），版本就涨了一格，
  // 而浏览器里那份缓存一个字都没变。
  const projects = useProjects();
  const [openId, setOpenId] = useState<string | null>(null);

  // 从活动记录跳过来：后端给的是 `jump.event_id`，前端不从那行字里认哪条事件。
  useEffect(() => {
    if (focusEventId) setOpenId(focusEventId);
  }, [focusEventId]);

  const views = events.data ?? [];
  // 花名册出参的 label 是开放字符串（后端 `_narrow` 之后的 dict），这里只按
  // `label === "Character"` 过滤——名单两维收的都是人物，别的 label 后端会拒。
  const roll = useMemo(() => (roster.data ?? []) as NodeRef[], [roster.data]);

  // 跳过来了，但这一章里没有那一条。**「这一章是空的」不能替它说话**：作者要知道的是
  // 「我刚才点的那条去哪了」，而不是「这里什么都没有」（§10 约束 8）。
  const missing =
    !!focusEventId && !events.isLoading && !views.some((v) => v.event.id === focusEventId);

  return (
    <div className="mnr">
      <div className="lab">已确认的情节（谁在场、谁知道了）</div>

      {events.isLoading && <span className="empty">读取中…</span>}
      {!events.isLoading && views.length === 0 && !missing && (
        <span className="empty">
          这一章还没有已确认的情节。确认过的情节会出现在这里，之后随时能改名单。
        </span>
      )}
      {/* 跳过来却找不到那一条：**说出来**，不要安静地摆一张看起来正常的单子。
          它可能已经被撤回，或者压根不在这一章。 */}
      {missing && (
        <div className="warn">没有在这一章找到刚才那条情节，它可能已经被改掉或撤回了。</div>
      )}

      {views.map((view) => {
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
                在场：{view.participants.map((n) => n.name).join("、") || "—"} · 知道这件事的：
                {view.knowers.map((n) => n.name).join("、") || "—"}
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
  );
}
