import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import type { EventView } from "../api/types";
import { useCoords } from "../store";
import { CanonEventCast } from "./CanonEventCast";

// 已生效情节的知情 / 在场名单 —— ADR 0020 押的那条退路的另一半。
//
// `knowers` 是抽取里唯一靠推断得来的一维（谁在场是文本里写着的，谁**因此知道了**是猜的），
// 而认知边界正是这个产品唯一的独家价值。自动生效之后它最可能出错，所以它必须改得掉。
//
// 喂进来的每一个字节都来自 `api.json`（真 app dump）。

const views = fixtures.eventsCanon as unknown as EventView[];
const first = views[0];

beforeEach(() =>
  useCoords.setState({ projectId: "project:ID1", chapter: 1, focusEventId: null }),
);

/** 展开第一条情节的名单编辑器。两条情节的概要在夹具里是同一句话，所以按序号取。 */
async function openFirst(user: ReturnType<typeof userEvent.setup>) {
  const heads = await screen.findAllByRole("button", { name: first.event.summary });
  await user.click(heads[0]);
  return heads[0];
}

/** `vi.spyOn(globalThis, "fetch")` 的调用记录，结构化收窄（`MockInstance` 的泛型
 *  在 fetch 上对不齐，而这里只需要「参数数组的数组」）。 */
type Calls = { mock: { calls: unknown[][] } };

const posts = (spy: Calls) =>
  spy.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
const lastBody = (spy: Calls) =>
  JSON.parse(String((posts(spy).at(-1)![1] as RequestInit).body));

describe("已确认的情节：谁在场、谁知道了", () => {
  it("先看得见 —— 在此之前 CANON 事件在浏览器里一个字都没露过面", async () => {
    renderWithApi(<CanonEventCast canonVersion={6} />);
    expect(await screen.findByText(/已确认的情节/)).toBeInTheDocument();
    const card = (await screen.findAllByRole("button", { name: first.event.summary }))[0]
      .closest(".event-card") as HTMLElement;
    const line = within(card).getByText(/^在场：/).parentElement!;
    for (const n of first.participants) expect(line.textContent).toContain(n.name);
    // 研发术语一个都不许露：这一格上的词是「在场」「知道这件事的」。
    expect(document.body.textContent).not.toMatch(/CANON|PROVISIONAL|knower|participant|event_id/);
  });

  it("勾选框里就是**现在的名单** —— 勾上的那些等于「改完之后是这些人」", async () => {
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />);
    await openFirst(user);

    const knowers = screen.getByRole("group", { name: "知道这件事的人" });
    for (const n of first.knowers) {
      expect(within(knowers).getByRole("checkbox", { name: n.name })).toBeChecked();
    }
    // 花名册里的人物都在候选里（地点 / 秘密不在——名单两维收的都是人物）。
    expect(within(knowers).getByRole("checkbox", { name: "未来大能" })).not.toBeChecked();
    expect(within(knowers).queryByRole("checkbox", { name: "青云城主府" })).toBeNull();
    expect(within(knowers).queryByRole("checkbox", { name: "血脉秘密" })).toBeNull();
    expect(screen.getByText(/不会把同一个人加两遍/)).toBeInTheDocument();
  });

  it("什么都没动的时候保存不了 —— 那一次编辑后端会判空，日志里也不该多一条", async () => {
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />);
    await openFirst(user);

    expect(screen.getByRole("button", { name: "保存名单" })).toBeDisabled();
    const knowers = screen.getByRole("group", { name: "知道这件事的人" });
    await user.click(within(knowers).getByRole("checkbox", { name: first.knowers[1].name }));
    expect(screen.getByRole("button", { name: "保存名单" })).toBeEnabled();
  });

  it("发出去的是**绝对集合**，而且只发动过的那一维", async () => {
    // `null` = 这一维不动。把没动过的名单也发过去，日志里就多一条「改了在场」
    // 而其实一个人都没变——`decision_log` 是只增不改的。
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />);
    const spy = vi.spyOn(globalThis, "fetch");
    await openFirst(user);
    const knowers = screen.getByRole("group", { name: "知道这件事的人" });
    await user.click(within(knowers).getByRole("checkbox", { name: first.knowers[1].name }));
    await user.click(screen.getByRole("button", { name: "保存名单" }));

    await waitFor(() => expect(posts(spy)).toHaveLength(1));
    expect(decodeURIComponent(String(posts(spy)[0][0]))).toContain(
      `/canon/events/${first.event.id}/cast`,
    );
    expect(lastBody(spy)).toEqual({
      knower_ids: [first.knowers[0].id],
      participant_ids: null,
      expected_canon_version: 6,
    });
  });

  it("「别处刚改过」要作者再看一眼，**不静默重试**", async () => {
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      {
        method: "POST",
        match: /\/canon\/events\/.*\/cast$/,
        status: 409,
        body: fixtures.errorStaleCanon,
      },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await openFirst(user);
    const knowers = screen.getByRole("group", { name: "知道这件事的人" });
    await user.click(within(knowers).getByRole("checkbox", { name: first.knowers[1].name }));
    await user.click(screen.getByRole("button", { name: "保存名单" }));

    expect(await screen.findByText(/先看一眼最新的/)).toBeInTheDocument();
    expect(posts(spy)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "看看最新的" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("stale_base_version");
  });

  it("从活动记录跳过来时，**后端指的那一条**自己就展开了", async () => {
    // 坐标是 `jump.event_id`，不是从那行字里认的 —— 夹具里两条情节的概要一模一样，
    // 按字认必然认错，而认错的产物是作者改了另一条情节的名单。
    useCoords.setState({ focusEventId: views[1].event.id });
    renderWithApi(<CanonEventCast canonVersion={6} />);

    const heads = await screen.findAllByRole("button", { name: first.event.summary });
    await waitFor(() => expect(heads[1]).toHaveAttribute("aria-expanded", "true"));
    expect(heads[0]).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByRole("group")).toHaveLength(2); // 只有那一条展开着
  });

  it("跳过来却找不到那一条时，说出来", async () => {
    // §10 约束 8：静默的零和真的零不许长得一样。它可能已经被撤回，或者不在这一章。
    useCoords.setState({ focusEventId: "event:NOT_HERE" });
    renderWithApi(<CanonEventCast canonVersion={6} />);
    expect(await screen.findByText(/没有在这一章找到刚才那条情节/)).toBeInTheDocument();
  });

  it("这一章一条都没有的时候说人话，不摆一张空表", async () => {
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      { match: /\/chapters\/\d+\/events\?scope=CANON/, body: [] },
    ]);
    expect(await screen.findByText(/这一章还没有已确认的情节/)).toBeInTheDocument();
  });
});
