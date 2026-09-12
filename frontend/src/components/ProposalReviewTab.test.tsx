import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import type { EventView } from "../api/types";
import { useCoords } from "../store";
import { ProposalReviewTab } from "./ProposalReviewTab";

// 右栏「事件」（2026-08-31 之前叫「待确认」）。审阅提案（接受/驳回/改一改）和
// 从正文发现的情节勾选确认都搬进了通知面板——见 `SystemNotifications.test.tsx`。
// 这个组件现在只剩一件事：包一层 `canonVersion` 的取值，交给 `CanonEventCast`
// 画已确认的情节。深的行为（在场/知情名单能改、乐观闸怎么走）钉在
// `CanonEventCast.test.tsx` 里，这里不重复。

const views = fixtures.eventsCanon as unknown as EventView[];
const first = views[0];

describe("事件（已确认）", () => {
  it("渲染已确认的情节，不是空态", async () => {
    useCoords.setState({ projectId: "project:ID1", chapter: 1, focusEventId: null });
    renderWithApi(<ProposalReviewTab />);
    // 夹具里两条情节的概要是同一句话（同 `CanonEventCast.test.tsx`），只认「至少有一个」。
    expect((await screen.findAllByRole("button", { name: first.event.summary })).length).toBeGreaterThan(0);
  });

  it("待确认那一摞在「通知」，不在这里；「分析本章」反过来 —— 它搬到这儿来了", async () => {
    // 两次搬家方向相反，别把它们记成一件事：
    //   · 2026-08-31 待确认的**卡片**搬去「通知」（那一格现在只剩已确认的情节）；
    //   · 2026-09-05 「分析本章」那颗按钮从「通知」搬**到这一栏的工具栏**上
    //     （作者定的位置：排序和放大镜中间，图标 + 悬浮）。
    // 这条测试从前只断言前一半，于是它在第二次搬家那天变成了反的。
    useCoords.setState({ projectId: "project:ID1", chapter: 1, focusEventId: null });
    renderWithApi(<ProposalReviewTab />);
    await screen.findAllByRole("button", { name: first.event.summary });
    expect(screen.getByRole("button", { name: "分析本章" })).toBeInTheDocument();
    expect(screen.queryByText(/待确认/)).toBeNull();
    expect(screen.queryByText(/从正文发现的情节/)).toBeNull();
    // 那句常驻的「分析：已完成 · 发现 N 条情节」跟着按钮一起没了 —— 结果现在是
    // 一句飘几秒就走的话（同「检验规则」那颗闪电）。
    expect(screen.queryByText(/^分析：/)).toBeNull();
  });
});
