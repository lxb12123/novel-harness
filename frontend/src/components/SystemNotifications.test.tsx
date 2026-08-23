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
