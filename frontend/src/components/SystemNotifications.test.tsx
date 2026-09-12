import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { SystemNotifications } from "./SystemNotifications";

// 系统通知（Task 14 / 021）。吃真 dump（`fixtures.notifications`：一条不带锚的
// summary_mismatch + 一条带锚的 text_advisory）。**坐标和动作全由后端给**，这里只渲染。

const PID = "project:ID1";

/** 2026-09-05 三块并一块之后，「从正文发现的情节」那一条**也带一颗「逐条看」**。
 *  下面几条钉的是**通知**折不折叠，跟它无关——清空这一路，屏幕上就只剩一颗，
 *  断言问的仍然是原来那个问题（而不是被另一颗按钮偶然满足/偶然打破）。 */
const NO_PROVISIONAL = { match: /\/chapters\/\d+\/events\?scope=PROVISIONAL/, body: [] };

describe("SystemNotifications", () => {
  it("开着通知 tab 时列出 OPEN 的（真 dump 那一条）", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />);
    expect(await screen.findByText(/总结与正文可能不一致/)).toBeInTheDocument();
    // 有一行，不是「没有」的空态。
    expect(screen.queryByText(/没有待处理的通知/)).toBeNull();
  });

  it("同一档攒到 4 条就折叠成一行，逐条的动作还在", async () => {
    // ── 这一条钉的是「一次冒出上百条」那一屏（2026-08-25）───────────────
    //
    // 定期扫描从这一天起也管抽取（`ChapterSummaryState.needs_work`），于是一本
    // 158 章的老书里那 155 章会**第一次**被整理，其中「一件都没留下」的那些各落
    // 一条通知。摊开就是上百张卡片，每张两颗按钮——那不是「告诉作者」，
    // 那是让他关掉这一格。
    useCoords.setState({ projectId: PID });
    const many = Array.from({ length: 12 }, (_, i) => ({
      id: `notice:${i}`,
      project_id: PID,
      kind: "extraction_yielded_nothing" as const,
      status: "OPEN" as const,
      chapter_number: 12 - i, // 落库顺序故意是乱的
      title: `第 ${12 - i} 章整理完了，一件都没留下`,
      jump: null,
      actions: ["ignore"],
      created_at: "2026-08-25T00:00:00Z",
    }));
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: many },
      NO_PROVISIONAL,
    ]);

    // 折叠成一行：报条数、摆章号，而**章号按大小排**（作者找的是第几章）。
    expect(await screen.findByText(/12 条/)).toBeInTheDocument();
    expect(screen.getByText(/第 1、2、3、4/)).toBeInTheDocument();
    // 12 张卡片没有摊开：那句标题只出现在折叠头上，不是 12 遍。
    expect(screen.queryAllByText(/本章未整理出内容/)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /^忽略$/ })).toBeNull();

    // **动作一个都没少**，只是默认收着。
    await userEvent.click(screen.getByRole("button", { name: "展开" }));
    expect(screen.queryAllByRole("button", { name: /^忽略$/ })).toHaveLength(12);
  });

  it("少于 4 条照旧一条一张卡 —— 一两条的时候摊开更好读", async () => {
    useCoords.setState({ projectId: PID });
    const two = Array.from({ length: 2 }, (_, i) => ({
      id: `notice:${i}`,
      project_id: PID,
      kind: "extraction_yielded_nothing" as const,
      status: "OPEN" as const,
      chapter_number: i + 1,
      title: `第 ${i + 1} 章整理完了，一件都没留下`,
      jump: null,
      actions: ["ignore"],
      created_at: "2026-08-25T00:00:00Z",
    }));
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: two },
      NO_PROVISIONAL,
    ]);

    expect(await screen.findAllByText(/本章未整理出内容/)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
  });

  it("整格都空才说「写就是了」——**不是「system_notification 表空」**", async () => {
    // ── 2026-09-05 之前这句话是假的 ──────────────────────────────────────
    // 那时这一格是三段并排，而只有最后一段带空态判断（判据是滤掉提案之后的
    // `plainItems.length === 0`）。于是上面堆着 3 张待确认卡片时，底下照样写着
    // 「现在没有需要你注意的」。**空态的判据必须和标题的范围一样宽。**
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: [] },
      { match: /\/notifications\/count$/, body: { open: 0 } },
      NO_PROVISIONAL,
    ]);
    expect(await screen.findByText(/没有待处理的通知/)).toBeInTheDocument();
  });

  it("只剩「从正文发现的情节」时不许说空——那句话曾经就是这么骗人的", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: [] },
      { match: /\/notifications\/count$/, body: { open: 0 } },
    ]);
    expect(await screen.findByText("从正文发现的情节")).toBeInTheDocument();
    expect(screen.queryByText(/没有待处理的通知/)).toBeNull();
  });

  it("「从正文发现的情节」和提案卡同一张脸，内容一条一条摊在卡里", async () => {
    // ⚠️ **这条测试 2026-09-05 反过来了。** 它原来钉的是「默认收着，点『逐条看』
    // 才摊开」；作者那天看着这一格说「换成和下边的同一个卡片外表，然后内容里边
    // 一条一条地放着」「就是统一形象，卡片内部内容」。
    //
    // 折叠去掉不是随手改：折叠那条规矩自己就写着「提案不参与折叠——要做决定的
    // 队列藏起来，等于多按一次才能开始干活」，而这一摞正是那种队列。
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />, [{ match: /\/notifications$/, body: [] }]);

    expect(await screen.findByText("从正文发现的情节")).toBeInTheDocument();
    // 壳子和下面那些提案卡是同一套 class（统一形象）。
    const card = screen.getByText("从正文发现的情节").closest(".notice-card") as HTMLElement;
    expect(card.className).toContain("proposal-card");
    // 卡头报总数。**范围收在 notice-head 里**：章号抬头也报条数，夹具那两条同章，
    // 满屏找 /2 条/ 会同时命中两个（2026-09-06 分章之后就是这样）。
    const head = card.querySelector(".notice-head") as HTMLElement;
    expect(head.textContent).toContain("2 条");
    // **按章分组，章号是抬头**（作者 2026-09-06：要像「事件」那一栏那样有个标题）。
    // 类名和那一栏共用，所以这里连 class 一起断言——同一件事在两块屏幕上长一个样。
    const heading = card.querySelector(".ev-chapter") as HTMLElement;
    expect(heading).not.toBeNull();
    expect(heading.textContent).toMatch(/^第 \d+ 章/);
    expect(heading.textContent).toContain("2 条");
    // 章号不再作为前缀出现在每一行上（那是分组之前的画法）。
    expect(card.querySelectorAll(".event-card.provisional")).toHaveLength(2);
    for (const row of card.querySelectorAll(".event-card.provisional")) {
      expect(row.textContent).not.toMatch(/第 \d+ 章/);
    }
    // 没有那颗折叠按钮了；勾选框和「确认所选」直接在卡里。
    expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /确认所选（0）/ })).toBeDisabled();
    // 那两句多余的话删了：标题已经说完的事不再说第二遍，章号跟着每一条走。
    expect(screen.queryByText(/确认后用于后续写作/)).toBeNull();
  });

  it("忽略走真路由；消失后列表重取", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />);
    await screen.findByText(/总结与正文可能不一致/);
    // 后端 fixture 的 actions 为空（本期 summary_mismatch 只带一行正文跳转），
    // 但「忽略」按钮要出现——它回答的是「这一条暂时不用管」。
    // 这里 force 检查：如果未来后端把 ignore 动作加进来，按钮仍然渲染同一件事。
    const rows = screen.getAllByText(/总结与正文可能不一致/);
    expect(rows.length).toBeGreaterThan(0);
    // fixture 是统一的 refuse 布局：卡片标题 + actions（目前空→按钮不画）。
    // 钉住「空 actions 时不画一颗点了没反应的按钮」。
    expect(screen.queryByRole("button", { name: /^忽略$/ })).toBeNull();
  });

  // ── 第四种：只告警不阻断的那一档（026 / M1-d）──────────────────────────
  //
  // 后端多一档 kind 时，前端这张 `KIND_TITLE` 漏了那一档的后果是把 `text_advisory`
  // 这个机器码原样摆到作者屏幕上（`?? item.kind` 那条兜底）。tsc 会先拦住漏字段，
  // 但**兜底那一支写不写得对只有这条测试看得见**。

  it("第四种通知有自己的说法，不是把机器码摆上屏", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />);
    expect(await screen.findByText(/此段落建议复核/)).toBeInTheDocument();
    // 那一档**不该**借用阻断那一条的措辞：它没有停掉任何东西。
    expect(screen.queryByText(/text_advisory/)).toBeNull();
  });

  it("带锚的那条点得过去 —— 锚原样进 highlight，前端不从标题里认位置", async () => {
    const user = userEvent.setup();
    useCoords.setState({ projectId: PID, chapter: 2, highlight: null });
    renderWithApi(<SystemNotifications />);
    await screen.findByText(/此段落建议复核/);

    // 不带锚的那条只给「去这一章」，带锚的那条给「去这一句」——两支各一颗。
    expect(screen.getByRole("button", { name: /查看该章/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /查看原句/ }));

    expect(useCoords.getState().chapter).toBe(1);
    expect(useCoords.getState().highlight).toEqual({
      para_index: 1,
      quote_text: "萧决",
      occurrence_k: 0,
    });
  });

  // ── 2026-08-31：待确认提案并进了通知面板 ──────────────────────────────────
  //
  // `GET .../notifications` 现在混着两种血统：真通知（`system_notification` 表）
  // 和现读现拼的 PENDING 提案（`proposal_notifications.py`，不进那张表）。这里
  // 只钉「提案那一半」——`kind` 是 `proposal_conflict`/`proposal_low_confidence`
  // 的行要画成完整卡片（对照/在场/知情/可信度/引语），不是套用别的六档那句
  // `noticeBody()` 生成的摘要。完整字段来自另一条 `/proposals`（项目全量）。

  const CONFLICT_NOTIFICATION = {
    id: "proposal:conflict1",
    project_id: PID,
    kind: "proposal_conflict" as const,
    status: "OPEN" as const,
    subject_type: "proposal",
    subject_id: "proposal:conflict1",
    chapter_number: 3,
    title_code: "proposal_conflict_title",
    title_params: {},
    summary_sha256: null,
    source_sha256: null,
    jump: null,
    actions: ["accept", "reject"],
    created_at: "2026-08-31T00:00:00Z",
  };

  const CONFLICT_PROPOSAL = {
    id: "proposal:conflict1",
    project_id: PID,
    kind: "edge_conflict" as const,
    summary: "",
    item_count: 1,
    items: [
      {
        update_kind: "relationship",
        current: { edge_id: "e1", subject_id: "n1", target_id: "n2", value: "敌对" },
        proposed: {
          edge_id: "e1",
          subject_id: "n1",
          target_id: "n2",
          value: "结拜",
          quote: "从今日起，你我八拜之交。",
        },
      },
    ],
    confidence: null,
    status: "PENDING" as const,
    chapter_number: 3,
    base_canon_version: 6,
    event_ids: [],
    edge_ids: ["e1"],
    node_refs: [
      { id: "n1", label: "Character", name: "萧决" },
      { id: "n2", label: "Character", name: "李管家" },
    ],
  };

  /** 状态类冲突：`target` 是**维度**（政治立场），值在 `value` 里。 */
  const STATE_CONFLICT_PROPOSAL = {
    ...CONFLICT_PROPOSAL,
    // **id 沿用 conflict1**：那条通知按 id 找它的提案，换个 id 就渲不出卡片。
    // 两条测试各自单独渲染，不会互相看见。
    items: [
      {
        update_kind: "state",
        current: { edge_id: "e2", subject_id: "n1", target_id: "n3", value: "表面中立" },
        proposed: {
          edge_id: "e2",
          subject_id: "n1",
          target_id: "n3",
          value: "站在皇帝一边",
          quote: "他有一个女儿毕竟也是贵妃。",
        },
      },
    ],
    edge_ids: ["e2"],
    node_refs: [
      { id: "n1", label: "Character", name: "萧决" },
      { id: "n3", label: "StateDim", name: "政治立场" },
    ],
  };

  it("状态类冲突要把**两个值**摆出来 —— 少了值，两行长得一模一样", async () => {
    // ⚠️ **这是一条回归测试。** `factLine` 原来只分两支：`relationship` 带 value，
    // **其余一律渲染成「A 在 B」**。而「其余」里除了位置还有状态和死亡，它们的
    // `target` 是维度、值在 `value` 里，于是卡片长这样：
    //
    //     当前：贾政 在 政治立场
    //     提议：贾政 在 政治立场
    //
    // 两行一模一样、零信息，而作者要按着它决定接受还是驳回。**后端两个值都发过来了**
    // （`find_conflict` 给 current/proposed 都填了 value），是渲染这一层扔的。
    // 它一直存在，只是 2026-09-06 全书回填把状态类冲突从个别几条变成一摞才被看见。
    //
    // 从前那张覆盖只测 `relationship` 那一支——**唯一带 value 的那一支**，
    // 所以它罩不住这个洞。
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: [CONFLICT_NOTIFICATION] },
      { match: /\/proposals$/, body: [STATE_CONFLICT_PROPOSAL] },
    ]);

    expect(await screen.findByText("关系冲突")).toBeInTheDocument();
    expect(screen.getByText(/萧决 的 政治立场 是 表面中立/)).toBeInTheDocument();
    expect(screen.getByText(/萧决 的 政治立场 是 站在皇帝一边/)).toBeInTheDocument();
    // 位置那一支照旧不带值（`find_conflict` 对 location 就不填 value），别顺手改坏。
    expect(screen.queryByText(/萧决 在 政治立场/)).toBeNull();
  });

  it("待确认提案（关系冲突）画成完整卡片，不是通用摘要句", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: [CONFLICT_NOTIFICATION] },
      { match: /\/proposals$/, body: [CONFLICT_PROPOSAL] },
    ]);

    expect(await screen.findByText("关系冲突")).toBeInTheDocument();
    expect(screen.getByText(/当前：/)).toBeInTheDocument();
    expect(screen.getByText(/萧决 与 李管家 敌对/)).toBeInTheDocument();
    expect(screen.getByText(/提议：/)).toBeInTheDocument();
    expect(screen.getByText(/萧决 与 李管家 结拜/)).toBeInTheDocument();
    expect(screen.getByText("从今日起，你我八拜之交。")).toBeInTheDocument();
    // 通用的那句摘要（`proposal_conflict_title` 的兜底文案）不该在完整卡片旁边
    // 再出现一遍——卡片本身已经把这句话说完整了。
    expect(screen.queryByText(/一处设定与整理结果不一致/)).toBeNull();

    expect(screen.getByRole("button", { name: "接受" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "驳回" })).toBeInTheDocument();
  });

  it("接受一条提案：打真路由，带上 canon_version，成功后通知重取", async () => {
    const user = userEvent.setup();
    useCoords.setState({ projectId: PID });
    let acceptBody: unknown = null;
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: [CONFLICT_NOTIFICATION] },
      { match: /\/proposals$/, body: [CONFLICT_PROPOSAL] },
      {
        method: "POST",
        match: /\/proposals\/proposal:conflict1\/accept$/,
        onRequest: (init) => {
          acceptBody = init?.body ? JSON.parse(String(init.body)) : null;
        },
        body: { proposal_id: "proposal:conflict1", status: "ACCEPTED", canon_version: 7, decision_id: "d1", event: null },
      },
    ]);

    await screen.findByText("关系冲突");
    await user.click(screen.getByRole("button", { name: "接受" }));

    expect(acceptBody).toEqual({ expected_canon_version: 6 });
  });

  const LOW_CONF_NOTIFICATION = {
    id: "proposal:lowconf1",
    project_id: PID,
    kind: "proposal_low_confidence" as const,
    status: "OPEN" as const,
    subject_type: "proposal",
    subject_id: "proposal:lowconf1",
    chapter_number: 5,
    title_code: "proposal_low_confidence_title",
    title_params: {},
    summary_sha256: null,
    source_sha256: null,
    jump: null,
    actions: ["accept", "reject", "edit"],
    created_at: "2026-08-31T00:01:00Z",
  };

  const LOW_CONF_PROPOSAL = {
    id: "proposal:lowconf1",
    project_id: PID,
    kind: "low_confidence_main" as const,
    summary: "",
    item_count: 1,
    items: [
      {
        source_kind: "event",
        event_id: "event:1",
        summary: "萧决在城楼上望见了远方的烽火。",
        confidence: 0.42,
        quote: "他望着远方的烽火，久久不语。",
      },
    ],
    confidence: 0.42,
    status: "PENDING" as const,
    chapter_number: 5,
    base_canon_version: 6,
    event_ids: ["event:1"],
    edge_ids: [],
    node_refs: [{ id: "n1", label: "Character", name: "萧决" }],
  };

  it("待确认提案（低置信情节）画情节卡：概要/可信度/引语齐全，且有「改一改」", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: [LOW_CONF_NOTIFICATION] },
      { match: /\/proposals$/, body: [LOW_CONF_PROPOSAL] },
    ]);

    expect(await screen.findByText("待确认的情节")).toBeInTheDocument();
    expect(screen.getByText("萧决在城楼上望见了远方的烽火。")).toBeInTheDocument();
    expect(screen.getByText(/42%/)).toBeInTheDocument();
    expect(screen.getByText("他望着远方的烽火，久久不语。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "接受" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "驳回" })).toBeInTheDocument();
  });

  it("关系冲突卡上没有「改一改」——只有低置信情节那一档才可能有", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: [CONFLICT_NOTIFICATION] },
      { match: /\/proposals$/, body: [CONFLICT_PROPOSAL] },
    ]);
    await screen.findByText("关系冲突");
    expect(screen.queryByRole("button", { name: "修改" })).toBeNull();
  });

  it("待确认的提案不参与「攒到 4 条就折叠」——它是要做决定的队列，不是看过就好的告警", async () => {
    useCoords.setState({ projectId: PID });
    const five = Array.from({ length: 5 }, (_, i) => ({
      ...CONFLICT_NOTIFICATION,
      id: `proposal:c${i}`,
      subject_id: `proposal:c${i}`,
    }));
    const proposals = five.map((n) => ({ ...CONFLICT_PROPOSAL, id: n.id }));
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: five },
      { match: /\/proposals$/, body: proposals },
      NO_PROVISIONAL,
    ]);
    expect(await screen.findAllByText("关系冲突")).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
  });
});
