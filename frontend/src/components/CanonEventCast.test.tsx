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
    // 顶上那句「已确认的情节 · 到第 N 章为止」2026-09-05 删了（作者点名）。范围现在
    // 靠**每一章一个黑体抬头**说清楚，所以就绪信号换成那个抬头。
    expect(await screen.findByText(`第 ${first.event.chapter_number} 章`)).toBeInTheDocument();
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
    // 角色册里的人物都在候选里（地点 / 秘密不在——名单两维收的都是人物）。
    expect(within(knowers).getByRole("checkbox", { name: "未来大能" })).not.toBeChecked();
    expect(within(knowers).queryByRole("checkbox", { name: "青云城主府" })).toBeNull();
    expect(within(knowers).queryByRole("checkbox", { name: "血脉秘密" })).toBeNull();
    expect(screen.getByText(/不会重复添加/)).toBeInTheDocument();
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

    expect(await screen.findByText(/先查看最新版本/)).toBeInTheDocument();
    expect(posts(spy)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "查看最新版本" })).toBeInTheDocument();
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
    expect(await screen.findByText(/未找到该情节/)).toBeInTheDocument();
  });

  it("「分析本章」在这一栏的工具栏上，是一颗图标 —— 点它就发那一次分析", async () => {
    // 它 2026-09-05 从「通知」那一格搬过来（作者定的位置：排序和放大镜中间）。
    // 那儿原来是一颗写着字的按钮 + 一句常驻的「分析：已完成 · 发现 7 条情节」；
    // 这一栏的规矩是**图标 + 悬浮**（同排序 / 放大镜），结果**飘一下就走**
    // （同「检验规则」那颗闪电）。
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      { method: "POST", match: /\/chapters\/\d+\/extract/, body: fixtures.extractionRun },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    const analyze = await screen.findByRole("button", { name: "分析本章" });

    // 三颗图标的**顺序**是作者点名定的：排序 → 分析 → 按章号找。
    const bar = analyze.closest(".ev-bar") as HTMLElement;
    const icons = Array.from(bar.querySelectorAll("button")).map((b) =>
      b.getAttribute("aria-label"),
    );
    expect(icons).toEqual(["改成正序", "分析本章", "按章号找"]);
    // 悬浮那份说明和无障碍名字是同一句话（不是原生 title —— 那个要等约一秒）。
    expect(analyze.getAttribute("data-tip")).toBe("分析本章");
    expect(analyze.getAttribute("title")).toBeNull();

    await user.click(analyze);
    await waitFor(() =>
      expect(
        posts(spy as unknown as Calls).some((c) => /\/chapters\/\d+\/extract/.test(String(c[0]))),
      ).toBe(true),
    );
  });

  it("分析跑完那一刻，这张单子自己重取——不用换一次 tab 才看见", async () => {
    // 2026-09-13 之前只轮询不重取：作者点完「分析本章」看到一句「整理出 3 条情节」，
    // 眼前的单子却还是空的。判据是「在眼皮底下从跑着变成跑完」：POST 回的是 PENDING，
    // 轮询回 SUCCEEDED，那一刻情节 / 角色册 / 通知这一套全部重取。
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      {
        method: "POST",
        match: /\/chapters\/\d+\/extract/,
        body: { ...fixtures.extractionRun, status: "PENDING" },
      },
      { match: /\/extractions\//, body: fixtures.extractionRun },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await screen.findAllByRole("button", { name: first.event.summary });
    const listReads = () =>
      (spy as unknown as Calls).mock.calls.filter(([url]) =>
        /\/events\?scope=CANON/.test(String(url)),
      ).length;
    const before = listReads();

    await user.click(screen.getByRole("button", { name: "分析本章" }));
    expect(await screen.findByText(/整理出 3 条情节/)).toBeInTheDocument();
    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
  });

  it("分析连 run 都没建出来（POST 500）也要说一句，不许只闪一下", async () => {
    // 作者 2026-09-13（桌面版）：「我点击了这个也没有反应画面就闪一下，我都不知道现在是
    // 成功了还是失败了」——那次后端 500（书出生时没领到 ruleset 基线），按钮从灰变金黄
    // 再变回灰，屏幕上一个字都没有。
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      {
        method: "POST",
        match: /\/chapters\/\d+\/extract/,
        status: 500,
        body: { detail: "Internal Server Error" },
      },
    ]);
    await user.click(await screen.findByRole("button", { name: "分析本章" }));
    expect(await screen.findByText(/本章分析未能开始/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Internal Server Error/);
  });

  it("忙态跟着后台那一行走：这一章在 running 里，按钮就是忙的——换个 tab 回来也一样", async () => {
    // 作者 2026-09-13：「一旦我切换到角色栏，或者通知那边再返回这个状态就丢失」。
    // 忙态不再只看自己那次 POST 回的 run，还看 `/background`（顶栏那盏灯读的同一份）。
    useCoords.setState({ chapter: 12 });
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      { match: /\/background$/, body: { configured: true, running: [12], queued: [] } },
    ]);
    const analyze = await screen.findByRole("button", { name: "分析本章" });
    await waitFor(() => expect(analyze).toBeDisabled());
    expect(analyze).toHaveClass("busy");
    expect(analyze.getAttribute("data-tip")).toBe("分析中…");
  });

  it("上一次失败的 run 原样回来时，自动带 force 再发一次——第二次点击不许没反应", async () => {
    // 不带 force 的 POST 见到已经失败的同一条 run 会原样还回来（202、status FAILED）；
    // 从前「说过的 run 不再说」的守卫让这一下悄无声息。
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      {
        method: "POST",
        match: /\/extract\?force=true/,
        body: { ...fixtures.extractionRun, id: "extraction_run:RETRY", status: "PENDING" },
      },
      {
        method: "POST",
        match: /\/chapters\/\d+\/extract$/,
        body: { ...fixtures.extractionRun, status: "FAILED", errors: ["model_unreachable"] },
      },
      { match: /\/extractions\/extraction_run:RETRY$/, body: { ...fixtures.extractionRun, id: "extraction_run:RETRY" } },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(await screen.findByRole("button", { name: "分析本章" }));
    await waitFor(() =>
      expect(
        posts(spy as unknown as Calls).some((c) => /force=true/.test(String(c[0]))),
      ).toBe(true),
    );
    expect(await screen.findByText(/整理出 3 条情节/)).toBeInTheDocument();
  });

  it("失败那句话留着、带一个 ×；成功的那句四秒就走", async () => {
    // 作者 2026-09-13：「他报错停留时间太短了，然后就消失了」。
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      {
        method: "POST",
        match: /\/chapters\/\d+\/extract/,
        body: { ...fixtures.extractionRun, status: "PENDING" },
      },
      {
        match: /\/extractions\//,
        body: { ...fixtures.extractionRun, status: "FAILED", errors: ["model_unreachable"] },
      },
    ]);
    await user.click(await screen.findByRole("button", { name: "分析本章" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/本章分析未完成/);
    expect(alert).toHaveClass("sticky");
    await user.click(within(alert).getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("模型服务没配好：空态先说「先连接模型」，「分析本章」那颗按钮按下去是去配、不是去跑", async () => {
    const user = userEvent.setup();
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      { match: /\/chapters\/\d+\/events\?scope=CANON/, body: [] },
      { match: /\/api\/settings$/, body: fixtures.settings }, // model_configured: false
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    expect(await screen.findByText("事件由模型从正文中整理，需先连接模型服务：")).toBeInTheDocument();
    const analyze = screen.getByRole("button", { name: "分析本章" });
    await waitFor(() => expect(analyze.getAttribute("data-tip")).toBe("先连接模型"));
    await user.click(analyze);
    expect(useCoords.getState().settingsOpen).toBe("link");
    expect(posts(spy as unknown as Calls)).toHaveLength(0);
  });

  it("这一章一条都没有的时候说人话，不摆一张空表", async () => {
    renderWithApi(<CanonEventCast canonVersion={6} />, [
      { match: /\/chapters\/\d+\/events\?scope=CANON/, body: [] },
    ]);
    // **范围是「到这一章为止」，不是「这一章」**：这一格读的是 `valid_from <= 当前章`，
    // 空态那句话跟着说清楚（作者 2026-09-04 问的就是这个范围）。
    expect(await screen.findByText(/截至第 \d+ 章尚无已确认的情节/)).toBeInTheDocument();
  });
});
