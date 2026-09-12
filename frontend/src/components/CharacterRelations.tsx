import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Edge as RFEdge,
  type Node as RFNode,
  type NodeChange,
  type NodeProps,
  type NodeTypes,
  type XYPosition,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useSubgraph } from "../api/hooks";
import { edgeLabelText } from "../backendMessages";
import { useLanguage, type Language } from "../language";
import { useCoords } from "../store";
import type { Subgraph } from "../api/types";

// 角色卡里的「关系」——**只有一张图，图上只有人**（2026-09-04 重做）。
//
// ── 这一格原来是什么样，为什么全拆了 ────────────────────────────────────────
//
// 原来是「一份文字清单 + 一张图」并列：清单是「裕王 — 仇敌 · 第 155 章起」三行，
// 图上除了人还画着地点（观音寺）和状态维度（装备），每条边上还写着「关系 · 第 155
// 章起」。作者原话：**「稍微设计有点问题，应该更精简一点」**，于是三件事一起做：
//
// 1. **文字清单整块删掉。** 同一件事在同一屏上说两遍（清单一遍、边上一遍），
//    而清单还比图先出现——精简的第一刀就是它。
// 2. **图上只留人物。** 地点被点名了：「我不知道你这个地点是什么意思，是表示他当前
//    地点还是什么？状态那边就可以体现了」——**当前所在地确实在「状态」那一格**
//    （`CharacterStatus`），在关系图里再画一遍只会让人以为那是另一件事。
//    作者留了个口子：「除非是两个人物之间的认识地点，这倒还有点东西」——那需要边上
//    带地点，今天的 `RELATED_TO` 没有这个字段，所以不做，**别顺手把地点节点加回来
//    当替代品**。
// 3. **边上不写字，改成点一下人物冒一个黑底白字的气泡。** 边标签在 260px 高的画布上
//    互相压着，而「两个人之间是什么关系」本来就是一次只问一个的问题。
//
// ── 气泡为什么长在节点里，而不是画布外面 ────────────────────────────────────
//
// 它是自定义节点（`PersonNode`）里一个绝对定位的子元素，所以**跟着画布的平移缩放
// 一起动**——摆在外面就得自己把流坐标换算成屏幕坐标，一拖一缩就飘。黑底白字用的是
// 工作台通用的那两个色（`--ink` / `--bg`，同 `[data-tip]` 那套气泡），暗色模式下自动
// 反过来，不写死 `#000`。
//
// **不是 `.icon-btn::after` 那套机制**（styles.css 里那份注释写着「全仓库只此一份，
// 再抄一遍就是第二份会漂的实现」）：那一套是 hover + `attr(data-tip)`，而这里的触发
// 是点击、内容来自 state、还得跟着画布变换——共用不了，只共用颜色。
//
// ── 2026-09-04（二）：铺满画布，按人数定大小 ────────────────────────────────
//
// 作者原话：**「这个节点大小能不能智能一点，按量少然后能展示的那种来展示它的大小，
// 现在感觉又不密，然后你又很小」**——右栏一宽，画布变成 1200×260 这种细长条，
// 而上一版把人**摆在一个正圆上**（R=150）：正方形的东西塞进细长的框，`fitView`
// 只能按**高**去缩（260 / 300 ≈ 0.72），于是横向空掉一大片、字还被缩到看不清。
// 两处都是同一个病的两面，所以一起治：
//
// 1. **圈的形状跟着画布的形状。** 量出这块画布的真实宽高（`ResizeObserver`），
//    椭圆的两个半轴各按宽高算——画布宽就摊得宽，缩窄了自己收回来。
// 2. **框的大小跟着人数。** 三五个人时字号/内边距放大一档，人多了收回去：
//    同一块地方，人少就该每个都看得清，人多就该都放得下。
//
// `fitViewOptions.maxZoom` 卡在 1：布局本身已经按像素铺满了，再让 `fitView` 放大
// 只会把 1px 的描边和字一起拉毛。它剩下的活只有「居中 + 万一放不下就缩」。
//
// ── 2026-09-04（三）：能拖动了 ──────────────────────────────────────────────
//
// 作者原话：「我发现我这个是手型的时候无法拖动这个节点，要不要在左边添加一个鼠标和
// 手型之间的切换？」——**光标是对的，拖不动的原因在别处**：ReactFlow 的 `nodes` 是
// 受控 prop，不接 `onNodesChange` 的话，拖拽产生的位移没人应用，节点当场弹回原位
// （光标却一直是 `grab`，看着像坏了）。所以这一版接上位移，**不加模式切换**：
// 按在人身上就拖那个人、按在空白上就拖整张图，这两件事本来就靠「按在哪」分开，
// 再加一个模式开关只会让作者多记一件事。
//
// 手动挪过的位置存在 `dragged` 里**盖在算出来的布局之上**，不是直接改布局：
// 点一个人（冒气泡）会让 `toFlow` 重算一遍，要是把位置也一起重算，作者刚摆好的图
// 会在他点下一个人的瞬间弹回去。**只有子图本身变了、或者画布尺寸变了才清空**——
// 那时算出来的位置全是新的，留着旧的反而错位。
//
// 点节点**不再切换中心**了（原来是 `focusNode(n.id)`）：一次点击不能既是「看关系」
// 又是「换人」。换人走上面那份角色册列表，那儿本来就是干这个的。

/** 这张图**只画和中心直接有关系的人**（2026-09-10 从 2 改成 1，作者点名）。
 *
 *  ── 两跳是怎么变成毛病的 ──────────────────────────────────────────────
 *  两跳会顺带画进「关系人的关系人」，他们和中心之间**本来就没有边**，悬浮上去只能
 *  说一句「和他之间没有直接记录的关系」。这件事一直存在，只是以前撞不上：
 *  `MAX_SUBGRAPH_NODES` 还是 30 的时候，第一跳的人就把名额占满了，第二跳根本轮不上。
 *  2026-09-09 那个上限提到 1000，它们才浮出来——真书上贾环第 158 章是
 *  **165 个直接关系 + 16 个只能说「没有直接关系」的**，而两种节点长得一模一样，
 *  得逐个悬浮才分得清。
 *
 *  作者的裁定是「没有直接关系的不用补到关系图谱中」，所以砍的是那一跳本身，
 *  不是给它换个画法。**要改回 2 的话，`relationLines` 里那条「没有直接边」的分支
 *  得一起加回来**——它随这次改动删了（本仓库不留没有消费者的代码）。 */
const HOPS = 1;

/** 画布尺寸的兜底值（jsdom 里没有 `ResizeObserver`、也量不出布局）。 */
const FALLBACK_BOX = { w: 640, h: 260 } as const;

/* 圈画多大（2026-09-04 二改）。
   **上一版按「画布有多宽」摊到边**，作者当场否了：「你这个节点隔这么远，就是这个
   线条这么远，你至于吗？特别是右边那一个，感觉隔得好远。」——1200px 宽的画布上四个
   人被推到最外沿，中间是四根很长的细线。
   所以圈的大小改成按「**这些人需要多大地方**」算（周长 ≈ 人数 × 间距），画布只决定
   它**扁到什么程度**；铺满画布的活交给 `fitView` 去放大——放大时框和线一起变大，
   「线相对框有多长」不变，所以既不小也不空。 */
const NEIGHBOR_GAP = 140;
/** 扁平度只跟着画布在 [0.5, 3] 之间走：再扁就成一条横线，再立就成一根竖线。
 *
 *  **上限 3 是「隔得太近」那一轮调上去的**（作者两句话把这个数夹在中间：先是
 *  「隔这么远，你至于吗」，改小之后「现在感觉隔得有点近了」）。往宽的方向放开、
 *  短半轴不动，是因为画布是细长的：`fitView` 按**高**算缩放，短半轴一大，整张图
 *  连框带字一起被缩小——「远一点」得从横向拿，纵向拿只会又变小。 */
const FLATNESS = { min: 0.5, max: 3 } as const;
const RING_MIN_RX = 190;
const RING_MIN_RY = 78;

/** 人少就把框放大一档。**同一块地方，人少该每个都看得清，人多该都放得下。** */
function nodeScale(others: number): { fontSize: number; padding: string } {
  // **上下比左右薄**（2026-09-04 作者：「这个节点其实可以不要这么高」）：框里只有
  // 一行名字，纵向的内边距一大就成了一个方块；宽度留着，名字两侧才不贴边。
  if (others <= 3) return { fontSize: 15, padding: "5px 14px" };
  if (others <= 6) return { fontSize: 14, padding: "4px 12px" };
  if (others <= 10) return { fontSize: 13, padding: "4px 10px" };
  return { fontSize: 12, padding: "3px 9px" };
}

/** 气泡里的一行：一条关系 + 它是不是最新的那条。 */
type TipLine = { text: string; latest: boolean };

type PersonData = {
  label: string;
  /** 这个人和中心那个人之间的关系，**一条一行**；`null` = 没点它，不冒气泡。 */
  tip: TipLine[] | null;
  /** 气泡往上还是往下长：**朝中心那一侧**（节点在中心下方就往上长，反之往下）。
   *
   *  ⚠️ 左右两版都试过、都不行，别改回去（两次都是截图自查发现的）：
   *  - **往外（远离中心）**：`fitView` 只把节点的包围盒装进画布，气泡不算在内，
   *    最外那个的气泡直接被画布边裁掉半截。
   *  - **往里（朝中心）**：正对着中心那个人的框，把他的名字盖掉一半。
   *  竖着放两头都躲开了：气泡只有二十几像素高，而每个节点离中心至少 70px，
   *  盖不到中心；朝中心那一侧长，也不会顶到画布上下边。 */
  side: "up" | "down";
};

/** 中心那个人和 `peerId` 之间的关系，**从新到旧一条一行**。
 *
 *  关系是会变的（作者原话：「比如说敌人，然后又变成情人什么的」），所以这儿和「状态」
 *  那一格同一套口径：**按生效章从新到旧排，最新那条打「（最新）」**，且只有超过一条
 *  时才打——只有一条时那个标签什么也没区分（同 `CharacterStatus`）。 */
function relationLines(g: Subgraph, peerId: string, language: Language): TipLine[] {
  const between = g.edges
    .filter(
      (e) =>
        (e.src === g.center.id && e.dst === peerId) || (e.dst === g.center.id && e.src === peerId),
    )
    .sort((a, b) => b.valid_from_chapter - a.valid_from_chapter);
  const newest = between.length > 1 ? between[0].valid_from_chapter : null;
  return between
    .map((e) => {
      /* **是 `props.value`，不是 `props.display`**：`CanonEdgeEditRequest.kind
         === "relation"` 那条请求参数叫 `display`，但落库时和 `HAS_STATE` 共用同一个
         `EdgeProps.value` 字段（`extract/ingest_helpers.py` 的写入路径直接读写
         `.props.value`，`RELATED_TO`/`HAS_STATE` 没有分两个字段）。`Edge.props` 的
         TS 类型也只声明了 `value`，没有 `display`。 */
      const name = e.props.value || edgeLabelText(e.type, language);
      return {
        text:
          language === "zh"
            ? `${name} · 第 ${e.valid_from_chapter} 章起`
            : `${name} · Since chapter ${e.valid_from_chapter}`,
        latest: e.valid_from_chapter === newest,
      };
    });
}

/** 子图 → 画布。**只留人物，且只留跟中心连得上的那些人物。**
 *
 *  `pickedId` 是作者刚点的那个人物；它决定哪个节点带气泡（气泡内容在这儿算好，
 *  组件只负责画）——所以「点谁冒什么」是一个纯函数，测得动，不用去骗 ReactFlow。 */
export function toFlow(
  g: Subgraph,
  language: Language,
  pickedId?: string | null,
  box: { w: number; h: number } = FALLBACK_BOX,
): { nodes: RFNode[]; edges: RFEdge[] } {
  const people = g.nodes.filter((n) => n.label === "Character");
  const ids = new Set(people.map((n) => n.id));
  // 留下的边要同时满足两条：
  //   ① 两端都是人 —— `LOCATED_AT`（→ 地点）、`HAS_STATE`（→ 状态维度）、
  //      `MEMBER_OF`（→ 势力）就此从图上消失；
  //   ② **有一端是中心** —— 作者 2026-09-10：「没有直接关系的不用补到关系图谱中」。
  //
  // ⚠️ 第 ② 条**必须写在这儿，不能只靠 `HOPS = 1`**：那个常量在两百行以外，
  // 而这条规则是「这张图画什么」的定义。只改常量的话，哪天有人为了别的需要把它调回 2，
  // 「朋友的朋友」会**静默地**重新长出来——那正是它上一次混进来的方式。
  const between = g.edges.filter(
    (e) => ids.has(e.src) && ids.has(e.dst) && (e.src === g.center.id || e.dst === g.center.id),
  );
  const linked = new Set(between.flatMap((e) => [e.src, e.dst]));
  // 只靠地点连过来的人（同在观音寺的第三个人）现在一条边都没有了——**孤零零一个框
  // 比没有它更让人困惑**，所以一起去掉，中心自己除外。
  const kept = people.filter((n) => n.id === g.center.id || linked.has(n.id));
  const others = kept.filter((n) => n.id !== g.center.id);

  // 周长 ≈ 人数 × 间距；扁平度跟着画布。两个下限按同一个倍数放大，**不各自夹**——
  // 各自夹会把扁平度夹没了（细长画布上又变回正圆）。
  const flat = Math.min(FLATNESS.max, Math.max(FLATNESS.min, box.w / box.h));
  const perimeter = Math.max(3, others.length) * NEIGHBOR_GAP;
  let ry = perimeter / (Math.PI * (1 + flat));
  let rx = flat * ry;
  const grow = Math.max(1, RING_MIN_RX / rx, RING_MIN_RY / ry);
  rx *= grow;
  ry *= grow;
  const size = nodeScale(others.length);

  const nodes: RFNode[] = kept.map((n) => {
    const isCenter = n.id === g.center.id;
    let x = 0,
      y = 0;
    if (!isCenter) {
      const i = others.findIndex((o) => o.id === n.id);
      const a = (2 * Math.PI * i) / Math.max(1, others.length);
      x = rx * Math.cos(a);
      y = ry * Math.sin(a);
    }
    const data: PersonData = {
      // **只写名字**：图上每一个都是人，再写一行「人物」就是给每个框贴同一张标签。
      label: n.name,
      tip: !isCenter && n.id === pickedId ? relationLines(g, n.id, language) : null,
      // 在中心下方（y > 0）就往上长，在上方或齐平就往下长——两头都躲开。
      side: y > 0 ? "up" : "down",
    };
    return {
      id: n.id,
      type: "person",
      position: { x, y },
      data: data as unknown as Record<string, unknown>,
      style: {
        border: `1px solid var(--accent)`,
        borderWidth: isCenter ? 2 : 1,
        borderRadius: 8,
        padding: size.padding,
        background: "var(--bg)",
        color: "var(--ink)",
        fontSize: size.fontSize,
        textAlign: "center",
        boxShadow: isCenter ? "0 0 0 3px var(--sel)" : "none",
      },
    };
  });

  // **边上不挂 label**：关系名和起始章都在气泡里说（见文件顶注第 3 条）。
  const edges: RFEdge[] = between.map((e) => ({
    id: e.id,
    source: e.src,
    target: e.dst,
    style: { stroke: "var(--line)" },
  }));
  return { nodes, edges };
}

function PersonNode({ data }: NodeProps) {
  const person = data as unknown as PersonData;
  return (
    <>
      {/* 连线要挂在句柄上，自定义节点没有默认句柄。它们只是锚点，不给作者看：
          `.node-handle` 把它们藏起来（`opacity: 0`），连线照旧从上下两头出去。 */}
      <Handle type="target" position={Position.Top} className="node-handle" />
      {person.label}
      {person.tip && (
        /* `nodrag nowheel` 是 ReactFlow 的两个约定类：**没有它们这个气泡里的滚轮
           会被画布拿去缩放、按住滚动条会变成拖动这个人**（滑轮和固定高度是作者
           点名要的，同事件那一格 `.char-events-scroll` 的做法）。
           点在气泡里不关它——`stopPropagation` 拦住冒泡，否则这一下会传到节点上
           被当成「再点一次 = 收起」。 */
        <div
          className={`node-tip ${person.side} nodrag nowheel`}
          onClick={(event) => event.stopPropagation()}
        >
          {person.tip.map((line) => (
            <div className="tip-line" key={line.text}>
              {line.text}
              {line.latest && <span className="fresh">（最新）</span>}
            </div>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="node-handle" />
    </>
  );
}

/** **必须是模块级常量**：每渲染一次新建一个对象会让 ReactFlow 每帧重建节点组件。 */
const NODE_TYPES: NodeTypes = { person: PersonNode };

/** 量这块画布的真实宽高。**jsdom 里没有 `ResizeObserver`**（组件测试），那时用兜底值，
 *  布局照样算得出来，不会因为一个尺寸把测试炸掉。 */
function useCanvasBox(ref: React.RefObject<HTMLDivElement | null>) {
  const [box, setBox] = useState<{ w: number; h: number }>(FALLBACK_BOX);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => {
      const rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      setBox((prev) =>
        prev.w === rect.width && prev.h === rect.height ? prev : { w: rect.width, h: rect.height },
      );
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(read);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return box;
}

export function CharacterRelations({ characterId }: { characterId: string }) {
  const { projectId, chapter } = useCoords();
  const language = useLanguage((s) => s.language);
  const subgraph = useSubgraph(projectId, characterId, chapter, HOPS);
  const [picked, setPicked] = useState<string | null>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const box = useCanvasBox(canvas);
  const [dragged, setDragged] = useState<Record<string, XYPosition>>({});
  const flow = useMemo(
    () => (subgraph.data ? toFlow(subgraph.data, language, picked, box) : null),
    [subgraph.data, language, picked, box],
  );
  // 重新算过布局之后，手动挪过的位置就不再对应任何东西了（见文件顶注）。
  useEffect(() => setDragged({}), [subgraph.data, box.w, box.h]);
  const nodes = useMemo(
    () => (flow?.nodes ?? []).map((n) => (dragged[n.id] ? { ...n, position: dragged[n.id] } : n)),
    [flow, dragged],
  );
  const onNodesChange = (changes: NodeChange[]) =>
    setDragged((prev) => {
      let next = prev;
      for (const change of changes) {
        // 只收位移。选中态这一格不用（气泡走自己的 `picked`），尺寸由 ReactFlow
        // 自己量，都不需要在这儿落地。
        if (change.type === "position" && change.position) {
          next = { ...next, [change.id]: change.position };
        }
      }
      return next;
    });

  return (
    <div className="grp">
      <div className="lab">{language === "zh" ? "关系" : "Relationships"}</div>
      {flow && flow.edges.length === 0 && (
        <span className="empty">
          {language === "zh" ? "尚无记录的关系" : "No recorded relationships yet"}
        </span>
      )}
      {flow && flow.edges.length > 0 && (
        <>
          <div
            ref={canvas}
            /* `marginBottom` 不是装饰：那句「点一个人看…」的说明 2026-09-04 撤了
               （作者：「这句话可以去掉，去掉之后这个空间和下面的标题依旧要有一定的
               隔离」），画布和下一格的小标题之间的空当原来是那行字撑着的。 */
            style={{
              height: 260,
              margin: "8px 0 10px",
              border: "1px solid var(--line)",
              borderRadius: 8,
            }}
          >
            <ReactFlow
              nodes={nodes}
              edges={flow.edges}
              nodeTypes={NODE_TYPES}
              onNodesChange={onNodesChange}
              fitView
              // **放大是有意的**：布局只算「这些人之间该隔多远」，铺满画布由这一步做。
              // 框和线一起放大，所以「线相对框有多长」不变——那个比例才是作者说的
              // 「隔得好远」。1.6 是上限，再大就成海报了。
              fitViewOptions={{ padding: 0.12, maxZoom: 1.6 }}
              proOptions={{ hideAttribution: true }}
              // 点一个人 = 看他和中心是什么关系；再点一次（或点空白）收起。
              onNodeClick={(_, n) => setPicked((prev) => (prev === n.id ? null : n.id))}
              onPaneClick={() => setPicked(null)}
            >
              <Background gap={16} color="var(--line)" />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
          {subgraph.data!.truncated && (
            <div className="row dim">
              {language === "zh" ? "部分内容已折叠" : "Some content collapsed"}
            </div>
          )}
        </>
      )}
      {subgraph.isFetching && !flow && (
        <span className="empty">{language === "zh" ? "关系图加载中…" : "Loading the graph…"}</span>
      )}
    </div>
  );
}
