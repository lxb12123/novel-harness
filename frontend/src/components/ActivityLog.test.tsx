import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { useCoords } from "../store";
import { ActivityLog, money } from "./ActivityLog";

// 喂进来的每一个字节都来自 `api.json`（真 app dump，`tests/test_frontend_contract.py` 冻的）。
// 下面几处「派生」的响应（空页、翻页的第二页、换一行的详情）也全是从那份 dump 拼的，
// **没有一个字段是手写的**——手写夹具等于两份手写的东西互相验证。

const ALL = fixtures.activity.entries.length;
const AUTHOR_ONLY = fixtures.activityAuthorOnly.entries.length;

/** 一条**作者亲手点过、而且今天真的改得掉**的行。
 *
 *  2026-08-24 从「更正认知类型」换成「抽取结果审阅」：前者随秘密下线没了
 *  （ADR 0039），而这几条测试要的从来是「一条带真跳转坐标的 author 行」，
 *  不是「哪条路由写的那一行」。 */
const KNOWLEDGE_ROW = /抽取结果审阅/;

/** 那条行的**第一条** —— 夹具里同名的有三条（一条退到兜底坐标），
 *  而这几条测试要的是「带真跳转坐标」的那一条，它排在最前。 */
const firstRow = async (name: RegExp) =>
  (await screen.findAllByRole("button", { name }))[0];
/** 抽取那一条：展开层里有「跑了什么 + 花了多少」。 */
const RUN_ROW = /第 1 章抽取/;
/** 一次模型调用：第三种展开层（能力 / 模型 / 为哪一章 / token）。 */
const CALL_ROW = /模型调用 · 抽取/;

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    cast: "",
    activeTab: "roster",
    page: "log",
    focusEventId: null,
  });
});

/** 折叠着的那些行（`aria-expanded="false"`）——筛选按钮没有这个属性，不会混进来。 */
const collapsed = () => screen.findAllByRole("button", { expanded: false });

describe("活动记录", () => {
  it("一行一条折叠着，**点开才去取那一条的详情**", async () => {
    // 详情里带着 `decision_log` 的审计信封。跟着列表一起拉 = 打开日志页就把全库
    // 审计内容搬进浏览器，而后端把 payload 排除在折叠层之外正是为了避免这件事。
    const user = userEvent.setup();
    const detailCalls: number[] = [];
    renderWithApi(<ActivityLog />, [
      {
        match: /\/activity\/extraction_run/,
        body: () => {
          detailCalls.push(1);
          return fixtures.activityRunDetail;
        },
      },
    ]);

    expect(await collapsed()).toHaveLength(ALL);
    expect(detailCalls).toHaveLength(0);

    await user.click(await screen.findByRole("button", { name: RUN_ROW }));
    await waitFor(() => expect(detailCalls).toHaveLength(1));
    expect(await screen.findByRole("button", { name: RUN_ROW, expanded: true })).toBeInTheDocument();
  });

  it("展开看得到「跑了什么、结果是什么、花了多少」", async () => {
    const user = userEvent.setup();
    renderWithApi(<ActivityLog />);
    await user.click(await screen.findByRole("button", { name: RUN_ROW }));

    // rows 的措辞全在后端（前端不写文案分支），这里只验它真的渲染成了定义列表。
    expect(await screen.findByText("有效事件")).toBeInTheDocument();
    expect(screen.getByText("待审提案")).toBeInTheDocument();
    // 花销：token 数是真的，钱是「未记录」——**不许渲染成 0**（§10 约束 8）。
    // 顶上那条总账也写着同样的数字，所以这里连模型名一起认，认的是这一步的那一条。
    expect(screen.getByText(/deepseek-v4 · 读入 1200 \/ 生成 400 token/)).toBeInTheDocument();
    expect(screen.getAllByText(/花费 未记录/).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/花费 0|¥0|0 元/);
  });

  it("**审计信封一个字都不上屏** —— 作者看的是 rows，不是给机器重放用的那份", async () => {
    const user = userEvent.setup();
    renderWithApi(<ActivityLog />);
    await user.click(await firstRow(KNOWLEDGE_ROW));

    expect(await screen.findByText("依据引语")).toBeInTheDocument();
    // payload 里真有这些键/值（见 api.json 的 activityDecisionDetail），一个都不许露出来：
    // 它们是引擎内部的东西，而且日志出参本来就是一个全新的泄漏面。
    const shown = document.body.textContent ?? "";
    for (const leak of ["canon_version", "retracted_edge_ids", "edge:ID37", "information_scope"]) {
      expect(shown).not.toContain(leak);
    }
  });

  it("作者做的和系统做的一眼分得开，而且能只看其中一种", async () => {
    const user = userEvent.setup();
    renderWithApi(<ActivityLog />);
    await collapsed();

    // 两种标记同时在场（ADR 0020：系统开始自动往书里写东西了，这件事必须看得见）
    expect(screen.getAllByText("作者").length).toBeGreaterThan(0);
    expect(screen.getAllByText("系统").length).toBeGreaterThan(0);

    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(screen.getByRole("button", { name: /作者做的/ }));

    await waitFor(() => expect(collapsed()).resolves.toHaveLength(AUTHOR_ONLY));
    expect(spy.mock.calls.some(([url]) => String(url).includes("actor=author"))).toBe(true);
    expect(screen.queryByRole("button", { name: RUN_ROW })).toBeNull();
  });

  it("筛掉的那些**还算数**：计数不跟着过滤一起缩", async () => {
    // 这是 ADR 0020 点名要的：自动升 CANON 开了之后 system 行会长得飞快，
    // 作者自己点过的那几十次会被淹没。计数要回答的是「我筛掉了多少」，
    // 跟着过滤一起变就什么都说明不了。
    const user = userEvent.setup();
    renderWithApi(<ActivityLog />);
    await user.click(await screen.findByRole("button", { name: /作者做的/ }));

    await waitFor(() => expect(collapsed()).resolves.toHaveLength(AUTHOR_ONLY));
    for (const tally of fixtures.activityAuthorOnly.actors) {
      // 连 actor 那个词一起认：两个计数可能互为后缀（13 / 3），只认数字会撞上。
      const who = tally.actor === "author" ? "作者做的" : "系统做的";
      expect(
        screen.getByRole("button", { name: new RegExp(`${who} ${tally.count}$`) }),
      ).toBeInTheDocument();
    }
    // 过滤之后列表里只剩 4 行，而「系统」那颗按钮上的数字还是全量的 3。
    expect(screen.getByRole("button", { name: /系统做的 3/ })).toBeInTheDocument();
  });
  it("引擎能改、工作台也能改的那一档，不再挂「入口还没做」那句话", async () => {
    // 这一句 2026-08-10 是诚实的（那两条改正路由在浏览器里零调用方），2026-08-11 起
    // 不是了：矩阵那一格点得开、已确认情节的名单也改得动。**留着它就变成骗人的文案。**
    const user = userEvent.setup();
    renderWithApi(<ActivityLog />);
    await user.click(await firstRow(KNOWLEDGE_ROW));

    await screen.findByText("依据引语");
    expect(screen.queryByText(/只能看这一格|入口还没做/)).toBeNull();
  });

  it("改事件名单那一行，跳过去带的是**后端给的那条事件**", async () => {
    // 名单编辑器按 `jump.event_id` 展开那一条。夹具里同一章的两条情节概要一模一样，
    // 从标题反推必然认错，而认错的产物是作者改了另一条情节的名单。
    const user = userEvent.setup();
    // 同标题同副标题的行不止一条（一次确认接受了一条事件），所以按**行序**取那一条，
    // 不按文字找——这一页的第一条禁令就是「别从字面反推」。
    const at = fixtures.activity.entries.findIndex((e) => e.jump?.target === "event_cast");
    const row = fixtures.activity.entries[at];
    renderWithApi(<ActivityLog />, [
      { match: /\/activity\/decision/, body: { ...fixtures.activityDecisionDetail, entry: row } },
    ]);

    await user.click((await collapsed())[at]);
    await user.click(await screen.findByRole("button", { name: `${row.jump!.label} →` }));

    const s = useCoords.getState();
    expect(s.page).toBe("workbench");
    expect(s.activeTab).toBe("review"); // 已确认情节的名单就在这一格里
    expect(s.focusEventId).toBe(row.jump!.event_id);
    // **跳转不带在场坐标。** 那个字段 2026-08-24 随秘密下线整个删了（ADR 0039）——
    // 它当年只为认知矩阵那一档存在（把一个「本章正文里没被点名」的人加回表上）。
    expect(s.cast).toBe("");
  });

  it("自动升 CANON 的边那一行，跳过去带的是**后端给的那条边**（Task 8）", async () => {
    // `activityCanonEdge` 是真 dump 的「自动升边」决策行：`jump.target = canon_edge`、
    // `jump.edge_id` 由后端填——前端不从那行字里认哪条边。点击后弹起
    // `CanonEdgeEditor`（store 的 `focusEdgeId`），而不是假装它只能定位。
    const user = userEvent.setup();
    const row = fixtures.activityCanonEdge;
    expect(row.jump!.target).toBe("canon_edge");
    expect(row.jump!.edge_id).toBeTruthy();
    expect(row.jump!.endpoints.length).toBeGreaterThan(0);
    // 详情主体真 dump 里没有这一条，所以把「哪一行」换掉——两半都来自 api.json。
    renderWithApi(<ActivityLog />, [
      {
        match: /\/activity(\?|$)/,
        body: { ...fixtures.activity, entries: [row, ...fixtures.activity.entries] },
      },
      { match: /\/activity\/decision/, body: { ...fixtures.activityDecisionDetail, entry: row } },
    ]);

    await user.click((await collapsed())[0]);
    await user.click(await screen.findByRole("button", { name: `${row.jump!.label} →` }));

    const s = useCoords.getState();
    expect(s.page).toBe("workbench");
    expect(s.focusEdgeId).toBe(row.jump!.edge_id);
    // 这一档不带在场坐标、不高亮矩阵那一格。
    expect(s.cast).toBe("");
  });

  it("`endpoints` 为空时说的是「从这儿点不到某一处」，**不是「改不了」**", async () => {
    // 空 `endpoints` 今天有两个意思，而它们在出参形状上长得一模一样：
    // ① 真的没有路由能改（自动升上去的位置/状态边）；
    // ② 有好几条、后端不替作者挑是哪一条（一次升掉一整章的干净事件）——那几条**改得掉**。
    // 从 label 的措辞去分辨就是「从字符串反推」，这一页的第一条禁令。所以措辞必须
    // 两种都成立：说成「改不了」会把 ② 说成没救了，而 ADR 0020 的整条退路就是「改得掉」。
    const user = userEvent.setup();
    // 真 dump 里那一行（`decision:ID32`，endpoints 是空的）+ 真 dump 的详情主体。
    // 夹具只 dump 了两条详情，所以这里把「哪一行」换掉——两半都来自 api.json。
    //
    // **2026-08-11 换过一次样本**：原本用的是「声明认知」那一行，而它其实**改得掉**
    //（`/canon/knowledge` 改的就是这一格上已经存在的那条边，不管当初是声明进来的还是
    // 确认进来的）——它被错归进空 bucket，`tests/test_canon_edit_loop.py::
    // test_the_authors_own_knowledge_declaration_is_not_filed_as_unfixable` 修掉了那一条。
    // 现在用「否决」那一行：被驳回的提案从来没升上 CANON，所以它是①的最硬形态。
    // **按形状找，不按 id 找**：`decision:ID3x` 那串序号是 dump 时按写入顺序编的，
    // 播种链上少一次写（2026-08-14 删掉 `declareKnows` 那一 grab 时就少了一次）
    // 整串就集体前移，而测试会红在一句和它主张毫无关系的话上。
    const emptyEndpoints = fixtures.activity.entries.find(
      (e) => e.id.startsWith("decision:") && e.jump !== null && e.jump.endpoints.length === 0,
    )!;
    expect(emptyEndpoints.jump?.endpoints).toEqual([]);
    renderWithApi(<ActivityLog />, [
      {
        match: /\/activity\/decision/,
        body: { ...fixtures.activityDecisionDetail, entry: emptyEndpoints },
      },
    ]);

    await user.click(await screen.findByRole("button", { name: /否决/ }));
    const note = await screen.findByText(/从这里点不到具体的某一处/);
    expect(note).toBeInTheDocument();
    expect(note.textContent).not.toMatch(/改不了|没救|无法修改|不能改/);
    // 「去第 1 章」仍然点得动（定位是有用的），只是它没有假装自己能改什么。
    expect(screen.getByRole("button", { name: `${emptyEndpoints.jump!.label} →` })).toBeEnabled();
  });

  // ── 没跑成的那一行：这一页上唯一需要作者动手的地方 ────────────────────────
  //
  // 夹具里**从来没有过一条失败的整理行**（`activity` 里三条 run 全是成功的），于是
  // 「失败了屏幕上给什么」在两个运行时的守卫下都扫的是一块永远干净的屏幕。
  // `activityFailed` / `activityFailedDetail` 是真 dump 的那半块。

  it("没跑成的那一条上有一颗**真能点**的按钮，打的就是后端给的那条路", async () => {
    // 在这之前它和跑成了的那些长得一模一样：后端 `_run_jump` 一眼都不看成没成，
    // 于是屏幕上那句红字只配着一颗「去第 N 章 →」——**而重跑的能力后端一直都在**。
    const user = userEvent.setup();
    const page = fixtures.activityFailed;
    const at = page.entries.findIndex((e) => e.jump?.target === "extraction_retry");
    expect(at, "这份 dump 里没有一条没跑成的整理 —— 下面全是空转").toBeGreaterThanOrEqual(0);
    const posts: string[] = [];
    renderWithApi(<ActivityLog />, [
      { match: /\/activity(\?|$)/, body: page },
      {
        match: /\/activity\/extraction_run/,
        body: fixtures.activityFailedDetail,
      },
      {
        method: "POST",
        match: /\/extract/,
        body: () => {
          posts.push("hit");
          return fixtures.extractionRun;
        },
      },
    ]);

    const jump = page.entries[at].jump!;
    await user.click((await collapsed())[at]);
    // 按钮上的字是后端写的（`jump.label`），前端不编第二份措辞。
    const go = await screen.findByRole("button", { name: `${jump.label} →` });

    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(go);
    await waitFor(() => expect(posts).toHaveLength(1));

    // **打的就是 `jump.endpoints[0]`**（pid 在 URL 里是编码过的，所以解回来再比），
    // 而且带着 `force`：不带它，接口照样 202、那一行照旧红着——一颗点了没反应的按钮。
    // 后端那侧有一个「不带 force」的探针钉着同一件事。
    const urls = spy.mock.calls.map(([url]) => decodeURIComponent(String(url)));
    const hit = urls.find((url) => url.startsWith(jump.endpoints[0]));
    expect(hit, `没有一次请求打在 ${jump.endpoints[0]} 上：${urls.join(" ")}`).toBeDefined();
    expect(hit).toContain("force=true");

    // 按完之后说一句人话（**不假装它已经跑完了**：那要过一会儿）。
    expect(await screen.findByText(/已经重新排上队了/)).toBeInTheDocument();
    // 这块屏幕整片扫一遍：失败那一行是这一页上最容易漏出研发术语的地方
    // （它曾经印着 `provider_failure：chapter analysis provider failed`）。
    expect(devTerms(screenText())).toEqual([]);
  });

  it("跑成了的那些行上**没有**那颗按钮", async () => {
    // 反面也得成立：`label` 和 `target` 是后端同一次判断的两个产物，
    // 跑成了就没有「再整理一次」这回事。
    const user = userEvent.setup();
    renderWithApi(<ActivityLog />);
    await user.click(await screen.findByRole("button", { name: RUN_ROW }));

    await screen.findByText("有效事件");
    expect(screen.queryByRole("button", { name: /再整理一次/ })).toBeNull();
  });

  // ── 「模型调用 · 章节总结」那一条 ────────────────────────────────────────

  /** 时间线里写总结那次调用（后端按「库里有没有一行总结指着它」判，不按 capability）。 */
  const summaryRow = () => {
    const at = fixtures.activity.entries.findIndex((e) => e.jump?.target === "summary");
    expect(at, "这份 dump 里没有一条跳去总结的调用 —— 下面全是空转").toBeGreaterThanOrEqual(0);
    return { at, row: fixtures.activity.entries[at] };
  };

  it("章节总结那一条跳的是**右栏那一格**，不是兜底的「去第 N 章」", async () => {
    // 右栏「章节总结」那一格（读 / 改 / 撤回 / 重新生成）是后来才长出来的，而这一条
    // 一直退在兜底坐标上。**跳去哪儿仍然是后端算的**：这里只把它给的枚举翻成 tab。
    const user = userEvent.setup();
    const { at, row } = summaryRow();
    useCoords.setState({ chapter: 5, activeTab: "roster" });
    renderWithApi(<ActivityLog />, [
      { match: /\/activity\/call/, body: fixtures.activitySummaryDetail },
    ]);

    await user.click((await collapsed())[at]);
    await user.click(await screen.findByRole("button", { name: `${row.jump!.label} →` }));

    const s = useCoords.getState();
    expect(s.page).toBe("workbench");
    expect(s.activeTab).toBe("summary");
    expect(s.chapter).toBe(row.jump!.chapter_number);
    // 这一档不带在场坐标（它跳的不是矩阵的一行），也不高亮任何一格。
    expect(s.castInclude).toBe("");
  });

  it("展开一条章节总结，看得见它**到底总结了什么**", async () => {
    // 在这之前那一层只有能力 / 模型 / token / 耗时：花了钱说得清清楚楚，
    // 花出来的东西一个字都没有。正文走投影层（`rows`），**不是把审计信封摊开**。
    const user = userEvent.setup();
    const { at } = summaryRow();
    const detail = fixtures.activitySummaryDetail;
    renderWithApi(<ActivityLog />, [{ match: /\/activity\/call/, body: detail }]);

    await user.click((await collapsed())[at]);

    const line = detail.rows.find((r) => r.label === "这次写出来的总结");
    expect(line, "夹具里这一条详情没带总结正文 —— 下面那句断言会变成空转").toBeDefined();
    expect(await screen.findByText(line!.label)).toBeInTheDocument();
    expect(screen.getByText(line!.value)).toBeInTheDocument();
    // 信封照旧一个字都不上屏（`payload` 在这一档本来就是 null，这里钉的是那条纪律）。
    expect(devTerms(screenText())).toEqual([]);
  });

  it("「看更早的」把后端那个游标**原样**回传，不自己拼", async () => {
    const user = userEvent.setup();
    const cursor = fixtures.activity.next_cursor!;
    renderWithApi(<ActivityLog />, [
      // 第二页：真 dump 的形状，entries 空 —— 翻到底就是这样。
      { match: /cursor=/, body: { ...fixtures.activity, entries: [], next_cursor: null } },
    ]);
    await collapsed();

    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(screen.getByRole("button", { name: "看更早的" }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(([url]) =>
          String(url).includes(`cursor=${encodeURIComponent(cursor)}`),
        ),
      ).toBe(true),
    );
    // 到底了就不再摆一个点了没反应的按钮
    await waitFor(() => expect(screen.queryByRole("button", { name: "看更早的" })).toBeNull());
    expect(await collapsed()).toHaveLength(ALL); // 已经读到的那些还在
  });

  // ── 用量条：那个数是不是全部 ──────────────────────────────────────────────
  //
  // 这一格 2026-08-12 之前是 `读入 {t.tokens_in} / 生成 {t.tokens_out} token`，
  // 而后端那侧是 `COALESCE(SUM(tokens_in), 0)`——**供应商不报 usage 的调用被当成 0
  // 加了进去**，于是屏幕上出现一个看起来确定、其实是「我们不知道」的数字。
  // 同一条上的 `cost` 那一格早就渲染成「未记录」还带一句为什么，两种口径并排摆了一轮。
  //
  // 三档各吃一份**真 dump**（`runsAllReported` / `runs` / `runsUnreported`，
  // 由 `tests/test_frontend_contract.py` 在三个不同的时刻从真 app 抓的）：
  // 夹具里只躺一种形状的样本，这几条断言扫的就是一块永远长一个样的屏幕。

  const RUNS = (body: unknown) => [{ match: /\/runs(\?|$)/, body }];

  it("全报了：直接给数", async () => {
    const t = fixtures.runsAllReported.totals;
    // 先验夹具真的是这一档 —— 否则下面那句断言测的是我以为的形状，不是后端给的。
    expect(t.metered_calls).toBe(t.calls);
    renderWithApi(<ActivityLog />, RUNS(fixtures.runsAllReported));

    expect(await screen.findByText(/读入 1200 \/ 生成 400 token/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/没报/);
  });

  it("报了一部分：合计照给，**同时说清它不是全部**", async () => {
    const t = fixtures.runs.totals;
    expect(t.metered_calls).toBeGreaterThan(0);
    expect(t.metered_calls).toBeLessThan(t.calls);
    renderWithApi(<ActivityLog />, RUNS(fixtures.runs));

    // 报了的那次的 1200 是真信息 —— 丢掉它是同一种假话的另一个方向。
    expect(await screen.findByText(/读入 1200 \/ 生成 400 token/)).toBeInTheDocument();
    // 而「这个数不是全部」必须在屏幕上，不是只挂在 title 里。
    expect(screen.getByText(/另有 1 次没报，实际更多/)).toBeInTheDocument();
  });

  it("一次都没报：说「未记录」，**绝不说 0**", async () => {
    const t = fixtures.runsUnreported.totals;
    expect(t.metered_calls).toBe(0);
    expect(t.tokens_in).toBeNull();
    renderWithApi(<ActivityLog />, RUNS(fixtures.runsUnreported));

    expect(await screen.findByText("用量未记录")).toBeInTheDocument();
    // **这就是被修掉的那句话**：作者按 README 的默认配置（DeepSeek）写书、花着真钱，
    // 而这条用量条告诉他「读入 0 token」。
    expect(document.body.textContent).not.toMatch(/读入 0|生成 0 token/);
  });

  // ── 花费那一格：**和旁边的用量是同一对形状** ──────────────────────────────
  //
  // 这一格 2026-08-13 之前只有一句 `花费 {money(t.cost)}`，而那个合计只算得上
  // `priced_calls` 那几次。旁边的 token 那一格早就照「合计 + 其中几条算得出」写了，
  // 只有这一格没跟上——于是它是一个**不说自己缺了几行**的合计。
  // 三档各吃一份真 dump（`runsAllReported` / `runsPartlyPriced` / `runs`）。

  it("全都算得出：直接给数，**而且带「约」字**", async () => {
    const t = fixtures.runsAllReported.totals;
    // 先验夹具真的是这一档 —— 否则下面那句测的是我以为的形状，不是后端给的。
    expect(
      t.priced_calls,
      "这份 dump 里还没有一次算得出价钱的调用（重生成夹具之后才有）",
    ).toBe(t.calls);
    renderWithApi(<ActivityLog />, RUNS(fixtures.runsAllReported));

    expect(await screen.findByText(/花费 约 \$/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/算不出/);
  });

  it("只算得出一部分：合计照给，**同时说清它不是全部**", async () => {
    const panel = fixtures.runsPartlyPriced;
    const t = panel.totals;
    expect(t.priced_calls).toBeGreaterThan(0);
    expect(t.priced_calls).toBeLessThan(t.calls);
    renderWithApi(<ActivityLog />, RUNS(panel));

    const unpriced = t.calls - t.priced_calls;
    expect(
      await screen.findByText(new RegExp(`另有 ${unpriced} 次算不出，实际更多`)),
    ).toBeInTheDocument();
    // 算得出的那几次是真信息 —— 丢掉它是同一种假话的另一个方向。
    expect(document.body.textContent).toMatch(/花费 约 \$/);
  });

  it("一次都算不出：说「未记录」，**绝不说 $0.00**", async () => {
    const t = fixtures.runs.totals;
    expect(t.priced_calls).toBe(0);
    renderWithApi(<ActivityLog />, RUNS(fixtures.runs));

    expect(await screen.findByText("花费未记录")).toBeInTheDocument();
    // 一张写着 0 元的账单是本仓反复在修的那种失败形态（漂亮的空结果 + 200）。
    expect(document.body.textContent).not.toMatch(/\$0\.00|花费 0/);
  });

  it("还没有记录时说人话，不摆一张空表", async () => {
    renderWithApi(<ActivityLog />, [
      {
        match: /\/activity(\?|$)/,
        body: { ...fixtures.activity, entries: [], next_cursor: null, actors: [] },
      },
    ]);
    expect(await screen.findByText(/系统整理过这本书之后/)).toBeInTheDocument();
  });

  // ── 界面上不摆研发术语 ────────────────────────────────────────────────
  //
  // **这条断言原本是一张词表**（`valid_from|canon_version|PROVISIONAL|endpoints|
  // payload|decision_log`），而后端当时正把 `萧决 在「青云城主府」` 这类原话
  // 印在这一页上——**那两个词恰好不在表里，于是它绿了一整轮**。补两个词进去只会让
  // 下一个词接着漏，所以判据换成了形状，且那套判据全仓只有一份
  //（`src/test/screenGuard.ts`，自守卫钉着五个真的上过屏的违规）。
  //
  // 三档展开层各验一遍：**少一档就有一整块屏幕没人看过**。这不是假想——
  // 「跑了什么」那一档 2026-08-11 之前印着 `snapshot:ID11` 和一段 prompt 指纹，
  // 「花了多少」那一档印着两个 `artifact:sha256:…` 和一整段 `params_json`。

  it("折叠着的那一页上，一个研发术语都没有", async () => {
    renderWithApi(<ActivityLog />);
    await collapsed();
    expect(devTerms(screenText())).toEqual([]);
    // 反面也得成立：这一页**说得出**发生过什么（过度收窄一样是 bug）。
    expect(document.body.textContent).toContain("青云城主府");
  });

  it.each([
    ["改了什么（确认那一档）", KNOWLEDGE_ROW, "依据引语"],
    ["跑了什么（整理那一档）", RUN_ROW, "有效事件"],
    ["花了多少（模型调用那一档）", CALL_ROW, "第几次尝试"],
  ])("展开「%s」也一样", async (_label, row, marker) => {
    const user = userEvent.setup();
    renderWithApi(<ActivityLog />);
    await user.click(await firstRow(row));
    await screen.findByText(marker);
    expect(devTerms(screenText())).toEqual([]);
  });
});

describe("钱那一格 —— 它是估算，不是账单", () => {
  it("算得出时必须带「约」字", () => {
    expect(money(0.34)).toBe("约 $0.34");
  });

  it("小到显示不出来时说「不到」，不说「约 $0.00」", () => {
    // 「约 $0.00」读起来像免费，而它不是 —— 一次起草大约就是这个量级。
    expect(money(0.0012)).toBe("不到 $0.01");
  });

  it("算不出来时是「未记录」，**绝不是 0**", () => {
    // 一张写着 0 元的账单是本仓反复在修的那种失败形态（漂亮的空结果 + 200）。
    expect(money(null)).toBe("未记录");
    expect(money(null)).not.toContain("0");
  });
});
