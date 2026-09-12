import { describe, expect, it } from "vitest";
import { fixtures } from "../test/harness";
import { toFlow } from "./CharacterRelations";

// `toFlow` 是纯函数，所以这一格的规矩**不用渲染就测得动**（也就不用去骗 ReactFlow：
// 画布本身在 `RosterTab.test.tsx` 那份 mock 里已经渲过了）。
//
// 这里钉的四条都是 2026-09-04 那次「更精简」重做的成果，每一条坏掉都不会有别的
// 东西红：地点/状态节点重新爬回图上、孤零零的人物框、边上又长出文字、点了人不冒
// 气泡。**「以中心那个人展开」这一条也在**：其余节点等距铺一圈。

type Node = { id: string; label: string; name: string };

const HERO: Node = fixtures.subgraph.center as unknown as Node;
const PEER: Node = { id: "character:ID7", label: "Character", name: "李管家" };
const FRIEND_OF: Node = { id: "character:ID20", label: "Character", name: "苏挽" };
const LONER: Node = { id: "character:ID21", label: "Character", name: "路人" };
const PLACE: Node = { id: "location:ID3", label: "Location", name: "观音寺" };

const edge = (
  id: string,
  src: string,
  dst: string,
  type: string,
  value: string | null,
  from: number,
) => ({
  id,
  src,
  dst,
  type,
  valid_from_chapter: from,
  valid_to_chapter: null,
  evidence_id: null,
  props: { value },
});

/** 一张有人、有地点、有孤立人物、有两跳朋友的图——四条规矩各有一个对象。 */
const graph = () =>
  ({
    ...fixtures.subgraph,
    nodes: [HERO, PEER, FRIEND_OF, LONER, PLACE],
    edges: [
      edge("edge:r1", HERO.id, PEER.id, "RELATED_TO", "宿敌", 5),
      // 两跳：苏挽只跟李管家有关系，和中心没有直接的边。
      edge("edge:r2", PEER.id, FRIEND_OF.id, "RELATED_TO", "旧识", 9),
      // 中心在观音寺，路人也在观音寺——他俩之间**没有**关系边。
      edge("edge:loc1", HERO.id, PLACE.id, "LOCATED_AT", null, 3),
      edge("edge:loc2", LONER.id, PLACE.id, "LOCATED_AT", null, 3),
    ],
    truncated: false,
  }) as unknown as Parameters<typeof toFlow>[0];

/** 一个中心 + n 个都和他直接有关系的人（形状测试要够多的点才看得出椭圆）。 */
const star = (n: number) =>
  ({
    ...fixtures.subgraph,
    nodes: [
      HERO,
      ...Array.from({ length: n }, (_, i) => ({
        id: `character:P${i}`,
        label: "Character",
        name: `人${i}`,
      })),
    ],
    edges: Array.from({ length: n }, (_, i) =>
      edge(`edge:s${i}`, HERO.id, `character:P${i}`, "RELATED_TO", "旧识", 5),
    ),
    truncated: false,
  }) as unknown as Parameters<typeof toFlow>[0];

type TipLine = { text: string; latest: boolean };
const tipOf = (nodes: ReturnType<typeof toFlow>["nodes"], id: string) =>
  (nodes.find((n) => n.id === id)?.data as { tip: TipLine[] | null }).tip;

const spread = (nodes: ReturnType<typeof toFlow>["nodes"], axis: "x" | "y") => {
  const values = nodes.map((n) => n.position[axis]);
  return Math.max(...values) - Math.min(...values);
};

describe("toFlow", () => {
  it("只留人物：地点不上图，只靠地点连过来的人也不上图", () => {
    const { nodes, edges } = toFlow(graph(), "zh");
    const names = nodes.map((n) => (n.data as { label: string }).label);

    expect(names).toContain(HERO.name);
    expect(names).toContain(PEER.name);
    // 地点：作者点名要去掉——「当前所在地」在「状态」那一格已经说了。
    expect(names).not.toContain(PLACE.name);
    // 路人和中心只是同在一个地点。地点一去掉他就一条边都没有了，
    // **一个孤零零的框比没有它更让人困惑**。
    expect(names).not.toContain(LONER.name);
    // 「李管家的旧识」和中心之间没有直接的边（2026-09-10 起不进这张图，见 `HOPS`）。
    expect(names).not.toContain(FRIEND_OF.name);
    // 边只剩**连着中心**的那一条：`edge:r2`（李管家—苏挽）两端都是人，但不碰中心。
    expect(edges.map((e) => e.id)).toEqual(["edge:r1"]);
  });

  it("节点上只写名字，边上一个字都不写", () => {
    const { nodes, edges } = toFlow(graph(), "zh");
    for (const n of nodes) {
      // 图上每一个都是人，再写一行「人物」等于给每个框贴同一张标签。
      expect((n.data as { label: string }).label).not.toMatch(/人物|地点/);
    }
    // 关系名和起始章都进气泡了——边标签在 260px 高的画布上只会互相压着。
    for (const e of edges) expect(e.label).toBeUndefined();
  });

  it("以中心那个人展开：中心在原点且加粗，其余的铺在同一条椭圆上", () => {
    // **8 个点**：角度里含 90°/270°，`max|y|` 才真的等于短半轴（6 个点最高只到
    // sin 60° = .87，拿它当半轴算，椭圆方程自然对不上——这条断言第一版就是这么假红的）。
    const box = { w: 1200, h: 260 };
    const { nodes } = toFlow(star(8), "zh", null, box);
    const center = nodes.find((n) => n.id === HERO.id);
    expect(center?.style?.borderWidth).toBe(2);
    expect(center?.position).toEqual({ x: 0, y: 0 });

    const others = nodes.filter((n) => n.id !== HERO.id);
    const rx = Math.max(...others.map((n) => Math.abs(n.position.x)));
    const ry = Math.max(...others.map((n) => Math.abs(n.position.y)));
    for (const n of others) {
      // 在同一条椭圆上 =（x/rx)² + (y/ry)² ≈ 1。等距那条**不再成立也不该成立**：
      // 正圆塞进细长画布，`fitView` 只能按高去缩，横向就空掉一大片（作者：
      // 「又不密，然后你又很小」）。
      const on = (n.position.x / rx) ** 2 + (n.position.y / ry) ** 2;
      expect(Math.abs(on - 1)).toBeLessThan(0.02);
    }
  });

  it("画布宽就摊得宽，画布高就立起来——圈的形状跟着画布", () => {
    const wide = toFlow(star(6), "zh", null, { w: 1200, h: 260 }).nodes;
    const tall = toFlow(star(6), "zh", null, { w: 320, h: 700 }).nodes;

    // 扁平度夹在 [0.5, 2]：再扁成一条横线、再立成一根竖线，而**圈本身的大小由
    // 人数定，不由画布定**（摊到画布边就是「隔得好远」那个病）。
    expect(spread(wide, "x")).toBeGreaterThan(spread(wide, "y") * 1.5);
    expect(spread(tall, "y")).toBeGreaterThan(spread(tall, "x") * 1.5);
  });

  it("人少的时候框大一档，人多了收回去", () => {
    const few = toFlow(star(2), "zh").nodes[0].style?.fontSize as number;
    const some = toFlow(star(6), "zh").nodes[0].style?.fontSize as number;
    const many = toFlow(star(14), "zh").nodes[0].style?.fontSize as number;
    expect(few).toBeGreaterThan(some);
    expect(some).toBeGreaterThan(many);
  });

  it("点一个人：只有他带气泡，内容是两人之间的关系 + 第几章", () => {
    const { nodes } = toFlow(graph(), "zh", PEER.id);

    expect(tipOf(nodes, PEER.id)).toEqual([{ text: "宿敌 · 第 5 章起", latest: false }]);
    expect(tipOf(nodes, HERO.id)).toBeNull();
  });

  it("关系换过的：从新到旧一条一行，最新那条打「（最新）」", () => {
    // 关系是会变的（作者：「比如说敌人，然后又变成情人什么的」）。同一对人身上
    // 两条边同时有效时，这一格的口径和「状态」那一格一样：新的在上面、打标签。
    const g = {
      ...graph(),
      edges: [
        edge("edge:old", HERO.id, PEER.id, "RELATED_TO", "宿敌", 5),
        edge("edge:new", PEER.id, HERO.id, "RELATED_TO", "情人", 40),
      ],
    } as unknown as Parameters<typeof toFlow>[0];

    expect(tipOf(toFlow(g, "zh", PEER.id).nodes, PEER.id)).toEqual([
      { text: "情人 · 第 40 章起", latest: true },
      { text: "宿敌 · 第 5 章起", latest: false },
    ]);
    // 只有一条时不打标签——那时「最新」什么也没区分（同 `CharacterStatus`）。
    expect(tipOf(toFlow(graph(), "zh", PEER.id).nodes, PEER.id)?.[0].latest).toBe(false);
  });

  it("🔴 两跳外的人**根本不该进这张图**（2026-09-10 起只取一跳）", () => {
    // 这一条原来断言的是「点两跳外的人会说清楚没有直接关系」——那句提示随
    // `HOPS = 1` 一起删了（作者：「没有直接关系的不用补到关系图谱中」）。
    // 现在钉的是更前面那一步：**只靠地点/势力连过来、和中心没有直接边的人，
    // 一个都不该留在画布上**。`toFlow` 里那条「只留跟中心连得上的人」本来就在做这件事，
    // 而它此前被两跳的节点绕过去了——两跳的人之间是有边的，只是那条边不连中心。
    const { nodes } = toFlow(graph(), "zh", FRIEND_OF.id);
    expect(nodes.some((n) => n.id === FRIEND_OF.id)).toBe(false);
  });

  it("气泡竖着长、朝中心那一侧——左右两版都被截图自查否掉了", () => {
    const { nodes } = toFlow(star(8), "zh", null, { w: 1200, h: 260 });
    for (const n of nodes.filter((x) => x.id !== HERO.id)) {
      const { side } = n.data as { side: "up" | "down" };
      // 往外长会被画布边裁半截（`fitView` 不把气泡算进包围盒）；往里长会盖住中心
      // 那个人的名字。竖着朝中心那一侧长，两头都躲开。
      expect(side).toBe(n.position.y > 0 ? "up" : "down");
    }
  });
});
