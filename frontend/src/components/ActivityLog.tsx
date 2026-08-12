import { useState } from "react";
import { useActivity, useActivityDetail, useRuns } from "../api/hooks";
import { useOpenChapter } from "../autopilot";
import { useCoords, type Tab } from "../store";
import { shownTime } from "../time";
import type {
  ActivityCost,
  ActivityEntry,
  ActivityJump,
  ActorTally,
  CostTotals,
  JumpTarget,
} from "../api/types";

// 活动记录（进度板 2.2 / [ADR 0020](docs/adr/0020-clean-extraction-auto-canon.md)）。
//
// 这一页存在的理由只有一条：**系统开始自动往作者的书里写东西了**（干净的抽取结果直接
// 升 CANON，`actor='system'`），而 ADR 0020 押的赌是「错了能改」比「错了先拦住」更合用。
// 那个赌注只有在作者**看得见系统写了什么**的时候才成立——在这一页之前，三张表
// （抽取 / 模型调用 / 确认）一直在写真数据，一条都没露过面。
//
// 三条纪律，破一条这页就退化成装饰：
//
// 1. **跳去哪儿由后端说了算。** 每一行带一个结构化 `jump`（坐标 + 措辞 + endpoints），
//    前端不从 title / subtitle / source 反推——反推 = 第二份路由表 = 两份迟早漂。
// 2. **`jump.endpoints` 为空是一个断言**（「今天没有任何路由能改这个东西」），不是没填。
//    自动升上去的**边**就落在这一档（抽取只产 LOCATED_AT / HAS_STATE / RELATED_TO，
//    而改正层只改 KNOWS↔BELIEVES 和事件名单）。这正是 ADR 0020 自己写下的推翻条件之一
//    ——**这一页把它显式说出来，那个条件才第一次可观测**。编个假按钮 = 把观测点关掉。
// 3. **审计信封不上屏。** `ActivityDetail.payload` 是给机器看的（引擎内部字段一堆），
//    作者要看的那份是 `rows`——措辞全在后端，这里不写一行文案分支。

/** actor → 中文。**开放字符串**（`decision_log.actor` 那一列就是开放的），
 *  认不出的原样显示：明天多一种 actor，宁可露出英文也别静默显示成空白。
 *  措辞跟后端的 `activity.actor_label` 对齐（作者 / 系统），两处说法不一致更糟。 */
const ACTOR_ZH: Record<string, string> = { author: "作者", system: "系统" };
const actorName = (actor: string): string => ACTOR_ZH[actor] ?? actor;

/** 没跑成的那几种态。成功的不写字——一行「完成」乘以几千行就是噪音。 */
const STATUS_ZH: Record<string, string> = {
  failed: "失败",
  running: "进行中",
  pending: "排队中",
};

/** 跳转坐标 → 右栏的哪一格。
 *
 *  **这不是第二份路由表。** 跳去哪个模块是后端算的（`jump.target`，结构化枚举），
 *  这里只把那个枚举翻成「右栏第几个 tab」——tab 是浏览器自己的东西，后端不知道
 *  也不该知道。改这条事实要打哪条路由，全在 `jump.endpoints` 里，前端一个字都不拼。
 *
 *  `chapter` 是 `null`：它是兜底坐标（「只能定位到这一章」），换完章就到位，
 *  右栏停在作者原来看的那一格比替他跳一格更诚实。 */
const TARGET_TAB: Record<JumpTarget, Tab | null> = {
  knowledge_cell: "matrix",
  event_cast: "review",
  proposal: "review",
  chapter: null,
};

/** 跳过去之后，作者今天在**工作台里**真的有得点吗。
 *
 *  **2026-08-11：认知矩阵和事件名单这两档从 `false` 变成了 `true`。** 1.1 落的那两条
 *  改正路由（`/canon/knowledge` / `/canon/events/{id}/cast`）此前在浏览器里没有调用方，
 *  两块面板都只能看；现在矩阵那一格点得开、已确认的情节名单也改得动，所以这一页
 *  不再需要那句「直接改动的入口还没做」——**留着它就从一句诚实话变成一句骗人的话。**
 *
 *  这张表和 `endpoints` 问的不是同一个问题，两个都要看：
 *  `endpoints` = 引擎有没有这条路（后端的知识）；这张表 = 工作台有没有那个控件
 *  （前端自己的知识）。少看一个，作者就会点到一个「跳过去发现改不了」的按钮。
 *  `chapter` 那一档仍然是 `false`，而且**不许因为「反正现在都能改了」顺手改成 true**：
 *  它盖着的正是「自动升上去的位置/状态边今天没有任何编辑入口」那一种。 */
const CAN_EDIT_HERE: Record<JumpTarget, boolean> = {
  knowledge_cell: true,
  event_cast: true,
  proposal: true,
  chapter: false,
};

/** 跳过去之后能不能动手，说清楚。能动手的（矩阵那一格 / 事件名单 / 待确认队列）不啰嗦。
 *
 *  两个条件都要成立才算「能动手」：引擎有那条路（`endpoints` 非空）**且**工作台有那个
 *  控件（`CAN_EDIT_HERE`）。只看后者的话，将来某个目标控件先落地、路由还没有，
 *  作者会点到一个必然报错的按钮。
 *
 *  ⚠️ **空 `endpoints` 今天有两个意思，下面这句话不许只按一个写**
 *  （`activity._decision_jump` 里那段注释 + `test_activity.py::
 *  test_a_row_that_changed_several_events_does_not_read_as_unfixable`）：
 *
 *  ① 真的没有任何路由能改它（自动升上去的位置/状态边——改正层只管
 *     「知道」↔「以为」和事件名单）；
 *  ② 有好几条、后端**不替作者挑是哪一条**（一次升掉一整章的干净事件，是常态不是边角）
 *     ——那几条其实一打就通。
 *
 *  两者在出参形状上长得一模一样（差别只在 `label` 的措辞里，而从措辞反推是这一页
 *  第一条禁令）。所以这句只说**「从这儿点不到某一处」**，绝不说「改不了」——
 *  把 ② 说成没救了，正好否掉 ADR 0020 押的那条退路。 */
function jumpNote(entry: ActivityEntry, jump: ActivityJump): string | null {
  if (CAN_EDIT_HERE[jump.target] && jump.endpoints.length > 0) return null;
  // 只有「改了东西」的那一行值得说这句：一次模型调用本来就没有什么可改的。
  return entry.source === "decision"
    ? "这一步改动的内容，从这里点不到具体的某一处，只能先跳到那一章。"
    : null;
}

/** 数字。**null 是「没记」不是 0**（§10 约束 8）：一张写着 0 的账单是假的。 */
const num = (value: number | null): string => (value === null ? "未记录" : String(value));

/** 用量那一格 —— **「已经知道的那些的合计」和「全部的合计」不是一回事**。
 *
 *  供应商不报 usage 时后端给的是 null（`record_call` 照抄，绝不估算），而 2026-08-12
 *  起草改成可中断之后这一档成了常态：README 教作者填的 DeepSeek 就不报。所以这一格
 *  分三档说三句不同的话，判据是 `metered_calls`（同旁边那格的 `priced_calls`）：
 *
 *  | 报了几次 | 屏幕上 |
 *  |---|---|
 *  | 全报了 | 读入 1200 / 生成 400 token |
 *  | 报了一部分 | 读入 1200 / 生成 400 token（另有 1 次没报，实际更多） |
 *  | 一次都没报 | 用量未记录 |
 *
 *  **「这个数不是全部」必须写在屏幕上，不能只挂在 title 里**：作者要判断的正是这件事，
 *  title 是补一句为什么，不是藏一半真相的地方。 */
function TokenCell({ t }: { t: CostTotals }) {
  const unreported = t.calls - t.metered_calls;
  if (t.metered_calls === 0)
    return (
      <span title="这几次模型服务商都没有回报用量 —— 是这个数拿不到，不是没有用。">
        用量未记录
      </span>
    );
  return (
    <span
      title={
        unreported > 0
          ? `另外 ${unreported} 次模型服务商没有回报用量，所以这里只是其余几次的合计。`
          : undefined
      }
    >
      读入 {num(t.tokens_in)} / 生成 {num(t.tokens_out)} token
      {unreported > 0 ? `（另有 ${unreported} 次没报，实际更多）` : ""}
    </span>
  );
}

/** 这本书到今天为止用掉多少。
 *
 *  **金额今天恒为「未记录」**，而且要说得出为什么：引擎不知道作者和模型服务商谈的价钱
 *  （钥匙是他自己的），所以只记用量不记钱。写成「¥0.00」是本仓库反复在修的那种失败形态。 */
function UsageStrip() {
  const { projectId } = useCoords();
  const runs = useRuns(projectId);
  const data = runs.data;
  if (!data) return null;
  const t = data.totals;
  // 零带着理由：一排「0 次 / 0 token / 花费未记录」看起来像账没记上，
  // 而真相通常只是这本书还没让系统整理过。
  if (data.run_count === 0 && t.calls === 0)
    return <div className="log-usage">还没有用过模型 —— 系统整理过这本书之后，用量会记在这里</div>;
  return (
    <div className="log-usage">
      <span>已整理 {data.run_count} 次</span>
      <span>模型调用 {t.calls} 次</span>
      <TokenCell t={t} />
      <span title="这台电脑上没有记价格：钥匙是你自己的，引擎不知道你和模型服务商谈的是多少钱。">
        花费 {num(t.cost)}
      </span>
    </div>
  );
}

/** 谁做的 —— 作者亲手点的和系统自动干的必须一眼分得开，且能筛。
 *
 *  **计数是全量的，不随过滤变**：它要回答的正是「我筛掉了多少」。ADR 0020 点名过这件事
 *  ——自动升 CANON 开了之后 system 行会长得快得多，作者自己点过的那几十次会被淹掉。 */
function ActorFilter({
  actors,
  actor,
  onPick,
}: {
  actors: ActorTally[];
  actor: string | null;
  onPick: (next: string | null) => void;
}) {
  const total = actors.reduce((n, a) => n + a.count, 0);
  return (
    <div className="log-filters">
      <button aria-pressed={actor === null} className={actor === null ? "on" : ""} onClick={() => onPick(null)}>
        全部 {total}
      </button>
      {actors.map((a) => (
        <button
          key={a.actor}
          aria-pressed={actor === a.actor}
          className={"log-actor-btn " + a.actor + (actor === a.actor ? " on" : "")}
          onClick={() => onPick(a.actor)}
        >
          {actorName(a.actor)}做的 {a.count}
        </button>
      ))}
    </div>
  );
}

function CostLine({ cost }: { cost: ActivityCost }) {
  return (
    <div className="log-cost">
      {cost.model} · 读入 {num(cost.tokens_in)} / 生成 {num(cost.tokens_out)} token · 用时{" "}
      {cost.ms === null ? "未记录" : `${cost.ms} 毫秒`} · 花费 {num(cost.cost)}
    </div>
  );
}

/** 跳到对应模块。**坐标、措辞、能不能改，三样都是后端给的。** */
function JumpRow({ entry, jump }: { entry: ActivityEntry; jump: ActivityJump }) {
  const openChapter = useOpenChapter();
  const jumpFromActivity = useCoords((s) => s.jumpFromActivity);
  const note = jumpNote(entry, jump);

  const go = () => {
    // 换章走全项目唯一那个入口（离开的那一章交后台整理），不裸 setChapter。
    if (jump.chapter_number !== null) openChapter(jump.chapter_number);
    jumpFromActivity({
      tab: TARGET_TAB[jump.target],
      // 高亮哪一格用后端给的两个 id。**不是从 subtitle 里的人名反解出来的**——
      // 名字解析可能歧义，而歧义时服务端的规矩是绝不替作者挑。
      cell:
        jump.character_id && jump.secret_id
          ? { character_id: jump.character_id, secret_id: jump.secret_id }
          : null,
      // 同理：改哪条情节的名单用 `jump.event_id`，不从那行字里认。
      eventId: jump.event_id,
      // 右栏那张表要**多算**上谁 —— 也是后端给的坐标（`jump.cast`），这里只是拼成
      // 后端收的那种写法。矩阵的行由本章正文推（ADR 0018），而日志里那个人可能在
      // 那一章一次都没被点名，那一行就不在表上、点不开也改不了。
      //
      // **它走 `include` 不走 `cast`**：`cast` 是过滤，而这个坐标按定义只知道一个人，
      // 拿它去过滤等于替作者把在场收窄成一个人——`must_not_reveal` 的判据是
      // 「在场的人里至少有一个还不知道」，少一个人就少一批禁令（ADR 0018 §3）。
      // 后端给不出不含歧义的称呼时它是空的，那时就照旧按推导算（和以前一样）。
      include: jump.cast.join("、"),
    });
  };

  return (
    <div className="log-jump">
      <button className="log-go" onClick={go}>
        {jump.label} →
      </button>
      {note && <span className="log-jump-note">{note}</span>}
    </div>
  );
}

/** 展开层：这一步跑了什么、结果是什么、花了多少、去哪儿改。
 *
 *  **`payload` 一个字都不渲染。** 它是审计信封（给机器重放用的），里面是引擎内部字段；
 *  作者要看的那一份后端已经写成 `rows` 了。渲染信封 = 把研发术语摆到作者脸上，
 *  同时把日志页变成一个新的泄漏面。 */
function EntryDetail({ entry }: { entry: ActivityEntry }) {
  const { projectId } = useCoords();
  const detail = useActivityDetail(projectId, entry.id);

  if (detail.isLoading) return <div className="log-detail dim">读取中…</div>;
  if (detail.isError || !detail.data)
    return <div className="log-detail err-box">这一条的详细内容没读出来。</div>;

  const { rows, cost, errors, entry: full } = detail.data;
  return (
    <div className="log-detail">
      {rows.length > 0 && (
        <dl className="log-rows">
          {rows.map((r, i) => (
            <div className="log-rows-line" key={`${r.label}-${i}`}>
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {errors.length > 0 && (
        <div className="err-box">
          {errors.map((e, i) => (
            <div key={i}>{e}</div>
          ))}
        </div>
      )}
      {cost && <CostLine cost={cost} />}
      {full.jump && <JumpRow entry={full} jump={full.jump} />}
    </div>
  );
}

/** 一行一条：折叠的时候只有「谁 · 做了什么 · 结果」，点开才去取详情。 */
function EntryRow({
  entry,
  open,
  onToggle,
}: {
  entry: ActivityEntry;
  open: boolean;
  onToggle: () => void;
}) {
  const time = shownTime(entry.ts);
  return (
    <li className={"log-item" + (open ? " open" : "")}>
      <button className="log-line" aria-expanded={open} onClick={onToggle}>
        <span className="log-fold" aria-hidden="true" />
        <span className={"log-actor " + entry.actor}>{actorName(entry.actor)}</span>
        <span className="log-title">{entry.title}</span>
        <span className="log-sub">{entry.subtitle}</span>
        {entry.status !== "succeeded" && (
          <span className={"log-status " + entry.status}>{STATUS_ZH[entry.status] ?? entry.status}</span>
        )}
        {time && <span className="log-time">{time}</span>}
      </button>
      {open && <EntryDetail entry={entry} />}
    </li>
  );
}

/** 活动记录页 —— 占中栏（左栏书架和右栏面板照旧留在原地，跳转过去就是那一栏在动）。 */
export function ActivityLog() {
  const { projectId } = useCoords();
  const [actor, setActor] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const log = useActivity(projectId, actor);

  const pages = log.data?.pages ?? [];
  const entries = pages.flatMap((p) => p.entries);
  // actors 取第一页那份就够：它是全量计数，翻页不会变（变了反而说明后端在骗人）。
  const actors = pages[0]?.actors ?? [];

  return (
    <section className="pane log">
      <div className="log-head">
        <h2>活动记录</h2>
        <p className="log-lead">
          这本书里发生过的每一步：系统自己整理的，和你亲手确认的。点开看它到底做了什么。
        </p>
        <UsageStrip />
        <ActorFilter
          actors={actors}
          actor={actor}
          onPick={(next) => {
            setActor(next);
            setOpenId(null);
          }}
        />
      </div>

      {log.isLoading && <div className="empty">读取中…</div>}
      {log.isError && <div className="err-box">活动记录没读出来。</div>}
      {!log.isLoading && !log.isError && entries.length === 0 && (
        <div className="empty">
          {actor === null
            ? "还没有留下记录。系统整理过这本书之后，它做的每一步都会出现在这里。"
            : `${actorName(actor)}还没有在这本书上留下记录。`}
        </div>
      )}

      <ul className="log-list">
        {entries.map((e) => (
          <EntryRow
            key={e.id}
            entry={e}
            open={openId === e.id}
            onToggle={() => setOpenId(openId === e.id ? null : e.id)}
          />
        ))}
      </ul>

      {log.hasNextPage && (
        <button className="log-more" disabled={log.isFetchingNextPage} onClick={() => log.fetchNextPage()}>
          {log.isFetchingNextPage ? "读取中…" : "看更早的"}
        </button>
      )}
    </section>
  );
}
