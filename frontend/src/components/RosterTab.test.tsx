import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { RosterTab } from "./RosterTab";

// ReactFlow 的 canvas/SVG 渲染依赖真实布局测量，jsdom 给不了；同 `LocalGraph.test.tsx`
// 生前的写法——把画布换成轻量桩，测的是接线（拉子图 → 转 flow 节点 → 名字进 DOM），
// 不测 ReactFlow 自己画得对不对。**这份 mock 现在几乎每条「选中一个人物」的测试都会
// 触发**：角色卡把「人物关系」并进来之后，选中人物就会渲染 `CharacterRelations`，
// 它内嵌着同一块画布。
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ nodes, edges }: { nodes: { id: string; data: { label: string } }[]; edges: unknown[] }) => (
    <div data-testid="flow">
      {nodes.map((n) => (
        <span key={n.id} data-testid="flow-node">
          {n.data.label}
        </span>
      ))}
      <span data-testid="flow-edge-count">{edges.length}</span>
    </div>
  ),
  Background: () => null,
  Controls: () => null,
  // `CharacterRelations` 的自定义节点要用这两个（连线的锚点）。**mock 工厂缺一个
  // 具名导出，vitest 直接报错**，所以组件那边一 import 就得在这儿跟一个。
  Handle: () => null,
  Position: { Top: "top", Bottom: "bottom", Left: "left", Right: "right" },
}));

/** 角色册里那几行的名字，**按屏幕上的先后**。排序断言全靠它。 */
function namesOnScreen(): string[] {
  return [...document.querySelectorAll<HTMLElement>(".item .nm")].map(
    (el) => el.textContent ?? "",
  );
}

/** **每一组**内部的名字，按屏幕上的先后。
 *
 *  排序是**组内**的，不是全局的：角色册先按 label 分组（人物 / 地点 / …），
 *  次数只决定组内谁在前面。拿全局序去断言会红在一个根本没坏的地方。 */
function namesPerGroup(): string[][] {
  return [...document.querySelectorAll<HTMLElement>(".grp")].map((grp) =>
    [...grp.querySelectorAll<HTMLElement>(".item .nm")].map((el) => el.textContent ?? ""),
  );
}

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
  // ReactFlow 需要 ResizeObserver 做尺寸测量；jsdom 里没有（同 `LocalGraph.test.tsx`）。
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

describe("角色册", () => {
  it("按 label 分组，人名一个不落", async () => {
    renderWithApi(<RosterTab />);
    for (const n of fixtures.rosterWithCounts) {
      expect(await screen.findByText(n.name)).toBeInTheDocument();
    }
  });

  // ══════════════════════════════════════════════════════════════════════
  // 出场章数 + 排序（2026-08-25）
  //
  // 抽取从这一天起认不出就建人物（ADR 0020 补记），一次性称呼会大量涌进角色册。
  // 按名字排的话主角和路人混在一起，所以组内改成按出场频率降序。
  // ══════════════════════════════════════════════════════════════════════

  it("每一行带出场章数，且它和角色册同一条出参回来", async () => {
    renderWithApi(<RosterTab />);
    const spy = vi.spyOn(globalThis, "fetch");
    const hero = fixtures.rosterWithCounts.find((n) => n.appearance_chapters > 0)!;
    const row = (await screen.findByText(hero.name)).closest(".item") as HTMLElement;

    expect(within(row).getByText(`${hero.appearance_chapters} 章`)).toBeInTheDocument();
    // **不许有第二次请求去拿这个数**：一行字里的一个数不值得多一次会失败、会晚到的往返。
    const urls = spy.mock.calls.map((c) => String(c[0]));
    expect(urls.filter((u) => /appearance|mentions|frequency/.test(u))).toEqual([]);
    expect(document.body.textContent).not.toMatch(/undefined/);
  });

  it("组内按出场章数降序（累计信息量兜底），一颗按钮能倒过来", async () => {
    // ── 为什么排序键是**两个数**，且出场章数在前（2026-08-31）──────────────
    //
    // `appearance_chapters`（「N 章」）是主键——**这一行右边显示的就是它**，
    // 按钮上写的「写得多 → 少」说的也是它。曾经主键是 `information_score`
    // （模型给他写的画像有多长），可那个数不上屏：一本画像已经跑过的书，
    // 屏幕上「20 章、2 章、11 章…」那一串看不出排序在哪
    // （作者原话：「我看这个排序好像不是按照这个顺序来」）。
    //
    // `information_score` 退到兜底：出场章数相等时靠它分高下，两个数各自会在
    // 一整类书上恒为 0（分数要等带画像的抽取跑过，章数要等总结落地）。给作者
    // 一个「按哪个排」的下拉，他会撞上「换了个排法、一列全是 0、看起来像坏了」；
    // 三级排序自己就退化得对。
    const user = userEvent.setup();
    renderWithApi(<RosterTab />);
    await screen.findByText(fixtures.rosterWithCounts[0].name);

    const signal = new Map(
      fixtures.rosterWithCounts.map((n) => [
        n.name,
        [n.appearance_chapters, n.information_score] as const,
      ]),
    );
    const numbers = (group: string[]) => group.map((name) => signal.get(name) ?? [-1, -1]);
    const cmp = (a: readonly number[], b: readonly number[]) =>
      b[0] - a[0] || b[1] - a[1];

    const desc = namesPerGroup().map(numbers);
    for (const group of desc) {
      expect(group).toEqual([...group].sort(cmp));
    }
    // 自守卫：**至少有一组里的数不全相同**，否则上面那条永远绿。
    expect(
      desc.some((group) => new Set(group.map((n) => n.join(","))).size > 1),
    ).toBe(true);

    await user.click(screen.getByRole("button", { name: "排序方向" }));
    for (const group of namesPerGroup().map(numbers)) {
      expect(group).toEqual([...group].sort((a, b) => cmp(b, a)));
    }
  });

  it("次数相同的那一批顺序是稳的 —— 不是每次刷新都换一遍", async () => {
    renderWithApi(<RosterTab />);
    await screen.findByText(fixtures.rosterWithCounts[0].name);
    const first = namesOnScreen();

    document.body.innerHTML = "";
    renderWithApi(<RosterTab />);
    await screen.findByText(fixtures.rosterWithCounts[0].name);

    expect(namesOnScreen()).toEqual(first);
  });

  it("那一列的措辞不许把 0 说成「没出场」", async () => {
    // 这一层数的是**总结**，不是正文。一本还没生成总结的书这一列全是 0，
    // 把它写成「没出场」就是拿一个空表当结论（§10 约束 8）。
    renderWithApi(<RosterTab />);
    await screen.findByText(fixtures.rosterWithCounts[0].name);
    expect(document.body.textContent).not.toMatch(/没出场|未出场|从未出现/);
  });

  // ══════════════════════════════════════════════════════════════════════
  // 事件时间线（2026-08-25）
  //
  // 维护者：「事件是**比较小的一条总结**。只放到和它相关的那个角色下面……
  // 一件事情如果跟好多人相关，那就放到每个相关人的下面。以后在角色册里也能看到。」
  // ══════════════════════════════════════════════════════════════════════

  it("点一个人 → 他的事件按章号排开，同一件事上的其他人写在「还有」里", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    useCoords.setState({ projectId: "project:ID1", selectedNodeId: hero.id });
    renderWithApi(<RosterTab />);

    // **等的是第一行内容，不是那一格的标题**：标题在 `isLoading` 那一档就渲染了，
    // 拿它当就绪信号会在数据到之前就往下断言（第一次写这条测试正是这么红的）。
    // **等的是第一行真的画出来了，不是那一格的标题**：标题在 `isLoading` 那一档就
    // 渲染了，拿它当就绪信号会在数据到之前就往下断言（第一次写这条测试正是这么红的）。
    const timeline = () =>
      [...document.querySelectorAll<HTMLElement>(".grp")].find((g) =>
        g.querySelector(".lab")?.textContent?.includes(`${hero.name}的事件`),
      );
    await waitFor(() =>
      expect(timeline()!.querySelectorAll(".item .nm").length).toBe(
        fixtures.characterEvents.length,
      ),
    );
    const rows = timeline()!.querySelectorAll<HTMLElement>(".item .nm");

    // 按章号升序 —— 时间线的全部意义。
    const chapters = [...rows].map((el) => Number(/第 (\d+) 章/.exec(el.textContent ?? "")![1]));
    expect(chapters).toEqual([...chapters].sort((a, b) => a - b));
    // 摘要是后端给的那一句（作者改过就是新的那一版）。
    expect(rows[0].textContent).toContain(fixtures.characterEvents[0].summary);

    // 同一件事上的**其他人**写在「还有：…」里，而**他自己不在里面**。
    const others = [
      ...fixtures.characterEvents[0].participants,
      ...fixtures.characterEvents[0].knowers,
    ].filter((n) => n.id !== hero.id);
    if (others.length > 0) {
      expect(screen.getAllByText(/还有：/)[0].textContent).toContain(others[0].name);
      expect(screen.getAllByText(/还有：/)[0].textContent).not.toContain(hero.name);
    }
  });

  it("一件事跟几个人相关，就在几个人的线上各出现一次", async () => {
    // 存储那一侧本来就是多对多（`event_participant`），这一条钉的是**界面真的
    // 在每个人名下都画出来了**——「以后在角色册里也能看到」那句话的验收。
    const shared = fixtures.characterEvents[0];
    expect(shared.participants.length).toBeGreaterThan(1); // 自守卫

    for (const who of shared.participants) {
      document.body.innerHTML = "";
      useCoords.setState({ projectId: "project:ID1", selectedNodeId: who.id });
      renderWithApi(<RosterTab />, [
        { match: /\/characters\/[^/]+\/events$/, body: [shared] },
        { match: /\/roster$/, body: fixtures.rosterWithCounts },
      ]);
      expect(await screen.findByText(new RegExp(shared.summary))).toBeInTheDocument();
    }
  });

  it("这件事掉了参与者时有一颗红点，点开是整句人话 + 跳转 + 「知道了」（034 补记）", async () => {
    // **吃真 dump**：`characterEventsCastChanged` 是删掉「沈知微的师弟」之后
    // 真实产出的形状（`title_code`/`title_params`/`jump` 全是后端给的）。
    // 用哪个人当 `selectedNodeId` 不重要——这里借一个真角色册里的人物，
    // 只控制事件路由的响应体（同上面「一件事跟几个人相关」那条测试的手法）。
    const [row] = fixtures.characterEventsCastChanged;
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    useCoords.setState({ projectId: "project:ID1", selectedNodeId: hero.id, chapter: 1 });
    const user = userEvent.setup();
    renderWithApi(<RosterTab />, [
      { match: /\/characters\/[^/]+\/events$/, body: [row] },
    ]);

    // 卡片上不摆机器词：这件事的 kind/id 不该原样出现在屏幕上。
    await screen.findByText(new RegExp(row.summary));
    expect(document.body.textContent).not.toMatch(/event_cast_changed/);
    expect(document.body.textContent).not.toContain(row.cast_changed!.id);

    // 默认收着：只有一颗点，没有整句话。
    const dot = document.querySelector(".cast-dot") as HTMLElement;
    expect(dot).toBeTruthy();
    expect(screen.queryByText(/被移出了这件事/)).toBeNull();

    await user.click(dot);
    // 整句话来自 title_code + title_params，不是前端编的第二份措辞。
    expect(
      await screen.findByText(/「沈知微的师弟」被移出了这件事.*还有 1 人牵扯其中/),
    ).toBeInTheDocument();

    // 跳转坐标是后端给的锚，不从文案里反推。
    await user.click(screen.getByRole("button", { name: "去这一句 →" }));
    expect(useCoords.getState().chapter).toBe(row.cast_changed!.chapter_number);
    expect(useCoords.getState().highlight).toEqual(row.cast_changed!.jump);
  });

  it("「知道了」发的是 resolve 不是 ignore（去重键不含 hash，看过了就该终态）", async () => {
    const [row] = fixtures.characterEventsCastChanged;
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    useCoords.setState({ projectId: "project:ID1", selectedNodeId: hero.id });
    const user = userEvent.setup();
    renderWithApi(<RosterTab />, [
      { match: /\/characters\/[^/]+\/events$/, body: [row] },
      { method: "POST", match: /\/notifications\/[^/]+\/resolve$/, body: { id: row.cast_changed!.id, status: "RESOLVED" } },
    ]);
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await user.click(await screen.findByRole("button", { name: /这件事的参与者变了/ }));
    await user.click(screen.getByRole("button", { name: "知道了" }));

    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining(`/notifications/${encodeURIComponent(row.cast_changed!.id)}/resolve`),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(
      fetchSpy.mock.calls.some(
        ([url]) => String(url).includes("/ignore"),
      ),
    ).toBe(false);
  });

  it("空的时候说清楚为什么空，不写「暂无数据」", async () => {
    // **这一格今天在真书上必然是空的**（那本 158 章的书里事件 0 条），
    // 所以空态那句话是这一件唯一每天都被看见的部分。
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    useCoords.setState({ projectId: "project:ID1", selectedNodeId: hero.id });
    renderWithApi(<RosterTab />, [
      { match: /\/characters\/[^/]+\/events$/, body: [] },
    ]);

    const said = await screen.findByText(/还没有跟.*有关的事件/);
    expect(said.textContent).toMatch(/还没整理过|没有落到/);
    expect(document.body.textContent).not.toMatch(/暂无数据|暂无|空空如也/);
  });

  it("选中的不是人物时，不画事件那一格", async () => {
    // 事件名单两维收的都是人物，拿一个地点去问「他的事件」后端会 422。
    // 画一颗必然撞 422 的东西比不画更糟（同日志页「endpoints 空就不画」那条）。
    const place = fixtures.rosterWithCounts.find((n) => n.label === "Location")!;
    useCoords.setState({ projectId: "project:ID1", selectedNodeId: place.id });
    renderWithApi(<RosterTab />);

    await screen.findByText(place.name);
    expect(screen.queryByText(/的事件/)).toBeNull();
  });

  // ══════════════════════════════════════════════════════════════════════
  // 删 / 改名（自动建人物的配套）
  // ══════════════════════════════════════════════════════════════════════

  it("删之前问一句 —— 这一步不可逆", async () => {
    const user = userEvent.setup();
    const target = fixtures.rosterWithCounts[0];
    renderWithApi(<RosterTab />, [
      {
        method: "DELETE",
        match: /\/nodes\//,
        body: {
          id: target.id,
          name: target.name,
          usage: { node_id: target.id, name: target.name, edges: 0, events: 0 },
        },
      },
    ]);
    const row = (await screen.findByText(target.name)).closest(".item") as HTMLElement;
    const spy = vi.spyOn(globalThis, "fetch");
    const deletes = () =>
      spy.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "DELETE");

    // 「删」原来是常驻按钮，2026-09-01 收进了悬浮才现身的「⋯」菜单（云纹描边重设计）。
    await user.click(within(row).getByRole("button", { name: `${target.name}的更多操作` }));
    await user.click(within(row).getByRole("button", { name: "删除" }));
    // **按下「删除」还没删**：先出现一句问话。
    expect(deletes()).toEqual([]);
    expect(within(row).getByText(/删了拿不回来/)).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "删掉" }));
    await waitFor(() => expect(deletes().length).toBe(1));
    // 版本走查询参数（带 body 的 DELETE 在各家客户端上支持得参差不齐）。
    expect(String(deletes()[0][0])).toMatch(/expected_canon_version=\d+/);
  });

  it("「算了」真的取消，不发请求", async () => {
    const user = userEvent.setup();
    const target = fixtures.rosterWithCounts[0];
    renderWithApi(<RosterTab />, [{ method: "DELETE", match: /\/nodes\//, body: {} }]);
    const row = (await screen.findByText(target.name)).closest(".item") as HTMLElement;
    const spy = vi.spyOn(globalThis, "fetch");

    await user.click(within(row).getByRole("button", { name: `${target.name}的更多操作` }));
    await user.click(within(row).getByRole("button", { name: "删除" }));
    await user.click(within(row).getByRole("button", { name: "算了" }));

    expect(
      spy.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "DELETE"),
    ).toEqual([]);
    expect(within(row).queryByText(/删了拿不回来/)).toBeNull();
  });

  it("改名发的是 PATCH，且带着它正在渲染的那个版本号", async () => {
    const user = userEvent.setup();
    const target = fixtures.rosterWithCounts[0];
    renderWithApi(<RosterTab />, [
      { method: "PATCH", match: /\/nodes\//, body: { ...target, name: "新名字" } },
    ]);
    const row = (await screen.findByText(target.name)).closest(".item") as HTMLElement;
    const spy = vi.spyOn(globalThis, "fetch");

    await user.click(within(row).getByRole("button", { name: `${target.name}的更多操作` }));
    await user.click(within(row).getByRole("button", { name: "改名" }));
    const box = within(row).getByPlaceholderText("输入新的名称");
    await user.clear(box);
    await user.type(box, "新名字{Enter}");

    const patches = () =>
      spy.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PATCH");
    await waitFor(() => expect(patches().length).toBe(1));
    const sent = JSON.parse(String((patches()[0][1] as RequestInit).body));
    expect(sent).toMatchObject({ name: "新名字" });
    expect(sent.expected_canon_version).toBe(fixtures.projects[0].canon_version);
  });

  it("空角色册只给一个清楚的下一步，不解释内部实现", async () => {
    renderWithApi(<RosterTab />, [{ match: /\/roster$/, body: [] }]);
    expect(await screen.findByText(/添加第一个条目/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/导入只切章|认知矩阵|规则|ADR/);
  });

  it("点「建第一个」能开出建条目的抽屉 —— 空态那句话必须真的有出口", async () => {
    renderWithApi(<RosterTab />, [{ match: /\/roster$/, body: [] }]);
    (await screen.findByText(/添加第一个条目/)).click();
    expect(await screen.findByRole("heading", { name: "角色册" })).toBeInTheDocument();
  });

  // ══════════════════════════════════════════════════════════════════════
  // 角色卡（2026-08-31）：「人物状态」「人物关系」「原文依据」三个 tab 并进来，
  // 点一个人不再跳页，卡片原地展开在他那一行下面。
  // ══════════════════════════════════════════════════════════════════════

  it("点一个人物 = 原地展开他的卡，不再跳去一个别的 tab", async () => {
    // **不用 `rosterWithCounts[0]`**：那是个地点（数组第一个恰好是 `location:ID5`），
    // 卡片只对人物展开（见 `RosterTab.tsx` 顶注），拿它测会掩盖这条真正在测的行为。
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    renderWithApi(<RosterTab />);
    (await screen.findByText(hero.name)).click();
    await waitFor(() => {
      expect(useCoords.getState().selectedNodeId).toBe(hero.id);
      // 「关系」并进角色册之前，`focusNode` 把 `activeTab` 切到已经不存在的
      // `"graph"`；现在这个动作**留在原地**，角色册本身就是落点。
      expect(useCoords.getState().activeTab).toBe("roster");
    });
    expect(await screen.findByText(hero.name + "的事件")).toBeInTheDocument();
  });

  it("地点/势力选中之后没有卡片可展开——只有人物有状态/关系/依据", async () => {
    const place = fixtures.rosterWithCounts.find((n) => n.label === "Location")!;
    renderWithApi(<RosterTab />);
    (await screen.findByText(place.name)).click();
    await waitFor(() => expect(useCoords.getState().selectedNodeId).toBe(place.id));
    expect(screen.queryByText(/的事件/)).toBeNull();
  });

  it("再点一次已经展开的名字 = 收起卡片", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    const user = userEvent.setup();
    renderWithApi(<RosterTab />);
    const row = (await screen.findByText(hero.name)).closest(".roster-row") as HTMLElement;
    await user.click(within(row).getByText(hero.name));
    await screen.findByText(hero.name + "的事件");

    // 展开之后名字在屏幕上**只有一处**了——卡片 2026-09-04 起不再印一遍本名
    // （作者：「这上边已经有贾环了，为什么在正文还要整一个」）。`within(row)` 留着
    // 是因为它说的正是「点行上那一个」，不靠「屏幕上只有一个」这个会随卡片内容变的
    // 前提；那条「卡里没有本名」的断言在 `CharacterBasicInfo.test.tsx` 里。
    await user.click(within(row).getByText(hero.name));
    await waitFor(() => expect(useCoords.getState().selectedNodeId).toBeNull());
    expect(screen.queryByText(hero.name + "的事件")).toBeNull();
  });

  it("状态：所在地、维度、生死都渲染出来", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    // 拿真 dump 改字段，不手写一份快照（同这个仓库其余测试的规矩）。
    const withState = {
      ...fixtures.characterState,
      is_dead: true,
      location: fixtures.states[0].location,
      // 状态那一格 2026-09-06 起读 `state_history`（每格可能多条），不是 `states`
      // （每格只有当前那一条）。两个都塞上：`states` 还喂着「已亡」那一档。
      states: [{ dim: { id: "statedim:1", label: "StateDim", name: "武功境界" }, dim_key: null, value: "筑基期", value_key: null, since_chapter: 3 }],
      state_history: [{ dim: { id: "statedim:1", label: "StateDim", name: "武功境界" }, dim_key: null, value: "筑基期", value_key: null, since_chapter: 3 }],
    };
    renderWithApi(<RosterTab />, [{ match: /\/characters\/.*\/state/, body: withState }]);
    (await screen.findByText(hero.name)).click();

    // **等的是数据到了，不是卡片容器挂上去**：`.char-card` 点开那一刻就同步挂进
    // DOM，这时状态那一格还在「读取中…」——拿容器存不存在当就绪信号会在数据到之前
    // 就往下断言（同 `CharacterTimeline` 顶上那段注释讲的坑）。
    // **爬到 `.st-dim` 那一行再断言**：2026-09-06 起字段名和值分在两个 span 里
    // （一行内可以有好几个带章号的值），`findByText(/武功境界/)` 拿到的是
    // 字段名那个 span，里面只有「武功境界：」——同 `CharacterStatus.test.tsx`
    // 里记着的那个坑。
    const dimRow = (await screen.findByText(/武功境界/)).closest(".st-dim") as HTMLElement;
    expect(dimRow).toHaveTextContent("筑基期");
    expect(dimRow).toHaveTextContent("第 3 章");
    // **`within(card)`，不是裸 `screen`**：地点「青云城主府」在角色册列表自己那一行
    // 也是一个独立的文本节点（它本来就是角色册里的一个条目），裸查会撞「不止一个匹配」。
    const card = dimRow.closest(".char-card") as HTMLElement;
    expect(within(card).getByText(new RegExp(fixtures.states[0].location!.name))).toBeInTheDocument();
    expect(within(card).getByText(/已亡/)).toBeInTheDocument();
  });

  it("状态：还没有维度时说清楚是为什么，不是「暂无数据」", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    renderWithApi(<RosterTab />); // 默认 fixture 的 `states`/`edges` 都是空的
    (await screen.findByText(hero.name)).click();
    expect(await screen.findByText(/还没有记录他的状态/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/暂无数据|暂无|空空如也/);
  });

  it("关系：只有一张图、图上只有人，那份文字清单不再出现", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    const peer = fixtures.rosterWithCounts.find(
      (n) => n.id !== hero.id && n.label === "Character",
    )!;
    const place = fixtures.rosterWithCounts.find((n) => n.label === "Location")!;
    // **数据从子图端点来，不再从 `…/state` 来**：文字清单删掉之后这一格只读子图
    // （`useCharacterState` 整个不用了），往 state 里塞关系边现在什么都不会发生。
    const withGraph = {
      ...fixtures.subgraph,
      center: { id: hero.id, label: "Character", name: hero.name },
      nodes: [
        { id: hero.id, label: "Character", name: hero.name },
        { id: peer.id, label: "Character", name: peer.name },
        { id: place.id, label: "Location", name: place.name },
      ],
      edges: [
        {
          id: "edge:related1", src: hero.id, dst: peer.id, type: "RELATED_TO",
          valid_from_chapter: 5, valid_to_chapter: null, evidence_id: null,
          props: { value: "宿敌" },
        },
        {
          id: "edge:loc1", src: hero.id, dst: place.id, type: "LOCATED_AT",
          valid_from_chapter: 3, valid_to_chapter: null, evidence_id: null,
          props: { value: null },
        },
      ],
    };
    renderWithApi(<RosterTab />, [{ match: /\/subgraph/, body: withGraph }]);
    (await screen.findByText(hero.name)).click();

    const labels = (await screen.findAllByTestId("flow-node")).map((n) => n.textContent);
    expect(labels).toContain(peer.name);
    // 地点不上图（作者点名：那是「状态」那一格的事）。
    expect(labels).not.toContain(place.name);
    // 那三行「对端 — 关系 · 第 N 章起」的清单整块删了——同一件事不在一屏上说两遍。
    expect(screen.queryByText(/宿敌 · 第 5 章起/)).toBeNull();
  });

  it("卡片展开时，那一行右端的「⋯」变成收起按钮，点它就收起", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    const user = userEvent.setup();
    renderWithApi(<RosterTab />);
    const row = (await screen.findByText(hero.name)).closest(".roster-row") as HTMLElement;
    await user.click(within(row).getByText(hero.name));
    await screen.findByText(hero.name + "的事件");

    // 展开态那个位置不再是「⋯」——一次点击不能既是「看更多操作」又是「收起」。
    expect(within(row).queryByRole("button", { name: `${hero.name}的更多操作` })).toBeNull();
    await user.click(within(row).getByRole("button", { name: `收起${hero.name}的卡片` }));
    await waitFor(() => expect(useCoords.getState().selectedNodeId).toBeNull());
    // 收起之后「⋯」回来了。
    expect(within(row).getByRole("button", { name: `${hero.name}的更多操作` })).toBeInTheDocument();
  });

  it("依据：带 evidence_id 的边取回原文，没有的边不显示", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    // `fixtures.states[0].edges[0]` 真的带 `evidence_id`（`evidence:ID14`），
    // 但契约夹具里没有单独一份 `/evidence/{id}` 的真 dump——那条端点只在这里
    // 手写一份（形状照 `EvidenceView`），不是绕过「吃真 dump」那条规矩，是补它没照到的一格。
    // 真 dump 里那条带引语的边是 `LOCATED_AT`，而**所在地 2026-09-04 起不进这一格**
    // （作者：「上面都去掉那些地址了，下面应该跟一下」，见 `CharacterEvidence.tsx`）。
    // 这条测的是「有引语就取回原文」，和边的种类无关，所以只把种类换成关系——
    // **别为了让它绿把地址那一行放回去。**
    const withEvidence = {
      ...fixtures.characterState,
      edges: fixtures.states[0].edges.map((e) => ({ ...e, type: "RELATED_TO" })),
    };
    const evidenceView = {
      id: "evidence:ID14",
      chapter_number: 1,
      quote_text: "萧决站在青云城主府的门前。",
      anchor: { para_index: 0, quote_text: "萧决站在青云城主府的门前。", occurrence_k: 1 },
    };
    renderWithApi(<RosterTab />, [
      { match: /\/characters\/.*\/state/, body: withEvidence },
      { match: /\/evidence\//, body: evidenceView },
    ]);
    (await screen.findByText(hero.name)).click();

    expect(
      await screen.findByText(new RegExp(`依据 第 ${evidenceView.chapter_number} 章`)),
    ).toHaveTextContent(evidenceView.quote_text);
  });

  it("依据：这一章还没有依据时指向「待确认」，不指向已经删掉的按钮", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    renderWithApi(<RosterTab />); // 默认 fixture 的 edges 是空的
    (await screen.findByText(hero.name)).click();
    expect(await screen.findByText(/还没有原文依据/)).toBeInTheDocument();
    expect(document.body.textContent).toMatch(/待确认/);
    expect(document.body.textContent).not.toMatch(/记录这句/);
  });

  it("事件超过 6 条封顶滚动，不足 6 条不留空白滚动区", async () => {
    const hero = fixtures.rosterWithCounts.find((n) => n.label === "Character")!;
    const many = Array.from({ length: 8 }, (_, i) => ({
      ...fixtures.characterEvents[0],
      event_id: `event:synth${i}`,
      chapter_number: i + 1,
    }));
    renderWithApi(<RosterTab />, [{ match: /\/characters\/[^/]+\/events$/, body: many }]);
    (await screen.findByText(hero.name)).click();
    // **等的是事件真的渲染出来，不是那一格的标题**：`.lab` 不管 loading 与否
    // 都会先挂上去，拿它当就绪信号会在数据到之前就往下断言（`.char-events-scroll`
    // 那时还没被加上去）——`CharacterTimeline` 顶上那段注释就是在讲这同一种坑。
    // 8 条合成事件共用同一句摘要，只有章号不同——连着章号一起匹配才是唯一的那一条。
    await screen.findByText(new RegExp(`第 ${many.length} 章 · ${many[many.length - 1].summary}`));

    await waitFor(() => expect(document.querySelector(".char-events-scroll")).not.toBeNull());

    document.body.innerHTML = "";
    renderWithApi(<RosterTab />, [
      { match: /\/characters\/[^/]+\/events$/, body: fixtures.characterEvents },
    ]);
    (await screen.findByText(hero.name)).click();
    await screen.findByText(hero.name + "的事件");
    expect(document.querySelector(".char-events-scroll")).toBeNull();
  });
});
