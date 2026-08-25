import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { RosterTab } from "./RosterTab";

/** 花名册里那几行的名字，**按屏幕上的先后**。排序断言全靠它。 */
function namesOnScreen(): string[] {
  return [...document.querySelectorAll<HTMLElement>(".item .nm")].map(
    (el) => el.textContent ?? "",
  );
}

/** **每一组**内部的名字，按屏幕上的先后。
 *
 *  排序是**组内**的，不是全局的：花名册先按 label 分组（人物 / 地点 / …），
 *  次数只决定组内谁在前面。拿全局序去断言会红在一个根本没坏的地方。 */
function namesPerGroup(): string[][] {
  return [...document.querySelectorAll<HTMLElement>(".grp")].map((grp) =>
    [...grp.querySelectorAll<HTMLElement>(".item .nm")].map((el) => el.textContent ?? ""),
  );
}

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
});

describe("花名册", () => {
  it("按 label 分组，人名一个不落", async () => {
    renderWithApi(<RosterTab />);
    for (const n of fixtures.rosterWithCounts) {
      expect(await screen.findByText(n.name)).toBeInTheDocument();
    }
  });

  // ══════════════════════════════════════════════════════════════════════
  // 出场章数 + 排序（2026-08-25）
  //
  // 抽取从这一天起认不出就建人物（ADR 0020 补记），一次性称呼会大量涌进花名册。
  // 按名字排的话主角和路人混在一起，所以组内改成按出场频率降序。
  // ══════════════════════════════════════════════════════════════════════

  it("每一行带出场章数，且它和花名册同一条出参回来", async () => {
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

  it("组内按出场次数降序，一颗按钮能倒过来", async () => {
    const user = userEvent.setup();
    renderWithApi(<RosterTab />);
    await screen.findByText(fixtures.rosterWithCounts[0].name);

    const counts = new Map(
      fixtures.rosterWithCounts.map((n) => [n.name, n.appearance_chapters]),
    );
    const numbers = (group: string[]) => group.map((name) => counts.get(name) ?? -1);

    const desc = namesPerGroup().map(numbers);
    for (const group of desc) {
      expect(group).toEqual([...group].sort((a, b) => b - a));
    }
    // 自守卫：**至少有一组里的数不全相同**，否则上面那条永远绿。
    expect(desc.some((group) => new Set(group).size > 1)).toBe(true);

    await user.click(screen.getByRole("button", { name: /出场多/ }));
    for (const group of namesPerGroup().map(numbers)) {
      expect(group).toEqual([...group].sort((a, b) => a - b));
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

  it("删不掉的时候，把后端那句话原样摆出来", async () => {
    // **不许在这儿写一句「删不掉这个条目」**：后端写的是「「北荒」还被引用着
    // （关系 1 / 情节 0），先把那几条改掉再删他」——那是给作者的话。
    // 盖掉它就是又造出第二个措辞源（`correctionError.ts` 顶上那段注释）。
    const user = userEvent.setup();
    const target = fixtures.rosterWithCounts[0];
    const said = `「${target.name}」还被引用着（关系 1 / 情节 0），先把那几条改掉再删他`;
    renderWithApi(<RosterTab />, [
      {
        method: "DELETE",
        match: /\/nodes\//,
        status: 409,
        body: { detail: { error: "node_in_use", message: said, usage: { edges: 1, events: 0 } } },
      },
    ]);
    const row = (await screen.findByText(target.name)).closest(".item") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "删" }));
    await user.click(within(row).getByRole("button", { name: "删掉" }));

    expect(await screen.findByText(said)).toBeInTheDocument();
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

  it("空花名册只给一个清楚的下一步，不解释内部实现", async () => {
    renderWithApi(<RosterTab />, [{ match: /\/roster$/, body: [] }]);
    expect(await screen.findByText(/添加第一个条目/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/导入只切章|认知矩阵|规则|ADR/);
  });

  it("点「建第一个」能开出建条目的抽屉 —— 空态那句话必须真的有出口", async () => {
    renderWithApi(<RosterTab />, [{ match: /\/roster$/, body: [] }]);
    (await screen.findByText(/添加第一个条目/)).click();
    expect(await screen.findByRole("heading", { name: "花名册" })).toBeInTheDocument();
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
