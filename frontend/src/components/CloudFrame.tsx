// 角色册行的云纹描边。作者原话：现有的「改名/删」按钮墙 + 矩形描边太丑，要换成
// 中式如意卷云纹——描边线本身由连续的云头/内卷/尾线组成，不是「矩形框 + 贴几朵云」。
//
// ── 2026-09-03：整份几何换成作者给的参考实现 ────────────────────────────────
//
// 前两版（`<pattern>` 平铺一个 61×18 的云头单元 + 四条边四个角八个小片段）作者一句
// 「太丑」否掉了，**问题不在比例，在结构**：一朵云被压成「一条弧 + 一个旋涡」两笔，
// 平铺出来是一排等距的锯齿；参考图里一朵云是**五笔分开的细线**——引子波、外层云头、
// 偏心内旋、浅底卷、朵与朵之间那道略微上勾的连接——笔画之间留白，所以看着是「云」
// 不是「波浪」。这种留白 `<pattern>` 给不了：pattern 内是一份路径，拆成五笔就得拆成
// 五个 pattern，而它们必须按同一个相位对齐，pattern 不保证这件事。
//
// 所以改成**每一行按自己量到的像素尺寸现拼一条闭合 path**（上边 → 右竖边 → 下边 →
// 左竖边）。`viewBox` 直接等于像素尺寸，1 用户单位 = 1px，云头永远不被行宽拉伸；
// 行越宽只是重复的份数越多，除不尽的余量只进末尾那截很浅的连接线（`connectorStroke`），
// 不摊到每朵云上。
//
// ⚠️ **下边沿是「同一份横向几何画在 y = height」**，不是把上边沿翻转过来。两行贴着
// 排（`.roster-row` 没有下外边距）时，上一行的下沿和下一行的上沿逐点重合成同一条线；
// 翻转会得到镜像的两条线叠在一起，那正是参考图里没有、旧实现里有的那道「双线」。
//
// 竖边/转角是另一套 10 笔的几何（上卷、内圈小眼、S 形过渡、下卷……），按 `height/48`
// 纵向缩放——**不是把横纹旋转 90°**：旋转过的云头在转角处会和横纹撞成一团黑。

import { useLayoutEffect, useMemo, useRef, useState } from "react";

/** 一笔独立的细线：一个起点 + 若干段三次贝塞尔（每段 6 个数 = 三个点）。 */
type Stroke = { start: [number, number]; cubics: number[][]; closed?: boolean };

// 几何单位就是 CSS 像素。45px 一朵接近参考图的节奏，也刚好放得下「外层云头 + 内卷小眼
// + 浅底卷 + 朵间上勾」四件东西。
const TARGET_PITCH = 45;
const CORNER_SPAN = 16;
const REFERENCE_HEIGHT = 48;
const MIN_CLOUD_WIDTH = CORNER_SPAN * 2 + TARGET_PITCH;
const MIN_CLOUD_HEIGHT = 40;

/* 一朵 45px 横向云。**刻意拆成五笔**：起笔各自分开，朵与朵之间那点白隙才留得住——
   连成一笔就退化成一条粗黑波浪带（旧实现的病）。 */
const HORIZONTAL_STROKES: Stroke[] = [
  {
    start: [0, 0],
    cubics: [
      [1.0, -0.18, 2.55, -0.9, 4.0, -0.95],
      [4.95, -0.95, 5.9, -0.2, 6.55, 0],
    ],
  },
  {
    start: [6.95, 0],
    cubics: [
      [7.85, -2.6, 10.0, -5.4, 12.75, -5.8],
      [15.0, -6.25, 15.8, -8.05, 19.2, -8.6],
      [22.4, -9.1, 27.4, -7.8, 29.2, -5.4],
      [30.5, -3.15, 30.2, -0.8, 28.1, 0],
    ],
  },
  {
    // 内卷**偏心**落在外层云头里，是一条开放的细旋涡，不是压在云头上的第二道弧。
    start: [18.1, -0.9],
    cubics: [
      [16.45, -1.45, 16.15, -3.4, 17.35, -4.8],
      [18.75, -6.35, 21.55, -6.9, 23.8, -6.1],
      [26.15, -5.3, 27.35, -3.45, 26.55, -1.95],
      [25.85, -0.75, 24.35, -0.35, 23.15, -1.08],
      [21.95, -1.8, 21.9, -2.95, 22.95, -3.65],
      [23.95, -4.32, 25.0, -3.78, 25.18, -2.95],
      [25.35, -2.25, 24.95, -1.72, 24.35, -1.62],
      [24.95, -1.0, 25.35, -0.62, 25.9, -0.38],
    ],
  },
  {
    start: [31.45, 0],
    cubics: [
      [30.25, 0.22, 31.65, -1.3, 33.45, -1.82],
      [35.55, -2.45, 37.7, -1.45, 39.25, -0.18],
      [40.35, 0.52, 41.15, 0.08, 42.0, -0.1],
    ],
  },
  {
    // 收笔停在基线略上方：这是参考图里朵与朵之间那道很浅的上勾。
    start: [42.85, 0],
    cubics: [
      [43.45, 0.08, 43.75, -0.35, 44.05, -0.58],
      [44.35, -0.78, 44.7, -0.68, TARGET_PITCH, -0.48],
    ],
  },
];

/* 竖边 + 转角。x 是从行的左右边沿往里，y 顺屏幕从上到下。上下两段外卷各自跟自己的
   内圈小眼、小点分开成独立笔画——转角比直边多这几层，才不会糊成一条粗线。 */
const VERTICAL_STROKES: Stroke[] = [
  {
    start: [16, 0],
    cubics: [
      [14.0, -0.2, 11.6, 0.55, 9.7, 2.2],
      [7.6, 4.05, 4.2, 4.9, 2.55, 7.35],
      [0.75, 9.75, 0.25, 12.9, 1.7, 15.35],
      [3.1, 17.8, 6.25, 19.05, 8.2, 17.85],
      [9.8, 16.85, 10.2, 14.65, 9.35, 12.95],
      [8.9, 11.7, 8.65, 11.2, 8.2, 11.0],
    ],
  },
  {
    // 转角上多搭一道短肩线，省得那个弯读成一条加粗的轮廓。
    start: [12.0, 1.25],
    cubics: [
      [11.15, 1.05, 10.25, 1.7, 9.35, 2.55],
      [8.65, 3.25, 7.95, 3.7, 7.25, 3.9],
    ],
  },
  {
    start: [7.9, 10.8],
    cubics: [
      [5.5, 9.95, 3.35, 11.1, 3.2, 13.0],
      [3.05, 15.05, 5.25, 15.95, 6.95, 14.75],
      [8.15, 13.9, 7.55, 11.95, 6.2, 11.65],
      [5.25, 11.45, 4.75, 12.35, 5.15, 13.0],
      [5.5, 13.55, 6.15, 13.4, 6.3, 12.9],
    ],
  },
  {
    start: [5.9, 12.65],
    cubics: [
      [5.55, 12.75, 5.58, 13.15, 5.88, 13.28],
      [6.2, 13.42, 6.42, 13.08, 6.25, 12.8],
      [6.1, 12.58, 5.98, 12.55, 5.9, 12.65],
    ],
    closed: true,
  },
  {
    start: [6.85, 18.7],
    cubics: [
      [5.55, 20.65, 4.05, 21.95, 4.2, 24.1],
      [4.4, 26.65, 4.25, 28.45, 3.45, 30.15],
      [3.0, 31.55, 3.35, 32.5, 4.15, 33.0],
    ],
  },
  {
    start: [4.05, 30.1],
    cubics: [
      [1.9, 31.0, -0.55, 33.85, 0.05, 36.85],
      [0.75, 40.25, 3.95, 42.7, 7.6, 42.0],
      [9.15, 41.7, 10.3, 42.25, 10.8, 43.0],
    ],
  },
  {
    start: [7.45, 35.15],
    cubics: [
      [5.35, 34.45, 3.0, 35.7, 2.95, 37.9],
      [2.95, 40.2, 5.55, 41.0, 7.5, 39.45],
      [8.7, 38.45, 8.0, 36.65, 6.6, 36.35],
      [5.45, 36.0, 4.85, 36.95, 5.3, 37.75],
      [5.75, 38.4, 6.55, 38.1, 6.5, 37.5],
    ],
  },
  {
    start: [10.75, 42.95],
    cubics: [
      [10.0, 43.55, 10.15, 44.2, 10.9, 44.8],
      [12.15, 45.65, 13.1, 46.9, 14.25, 47.45],
      [14.95, 47.8, 15.5, 48.0, 16, 48],
    ],
  },
  {
    // 下转角的镜像肩线，同样独立，弯处才有跟上转角一样的透气感。
    start: [13.0, 47.25],
    cubics: [
      [12.35, 47.15, 11.8, 46.65, 11.4, 46.15],
      [11.05, 45.75, 10.85, 45.5, 10.6, 45.25],
    ],
  },
  // 闭合的下眼放在最后：整条 path 因此以一个正当的 `Z` 收尾，而不是让上面那道开放的
  // 肩线被自动斜着连回起点。
  {
    start: [6.0, 37.4],
    cubics: [
      [5.7, 37.55, 5.72, 37.9, 5.98, 38.02],
      [6.25, 38.15, 6.45, 37.82, 6.3, 37.55],
      [6.2, 37.35, 6.08, 37.32, 6.0, 37.4],
    ],
    closed: true,
  },
];

/* ── 底色那一块的边界（2026-09-04）────────────────────────────────────────────
   作者原话：「他现在是一个长方形悬浮色，与我这一栏有点不像」——悬浮/选中的底色原来是
   `.roster-row` 上一条长方形背景，而这一行的轮廓是云纹：长方形的直边和云头各走各的，
   云头落在底色外面的白里，看着是「一条灰带 + 一条不相干的云线」。所以底色改成**按云纹
   现拼一块面积**，边界就是描边本身。

   描边是**五笔分开**才成云（笔间留白），底色要的正好相反：必须一笔连到底才围得出面积。
   于是这儿把「走在最外圈的那几段」接成一条连续轮廓——**是对同一份几何的切片，不是另抄
   一份**。抄一份，底色和描边就会各自漂，而那正是这次要修的病。 */

type Point = [number, number];
/** 轮廓上的一段：`c` 在就是三次贝塞尔，不在就是直线（笔与笔之间那点白隙用它搭过去）。 */
type Seg = { to: Point; c?: [Point, Point] };
/** 一条连续轮廓：一个起点 + 若干段。 */
type Chain = { start: Point; segs: Seg[] };
/** `[第几笔, 取前几段]`。 */
type OutlineSlice = readonly [number, number];

/* 横纹取「引子波 + 外层云头 + 浅底卷 + 朵间上勾」四笔——**跳过第三笔那个内旋**，
   它在云头里面，不是边界。 */
const HORIZONTAL_OUTLINE: readonly OutlineSlice[] = [
  [0, 2],
  [1, 4],
  [3, 3],
  [4, 2],
];
/* 竖边只取每一卷**往外那一段**：卷进去的尾巴（外卷收笔那两段）、内圈小眼、小点、
   两道肩线都在轮廓里面。四笔接起来正好是从上转角 (16,0) 走到下转角 (16,48)。 */
const VERTICAL_OUTLINE: readonly OutlineSlice[] = [
  [0, 4],
  [4, 2],
  [5, 3],
  [7, 3],
];

const fmt = (value: number): string => Number(value.toFixed(3)).toString();

function segsOf(stroke: Stroke, count: number): Seg[] {
  return stroke.cubics.slice(0, count).map((cubic) => ({
    c: [
      [cubic[0], cubic[1]],
      [cubic[2], cubic[3]],
    ] as [Point, Point],
    to: [cubic[4], cubic[5]] as Point,
  }));
}

/** 按切片规格把几笔的外圈段接成一条连续轮廓（断口都在 1.6px 以内，直线搭过去）。 */
function outlineChain(strokes: readonly Stroke[], spec: readonly OutlineSlice[]): Chain {
  const [head, ...rest] = spec;
  const chain: Chain = {
    start: [strokes[head[0]].start[0], strokes[head[0]].start[1]],
    segs: segsOf(strokes[head[0]], head[1]),
  };
  for (const [index, count] of rest) {
    chain.segs.push({ to: [strokes[index].start[0], strokes[index].start[1]] });
    chain.segs.push(...segsOf(strokes[index], count));
  }
  return chain;
}

function mapChain(chain: Chain, map: (point: Point) => Point): Chain {
  return {
    start: map(chain.start),
    segs: chain.segs.map((seg) =>
      seg.c
        ? { c: [map(seg.c[0]), map(seg.c[1])] as [Point, Point], to: map(seg.to) }
        : { to: map(seg.to) },
    ),
  };
}

/** 倒着走同一条轮廓：三次贝塞尔反向 = 两个控制点对调、终点换成上一段的终点。
 *  下边沿和左竖边都得反着走，四条边才首尾相接围成一圈。 */
function reverseChain(chain: Chain): Chain {
  const points: Point[] = [chain.start, ...chain.segs.map((seg) => seg.to)];
  const segs: Seg[] = [];
  for (let i = chain.segs.length - 1; i >= 0; i -= 1) {
    const seg = chain.segs[i];
    segs.push(seg.c ? { c: [seg.c[1], seg.c[0]], to: points[i] } : { to: points[i] });
  }
  return { start: points[points.length - 1], segs };
}


/** 把一笔的 y 按行高缩放（x 是「离边沿多远」，不跟着高度变）。 */
function scaleVertical(stroke: Stroke, height: number): Stroke {
  const scale = height / REFERENCE_HEIGHT;
  return {
    start: [stroke.start[0], Number((stroke.start[1] * scale).toFixed(3))],
    cubics: stroke.cubics.map((cubic) =>
      cubic.map((value, index) => (index % 2 === 0 ? value : Number((value * scale).toFixed(3)))),
    ),
    ...(stroke.closed ? { closed: true } : {}),
  };
}

function shift(stroke: Stroke, dx: number): Stroke {
  return {
    start: [stroke.start[0] + dx, stroke.start[1]],
    cubics: stroke.cubics.map((cubic) =>
      cubic.map((value, index) => (index % 2 === 0 ? value + dx : value)),
    ),
    ...(stroke.closed ? { closed: true } : {}),
  };
}

function appendStroke(
  parts: string[],
  stroke: Stroke,
  map: (point: [number, number]) => [number, number],
): void {
  const start = map(stroke.start);
  parts.push("M", fmt(start[0]), fmt(start[1]));
  for (const cubic of stroke.cubics) {
    parts.push("C");
    for (let i = 0; i < cubic.length; i += 2) {
      const point = map([cubic[i], cubic[i + 1]]);
      parts.push(fmt(point[0]), fmt(point[1]));
    }
  }
  if (stroke.closed) parts.push("Z");
}

/** 宽度除不尽 45px 时，余下那一小截只画一道很浅的连接线——**不拉伸最后一朵云**。 */
function connectorStroke(length: number): Stroke | null {
  if (!Number.isFinite(length) || length <= 0.001) return null;
  const amplitude = Math.min(0.55, length * 0.24);
  const first = Math.max(0.001, length * 0.45);
  const second = Math.max(first + 0.001, length);
  return {
    start: [0, 0],
    cubics: [
      [length * 0.16, -amplitude, length * 0.31, amplitude, first, 0],
      [length * 0.62, 0, length * 0.82, -amplitude, second, -Math.min(0.28, amplitude * 0.65)],
    ],
  };
}

function appendHorizontal(parts: string[], width: number, height: number, bottom: boolean): void {
  const edgeLength = width - CORNER_SPAN * 2;
  const units = Math.max(1, Math.floor(edgeLength / TARGET_PITCH));
  const remainder = Math.max(0, edgeLength - units * TARGET_PITCH);
  const yOffset = bottom ? height : 0;
  // 下边沿走**和上边沿一样的方向**（不翻转），两行贴着排时两条线才逐点重合成一条。
  // path 的绕向无所谓：这东西 `fill: none`。
  const map = ([x, y]: [number, number]): [number, number] => [CORNER_SPAN + x, yOffset + y];

  for (let unit = 0; unit < units; unit += 1) {
    for (const stroke of HORIZONTAL_STROKES) {
      appendStroke(parts, shift(stroke, unit * TARGET_PITCH), map);
    }
  }
  const tail = connectorStroke(remainder);
  if (tail) appendStroke(parts, shift(tail, units * TARGET_PITCH), map);
}

function appendVertical(parts: string[], width: number, height: number, right: boolean): void {
  for (const stroke of VERTICAL_STROKES) {
    appendStroke(parts, scaleVertical(stroke, height), ([inward, along]) => [
      right ? width - inward : inward,
      along,
    ]);
  }
}

/** 按行的真实像素尺寸拼一条闭合云纹 path；太小画不下就返回空串（不画，不硬塞）。 */
export function buildCloudPath(width: number, height: number): string {
  if (!Number.isFinite(width) || !Number.isFinite(height)) return "";
  if (width < MIN_CLOUD_WIDTH || height < MIN_CLOUD_HEIGHT) return "";

  const parts: string[] = [];
  appendHorizontal(parts, width, height, false);
  appendVertical(parts, width, height, true);
  appendHorizontal(parts, width, height, true);
  appendVertical(parts, width, height, false);
  if (parts.at(-1) !== "Z") parts.push("Z");
  return parts.join(" ");
}

/** 上/下边沿的连续轮廓。**单元数和余量的算法必须和 `appendHorizontal` 一模一样**，
 *  否则底色的收边和描边会差出小半朵云。 */
function horizontalOutline(edgeLength: number): Chain {
  const units = Math.max(1, Math.floor(edgeLength / TARGET_PITCH));
  const remainder = Math.max(0, edgeLength - units * TARGET_PITCH);
  const unit = outlineChain(HORIZONTAL_STROKES, HORIZONTAL_OUTLINE);
  const chain: Chain = { start: unit.start, segs: [...unit.segs] };
  // 朵与朵之间那点白隙（描边里是断开的）在这儿必须接上，否则围不出一块面积。
  const append = (next: Chain) => chain.segs.push({ to: next.start }, ...next.segs);
  for (let i = 1; i < units; i += 1) {
    append(mapChain(unit, ([x, y]) => [x + i * TARGET_PITCH, y]));
  }
  const tail = connectorStroke(remainder);
  if (tail) {
    const dx = units * TARGET_PITCH;
    append(mapChain({ start: tail.start, segs: segsOf(tail, tail.cubics.length) }, ([x, y]) => [
      x + dx,
      y,
    ]));
  }
  return chain;
}

/** 悬浮/选中那块底色的形状：**云纹围出来的那一条**，不是行盒子那个长方形。
 *
 *  上下两条边界是同一份横向几何差一个行高（同 `appendHorizontal` 里那条 ⚠️），所以它是
 *  一条**等厚的波浪带**：相邻两行贴着排时，上一行底色的下边界就是下一行底色的上边界，
 *  逐点重合、不留缝也不叠色。云头因此拱到行盒子之上约 9px——那块面积在视觉上本来就
 *  属于这一行（`.cloud-frame svg` 的 `overflow: visible` 就是为它开的）。 */
export function buildCloudFillPath(width: number, height: number): string {
  if (!Number.isFinite(width) || !Number.isFinite(height)) return "";
  if (width < MIN_CLOUD_WIDTH || height < MIN_CLOUD_HEIGHT) return "";

  const horizontal = horizontalOutline(width - CORNER_SPAN * 2);
  const scale = height / REFERENCE_HEIGHT;
  const vertical = mapChain(outlineChain(VERTICAL_STROKES, VERTICAL_OUTLINE), ([inward, along]) => [
    inward,
    along * scale,
  ]);
  const loop: Chain[] = [
    mapChain(horizontal, ([x, y]) => [CORNER_SPAN + x, y]),
    mapChain(vertical, ([inward, along]) => [width - inward, along]),
    reverseChain(mapChain(horizontal, ([x, y]) => [CORNER_SPAN + x, height + y])),
    reverseChain(vertical),
  ];

  const parts: string[] = [];
  loop.forEach((chain, index) => {
    // 四条边的接头差不到 1px（云头收笔停在基线略上方），直线接一下就闭合。
    parts.push(index === 0 ? "M" : "L", fmt(chain.start[0]), fmt(chain.start[1]));
    for (const seg of chain.segs) {
      if (seg.c) {
        parts.push("C", fmt(seg.c[0][0]), fmt(seg.c[0][1]), fmt(seg.c[1][0]), fmt(seg.c[1][1]));
      } else {
        parts.push("L");
      }
      parts.push(fmt(seg.to[0]), fmt(seg.to[1]));
    }
  });
  parts.push("Z");
  return parts.join(" ");
}

type Box = { w: number; h: number };

type Row = {
  update: (box: Box) => void;
  /** 这一行在（或快要进）视口里吗。没有 `IntersectionObserver` 时恒为 `true`。 */
  onscreen: boolean;
  /** 屏幕外量到的新尺寸：先记账，滚进视口再算几何。 */
  deferred: Box | null;
};

const rows = new Map<Element, Row>();

/* ── 只给看得见的那些行算几何 ──────────────────────────────────────────────
 *
 * **这一层是 2026-09-09 加的，起因是作者拖右栏分隔条时「特别卡」。** 量出来的账：
 * 真书的角色册 **398 行、796 条 path，`d` 属性加起来 8.16 MB**，而拖动是每帧一次
 * `ResizeObserver` 回调 —— 于是每帧重建这 8 MB 字符串、浏览器再重新光栅化 796 条
 * 复杂路径。实测**一帧 320ms（≈3fps）**。
 *
 * 面板里同时看得见的只有十几行，**那 8 MB 里作者当下看得见的不到 4%**。所以屏幕外
 * 的行在 resize 回调里只记账不算几何，滚进视口时再补算一次。这就是列表虚拟化在做的
 * 那件事，只是**不拆 DOM**：拆 DOM 要动 `RosterTab` 的分组、排序、键盘导航和行菜单，
 * 而卡的根源在这一层，不在那一层。
 *
 * `rootMargin` 留 300px 的预热带：等滚到眼前才算，那一下的空档会被看成「云纹闪了一下」。
 *
 * ⚠️ **两个观察器都可能不存在**（jsdom 里两个都没有）。缺 `ResizeObserver` 时退回
 * 「挂载量一次」（原来就是这样）；缺 `IntersectionObserver` 时退回「所有行都算」，
 * 也就是这次优化之前的行为——**降级的方向永远是画得对，不是画得快**。
 */
const sharedVisibility =
  typeof IntersectionObserver === "undefined"
    ? null
    : new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            const row = rows.get(entry.target);
            if (!row) continue;
            row.onscreen = entry.isIntersecting;
            if (entry.isIntersecting && row.deferred) {
              row.update(row.deferred);
              row.deferred = null;
            }
          }
        },
        { rootMargin: "300px" },
      );

const sharedObserver =
  typeof ResizeObserver === "undefined"
    ? null
    : new ResizeObserver((entries) => {
        for (const entry of entries) {
          const row = rows.get(entry.target);
          if (!row) continue;
          // `contentRect` 而不是 `getBoundingClientRect()`：后者 398 行就是 398 次
          // 取尺寸，而这个数观察器已经算好了。**`.cloud-frame` 没有 border/padding**
          // （`position: absolute; inset: 0` 的纯覆盖层），所以两者逐像素相等；
          // 哪天它长出边框，这一行要跟着回到 `getBoundingClientRect()`。
          const box = { w: entry.contentRect.width, h: entry.contentRect.height };
          if (row.onscreen) row.update(box);
          else row.deferred = box;
        }
      });

/** 挂在某一行上的云纹层：一块底色（悬浮/选中才现形）+ 描边。`.cloud-frame` 是
 *  `pointer-events: none` 的覆盖层（styles.css），铺满调用方那个 `position: relative`
 *  的行容器，不挡点击/菜单。 */
export function CloudFrame() {
  const ref = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<Box | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = (next: Box) =>
      setBox((prev) => (prev && prev.w === next.w && prev.h === next.h ? prev : next));
    // 挂载这一次照旧当场量、当场算：这一行现在就要画出来，而 `IntersectionObserver`
    // 的头一次回调是异步的——等它，首屏那十几行会先空一帧。
    const rect = el.getBoundingClientRect();
    update({ w: rect.width, h: rect.height });
    if (!sharedObserver) return;
    // `onscreen` 的初值是「有没有那个观察器」：没有就一直算（降级成优化前的行为），
    // 有就等它的头一次回调说话（它会立刻来，同步的只有这一次挂载量。）
    rows.set(el, { update, onscreen: sharedVisibility === null, deferred: null });
    sharedObserver.observe(el);
    sharedVisibility?.observe(el);
    return () => {
      sharedObserver.unobserve(el);
      sharedVisibility?.unobserve(el);
      rows.delete(el);
    };
  }, []);

  // **算一次就存着**：这两个字符串一条行就上万字符，而这个组件会因为父层任何一次
  // 重渲染跟着重渲染——不锁住的话，尺寸一个像素没变也会把 8 MB 重新拼一遍。
  const d = useMemo(() => (box ? buildCloudPath(box.w, box.h) : ""), [box]);
  const fill = useMemo(() => (box ? buildCloudFillPath(box.w, box.h) : ""), [box]);
  return (
    <div className="cloud-frame" ref={ref} aria-hidden="true">
      {d && box && (
        <svg
          viewBox={`0 0 ${fmt(box.w)} ${fmt(box.h)}`}
          preserveAspectRatio="none"
          focusable="false"
        >
          {/* 底色先画，描边压在它上面——描边是这一块的边界，不能被底色盖住。 */}
          {fill && <path className="cloud-fill" d={fill} />}
          <path className="cloud-stroke" d={d} />
        </svg>
      )}
    </div>
  );
}
