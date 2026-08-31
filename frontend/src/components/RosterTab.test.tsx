import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { RosterTab } from "./RosterTab";

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

    await user.click(within(row).getByRole("button", { name: "删" }));
    // **按下「删」还没删**：先出现一句问话。
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

    await user.click(within(row).getByRole("button", { name: "删" }));
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

  it("点一个人 = 看他的关系图（这条在搬家之后没变）", async () => {
    renderWithApi(<RosterTab />);
    const first = fixtures.rosterWithCounts[0];
    (await screen.findByText(first.name)).click();
    await waitFor(() => {
      expect(useCoords.getState().selectedNodeId).toBe(first.id);
      expect(useCoords.getState().activeTab).toBe("graph");
    });
  });
});
