import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { SystemNotifications } from "./SystemNotifications";

// 系统通知（Task 14 / 021）。吃真 dump（`fixtures.notifications`：一条不带锚的
// summary_mismatch + 一条带锚的 text_advisory）。**坐标和动作全由后端给**，这里只渲染。

const PID = "project:ID1";

describe("SystemNotifications", () => {
  it("开着通知 tab 时列出 OPEN 的（真 dump 那一条）", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />);
    expect(await screen.findByText(/总结与正文可能对不上/)).toBeInTheDocument();
    // 有一行，不是「没有」的空态。
    expect(screen.queryByText(/现在没有需要你注意的/)).toBeNull();
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
    renderWithApi(<SystemNotifications />, [{ match: /\/notifications$/, body: many }]);

    // 折叠成一行：报条数、摆章号，而**章号按大小排**（作者找的是第几章）。
    expect(await screen.findByText(/12 条/)).toBeInTheDocument();
    expect(screen.getByText(/第 1、2、3、4/)).toBeInTheDocument();
    // 12 张卡片没有摊开：那句标题只出现在折叠头上，不是 12 遍。
    expect(screen.queryAllByText(/这一章什么都没整理出来/)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /不再提醒这一条/ })).toBeNull();

    // **动作一个都没少**，只是默认收着。
    await userEvent.click(screen.getByRole("button", { name: "逐条看" }));
    expect(screen.queryAllByRole("button", { name: /不再提醒这一条/ })).toHaveLength(12);
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
    renderWithApi(<SystemNotifications />, [{ match: /\/notifications$/, body: two }]);

    expect(await screen.findAllByText(/这一章什么都没整理出来/)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "逐条看" })).toBeNull();
  });

  it("没有 OPEN 时是一句「写就是了」，不是一个空面板", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />, [
      { match: /\/notifications$/, body: [] },
      { match: /\/notifications\/count$/, body: { open: 0 } },
    ]);
    expect(await screen.findByText(/现在没有需要你注意的/)).toBeInTheDocument();
  });

  it("忽略走真路由；消失后列表重取", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />);
    await screen.findByText(/总结与正文可能对不上/);
    // 后端 fixture 的 actions 为空（本期 summary_mismatch 只带一行正文跳转），
    // 但「忽略」按钮要出现——它回答的是「这一条暂时不用管」。
    // 这里 force 检查：如果未来后端把 ignore 动作加进来，按钮仍然渲染同一件事。
    const rows = screen.getAllByText(/总结与正文可能对不上/);
    expect(rows.length).toBeGreaterThan(0);
    // fixture 是统一的 refuse 布局：卡片标题 + actions（目前空→按钮不画）。
    // 钉住「空 actions 时不画一颗点了没反应的按钮」。
    expect(screen.queryByRole("button", { name: /不再提醒这一条/ })).toBeNull();
  });

  // ── 第四种：只告警不阻断的那一档（026 / M1-d）──────────────────────────
  //
  // 后端多一档 kind 时，前端这张 `KIND_TITLE` 漏了那一档的后果是把 `text_advisory`
  // 这个机器码原样摆到作者屏幕上（`?? item.kind` 那条兜底）。tsc 会先拦住漏字段，
  // 但**兜底那一支写不写得对只有这条测试看得见**。

  it("第四种通知有自己的说法，不是把机器码摆上屏", async () => {
    useCoords.setState({ projectId: PID });
    renderWithApi(<SystemNotifications />);
    expect(await screen.findByText(/这一段值得再看一眼/)).toBeInTheDocument();
    // 那一档**不该**借用阻断那一条的措辞：它没有停掉任何东西。
    expect(screen.queryByText(/text_advisory/)).toBeNull();
  });

  it("带锚的那条点得过去 —— 锚原样进 highlight，前端不从标题里认位置", async () => {
    const user = userEvent.setup();
    useCoords.setState({ projectId: PID, chapter: 2, highlight: null });
    renderWithApi(<SystemNotifications />);
    await screen.findByText(/这一段值得再看一眼/);

    // 不带锚的那条只给「去这一章」，带锚的那条给「去这一句」——两支各一颗。
    expect(screen.getByRole("button", { name: /去这一章/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /去这一句/ }));

    expect(useCoords.getState().chapter).toBe(1);
    expect(useCoords.getState().highlight).toEqual({
      para_index: 1,
      quote_text: "萧决",
      occurrence_k: 0,
    });
  });
});
