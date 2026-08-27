import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { messageForCode } from "../backendMessages";
import { useCoords } from "../store";
import { ProposalReviewTab } from "./ProposalReviewTab";

const open = () => {
  useCoords.getState().setProject("project:ID1");
  useCoords.getState().setChapter(1);
  renderWithApi(<ProposalReviewTab />);
};

/** `vi.spyOn(globalThis, "fetch")` 的调用记录（同 `CanonEventCast.test.tsx` 那一份）。 */
type Calls = { mock: { calls: unknown[][] } };

const postsTo = (spy: Calls, tail: string) =>
  spy.mock.calls.filter(
    ([url, init]) =>
      (init as RequestInit | undefined)?.method === "POST" && String(url).endsWith(tail),
  );
const bodyOf = (call: unknown[]) => JSON.parse(String((call[1] as RequestInit).body));

describe("待确认内容", () => {
  it("渲染待确认的冲突卡：当前 vs 提议 + 引语", async () => {
    open();
    const card = (await screen.findByText(/关系冲突/)).closest(".statecard") as HTMLElement;
    expect(within(card).getByText(/当前：/)).toBeInTheDocument();
    expect(within(card).getByText(/提议：/)).toBeInTheDocument();
    const conflict = fixtures.proposals.find((p) => p.kind === "edge_conflict");
    const item = (conflict!.items as { proposed: { quote: string } }[])[0];
    expect(within(card).getByText(item.proposed.quote)).toBeInTheDocument();
  });

  it("需要确认的情节卡渲染概要、在场人名、可信程度与证据引语", async () => {
    open();
    const card = (await screen.findByText(/^需要确认的情节/)).closest(".statecard") as HTMLElement;
    expect(within(card).getByText(/可信程度 60%/)).toBeInTheDocument();
    expect(within(card).getByText(/在场：萧决、李管家/)).toBeInTheDocument();
    expect(
      within(card).getByText(/萧决在青云城主府第一次听说了血脉秘密的真相/),
    ).toBeInTheDocument();
  });

  it("旧的「新人物」提案不再画卡片，但队列里也不静默少掉它们", async () => {
    // **`new_character` 那一支 2026-08-25 整个不画了**（ADR 0020 补记）：抽取认不出
    // 就直接建人物，这类提案不会再有新的。
    //
    // 这一条同时钉住另一半：真书里还有 22 条 2026-08-15 攒下来的 PENDING 行，
    // **只是滤掉它们 = 静默的零**（§10 约束 8）——作者会看到一个比实际短的队列，
    // 而没有一处说过差额去哪了。所以那一支不画卡片，但要报一句数。
    const retired = fixtures.proposals.filter(
      (p) => p.kind === "new_character" && p.status === "PENDING",
    );
    expect(retired.length).toBeGreaterThan(0); // 自守卫：夹具里没有就永远绿

    open();
    await screen.findByText(/^需要确认的情节/);
    // 卡片没了：那两颗只有这一支才有的按钮，屏幕上一个都不许有。
    expect(screen.queryByRole("button", { name: "接受为角色" })).toBeNull();
    expect(screen.queryByRole("button", { name: "标为路人" })).toBeNull();
    // 但那几条被说出来了。
    expect(
      screen.getByText(new RegExp(`还有 ${retired.length} 条旧的「新人物」待确认`)),
    ).toBeInTheDocument();
  });

  it("审阅成功后刷新提案/事件/花名册/状态查询", async () => {
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const user = userEvent.setup();
    open();
    const card = (await screen.findByText(/^需要确认的情节/)).closest(".statecard") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "接受" }));
    expect(invalidate).toHaveBeenCalled();
    const calls = invalidate.mock.calls.flatMap((c) => c as { queryKey?: unknown[] }[]);
    const keys = calls.map((c) => c?.queryKey?.[0]).filter(Boolean);
    for (const prefix of ["proposals", "events", "roster", "state"]) {
      expect(keys).toContain(prefix);
    }
    invalidate.mockRestore();
  });

  it("从正文发现的情节带未确认标记，可勾选批量确认", async () => {
    const user = userEvent.setup();
    open();
    const checkboxes = await screen.findAllByRole("checkbox");
    expect(checkboxes.length).toBeGreaterThan(0);
    const labels = screen.getAllByText(/未确认/);
    expect(labels.length).toBeGreaterThan(0);
    await user.click(checkboxes[0]);
    expect(screen.getByRole("button", { name: /确认所选（1）/ })).toBeEnabled();
  });

  it("冲突卡上的**名字是后端给的**，不是拿 id 去花名册里查出来的", async () => {
    // 夹具里 `/roster` 是**在这个地点被建出来之前** dump 的，所以 `location:ID22`
    // 不在里面——而这正是真实失败的形状：花名册（`["roster", pid]`）和队列
    // （`["proposals", pid, chapter]`）是两条独立缓存，后台整理造出的新节点会在
    // 前者里缺席一拍。当时的兜底是 `id.slice(-6)` → 屏幕上一个 `n:ID22`。
    open();
    await screen.findByText(/关系冲突/);
    const rosterIds = new Set(fixtures.roster.map((n) => n.id));
    expect(rosterIds.has("location:ID22")).toBe(false); // 自守卫：这个样本还差着那一拍
    expect(document.body.textContent).toContain("青云城");
  });

  it("后端认不出的那个 id 说「—」，**不许把内部编号截短了摆上屏**", async () => {
    // `node_refs` 里没有它 = 后端认不出（不存在 / 跨项目）。这不是「后端忘了给」，
    // 后端不编假名字；界面这时也不许自己编一个「看起来像名字」的东西出来。
    const conflict = fixtures.proposals.find((p) => p.kind === "edge_conflict")!;
    renderWithApi(<ProposalReviewTab />, [
      {
        match: /\/chapters\/\d+\/proposals/,
        body: [{ ...conflict, node_refs: [] }],
      },
    ]);
    const card = (await screen.findByText(/关系冲突/)).closest(".statecard") as HTMLElement;
    expect(within(card).getByText(/当前：—/)).toBeInTheDocument();
    expect(devTerms(card.textContent ?? "")).toEqual([]);
  });

  it("不展示任何原始 items_json 或 item_count 字段", async () => {
    open();
    await screen.findByText(/关系冲突/);
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("items_json");
    expect(text).not.toContain("proposal_set");
  });

  it("分析失败后可以重新分析，且状态使用中文", async () => {
    const user = userEvent.setup();
    const { failed, succeeded } = analysisRuns();
    useCoords.getState().setProject("project:ID1");
    useCoords.getState().setChapter(1);
    renderWithApi(<ProposalReviewTab />, extractionRoutes());

    await user.click(screen.getByRole("button", { name: "分析本章" }));
    const retry = await screen.findByRole("button", { name: "重新分析" });
    await user.click(retry);
    expect(await screen.findByText(/分析：已完成/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新分析" })).toBeNull();
    expect(document.body.textContent).not.toMatch(/FAILED|SUCCEEDED|run|抽取|提案/);
    expect(failed.status).toBe("FAILED"); // 自守卫：夹具真的是那两档
    expect(succeeded.status).toBe("SUCCEEDED");
  });

  it("一次**没跑成**的整理：屏幕上是人话，不是英文诊断", async () => {
    // ── 这条测试存在的理由 ──────────────────────────────────────────────────
    //
    // 这块屏幕上原来渲染的是 `e.message` —— `ExtractionRunError.message` 是写给
    // **维护者**的英文诊断（`extract/control.py` 明写「它永远不上作者的屏幕」），
    // 于是小说作者看到的是 `provider_failure: chapter analysis provider failed`。
    //
    // **它没被任何守卫抓到，因为这一格喂的是一份手写的失败**：
    // `{ code: "analysis_format", message: "格式错误" }` —— 一个真码配一句**中文**。
    // 测试绿着，屏幕是英文。现在夹具里有一条**真的**没跑成的整理
    //（`tests/test_frontend_contract.py` 从真 app dump），这一格吃的就是它。
    //
    // 国际化第四批·笔二起 `errors` 是 `ExtractionErrorCode` 的原始值，不是拼好的
    // 中文——组件拿它去 `messageForCode("run_error", ...)` 查 `RUN_ERROR_LABEL`。
    // 这里断言的是**渲染出来的那句话**，不是原始码本身。
    const user = userEvent.setup();
    const { failed } = analysisRuns();
    useCoords.getState().setProject("project:ID1");
    useCoords.getState().setChapter(1);
    renderWithApi(<ProposalReviewTab />, extractionRoutes());

    await user.click(screen.getByRole("button", { name: "分析本章" }));
    await screen.findByRole("button", { name: "重新分析" });

    expect(failed.errors.length).toBeGreaterThan(0); // 自守卫：没有原因就没什么可验的
    for (const code of failed.errors) {
      expect(typeof code).toBe("string"); // `{code, message}` 回来了就红
      const rendered = messageForCode("run_error", "zh", { code });
      expect(rendered).toBeDefined(); // 认不出的码 = RUN_ERROR_LABEL 漏了行
      expect(screen.getByText(rendered!)).toBeInTheDocument();
      expect(rendered).toMatch(/[一-鿿]/);
    }
    expect(devTerms(screenText())).toEqual([]);
  });
});

/** 契约夹具里那两条真的整理运行：跑成的 / 没跑成的。**一个字节都不手写。** */
function analysisRuns() {
  return { failed: fixtures.extractionFailed, succeeded: fixtures.extractionRun };
}

/** 「分析本章 → 失败 → 重新分析 → 成功」这条路上的四条桩。
 *  id 从夹具里取，所以轮询打到哪一条由真 dump 决定，不由这里编的字符串决定。 */
function extractionRoutes() {
  const { failed, succeeded } = analysisRuns();
  return [
    { method: "POST", match: /\/extract\?force=true/, body: succeeded },
    { method: "POST", match: /\/extract$/, body: failed },
    { method: "GET", match: new RegExp(`/extractions/${succeeded.id}`), body: succeeded },
    { method: "GET", match: /\/extractions\//, body: failed },
  ];
}

// ══════════════════════════════════════════════════════════════════════════
// 「改一改再收下」（`POST …/proposals/{id}/edit`）
// ══════════════════════════════════════════════════════════════════════════
//
// 在此之前这一格只有「接受」和「驳回」：抽取说「这场戏里李管家也知情」而其实他不知情时，
// 作者只能整条丢掉（这一章的情节记录就空了，证据链跟着丢）或整条收下（把一条假事实放进
// 这个产品唯一在卖的那张表）。**引擎和路由一直是通的**，缺的只有这一格。

describe("改一改再收下", () => {
  /** 那条低置信提案的事件视图（真夹具）：知情人有两个，正好够去掉一个。 */
  const eventOf = () => {
    const proposal = fixtures.proposals.find((p) => p.kind === "low_confidence_main")!;
    const view = fixtures.eventsProvisional.find(
      (v) => v.event.id === proposal.event_ids[0],
    )!;
    return { proposal, view };
  };

  it("低置信情节卡上有第三个动作，而它旁边那两个还在", async () => {
    open();
    const card = (await screen.findByText(/^需要确认的情节/)).closest(".statecard") as HTMLElement;
    for (const name of ["接受", "改一改", "驳回"]) {
      expect(within(card).getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("冲突卡上**没有**这个动作 —— 后端只对「恰好 1 个 event」开放", async () => {
    // `extract/proposal_validation.py`：edit 只允许恰好 1 个 event，且不得含
    // edge / new_character。画一颗点下去只会撞 422 的按钮，比没有按钮更糟。
    //（新人物卡那一半 2026-08-25 随那一支一起没了，见上面那条。）
    open();
    await screen.findByText(/关系冲突/);
    const card = screen.getByText(/关系冲突/).closest(".statecard") as HTMLElement;
    expect(within(card).queryByRole("button", { name: "改一改" })).toBeNull();
  });

  it("勾选框里就是**现在这条提案上的名单**，花名册里的人物也在候选里", async () => {
    const user = userEvent.setup();
    const { view } = eventOf();
    open();
    const card = (await screen.findByText(/^需要确认的情节/)).closest(".statecard") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "改一改" }));

    const knowers = screen.getByRole("group", { name: "知道这件事的人" });
    for (const n of view.knowers) {
      expect(within(knowers).getByRole("checkbox", { name: n.name })).toBeChecked();
    }
    // 花名册里的人物进候选，地点 / 秘密不进（名单两维收的都是人物）。
    expect(within(knowers).getByRole("checkbox", { name: "未来大能" })).not.toBeChecked();
    expect(within(knowers).queryByRole("checkbox", { name: "青云城主府" })).toBeNull();
    expect(screen.getByText(/不会把同一个人加两遍/)).toBeInTheDocument();
  });

  it("去掉一个知情人再收下：打的是 /edit，发的是**改完之后的整份名单**", async () => {
    const user = userEvent.setup();
    const { proposal, view } = eventOf();
    const dropped = view.knowers[view.knowers.length - 1];
    const kept = view.knowers.slice(0, -1).map((n) => n.id);
    expect(kept.length).toBeGreaterThan(0); // 自守卫：这条事件得有得可去
    open();
    const spy = vi.spyOn(globalThis, "fetch");

    const card = (await screen.findByText(/^需要确认的情节/)).closest(".statecard") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "改一改" }));
    const knowers = screen.getByRole("group", { name: "知道这件事的人" });
    await user.click(within(knowers).getByRole("checkbox", { name: dropped.name }));
    await user.click(within(card).getByRole("button", { name: "改完收下" }));

    await waitFor(() => expect(postsTo(spy, "/edit")).toHaveLength(1));
    const call = postsTo(spy, "/edit")[0];
    expect(decodeURIComponent(String(call[0]))).toContain(`/proposals/${proposal.id}/edit`);
    // **绝对集合**：发的是「改完之后是这些人」，不是「删掉谁」。
    // 没动过的那两维发 `null`，否则 `decision_log` 里会多出一条什么都没改的记录。
    expect(bodyOf(call)).toEqual({
      edited_summary: null,
      knower_ids: kept,
      participant_ids: null,
      expected_canon_version: expect.any(Number),
    });
  });

  it("改概要也走同一条路；一个字都没动时按钮点不了", async () => {
    const user = userEvent.setup();
    open();
    const spy = vi.spyOn(globalThis, "fetch");

    const card = (await screen.findByText(/^需要确认的情节/)).closest(".statecard") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "改一改" }));
    const submit = within(card).getByRole("button", { name: "改完收下" });
    // 什么都没改 = 不是一次编辑（后端 422）。**在按下去之前就拦住**。
    expect(submit).toBeDisabled();

    const box = within(card).getByRole("textbox", { name: "这件事怎么说" });
    await user.clear(box);
    // 空概要同样不是一次编辑，而且它有话说（§10 约束 8：零带着理由）。
    expect(submit).toBeDisabled();
    expect(within(card).getByText(/整条不要的话用「驳回」/)).toBeInTheDocument();

    await user.type(box, "萧决只是听见了半句");
    await user.click(submit);

    await waitFor(() => expect(postsTo(spy, "/edit")).toHaveLength(1));
    expect(bodyOf(postsTo(spy, "/edit")[0])).toMatchObject({
      edited_summary: "萧决只是听见了半句",
      knower_ids: null,
      participant_ids: null,
    });
  });

  it("收下之后刷新提案/事件/花名册/状态查询（同 accept：canon 版本一样往前走一格）", async () => {
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const user = userEvent.setup();
    const { view } = eventOf();
    open();
    const card = (await screen.findByText(/^需要确认的情节/)).closest(".statecard") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "改一改" }));
    const knowers = screen.getByRole("group", { name: "知道这件事的人" });
    await user.click(within(knowers).getByRole("checkbox", { name: view.knowers[0].name }));
    await user.click(within(card).getByRole("button", { name: "改完收下" }));

    await waitFor(() => {
      const keys = invalidate.mock.calls
        .flatMap((c) => c as { queryKey?: unknown[] }[])
        .map((c) => c?.queryKey?.[0])
        .filter(Boolean);
      for (const prefix of ["proposals", "events", "roster", "state", "projects"]) {
        expect(keys).toContain(prefix);
      }
    });
    invalidate.mockRestore();
  });

  it("这一格上一个研发术语都没有（含展开的编辑器）", async () => {
    const user = userEvent.setup();
    open();
    const card = (await screen.findByText(/^需要确认的情节/)).closest(".statecard") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "改一改" }));
    screen.getByRole("group", { name: "知道这件事的人" });
    expect(devTerms(screenText())).toEqual([]);
  });
});
