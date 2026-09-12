import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { CharacterStatus } from "./CharacterStatus";

// 角色卡里的「状态」那一格。**这一格 2026-09-04 之前一条组件测试都没有**——
// 它的两条纪律各自坏掉时都不会红：
//
// 1. **基础资料在这一格里，不另开一格**（作者裁定：「基础是状态里面的一个子集」）。
//    它取的是 `…/profile` 那条路由的 `profile` 字段，而前端曾经把它整个扔掉
//    （`api/types.ts` 里 `profile: unknown`）——扔掉的时候屏幕上只是少几行，
//    没有任何断言会红。
// 2. **「第 N 章起」+「（最新）」**：排序键必须是屏幕上印着的那个数。
//
// 契约夹具里 `profile` 那四项全是 null（demo 库没跑过抽取），所以第一条只能靠
// 「拿真 dump 改字段」来测——同这个仓库其余测试的规矩，不手写一份快照。

const PID = "project:ID1";
const HERO = "character:ID9";

const profileWith = (patch: Record<string, unknown>) => ({
  ...fixtures.characterProfile,
  profile: { ...fixtures.characterProfile.profile, ...patch },
});

/** 这一格 2026-09-06 起读的是 `state_history`（每格可能有多条），不是 `states`
 *  （每格只有当前那一条）。两个字段都塞上：`states` 还喂着「所在地 / 已亡」那两行。 */
const stateWith = (history: unknown[]) => ({
  ...fixtures.characterState,
  states: history,
  state_history: history,
});

const dim = (name: string, value: string, since: number) => ({
  dim: { id: `statedim:${name}`, label: "StateDim", name },
  dim_key: null,
  value,
  value_key: null,
  since_chapter: since,
});

describe("CharacterStatus", () => {
  it("基础资料排在这一格里：有值的印出来，空的不占一行", async () => {
    useCoords.setState({ projectId: PID, chapter: 2 });
    renderWithApi(<CharacterStatus characterId={HERO} />, [
      {
        match: /\/characters\/[^/]+\/profile$/,
        body: profileWith({ gender: "男", personality: "睚眦必报" }),
      },
    ]);

    expect(await screen.findByText(/性别.*男/)).toBeInTheDocument();
    expect(screen.getByText(/性格.*睚眦必报/)).toBeInTheDocument();
    // 空的那两项不占一行：四项全空是常态，摆四行「未记录」就是给作者看空表单。
    expect(screen.queryByText(/出身/)).toBeNull();
    expect(screen.queryByText(/备注/)).toBeNull();
    // **基础没有「第 N 章起」**：它存在人物节点上，一本书一份，没有时态。
    expect(screen.getByText(/性别.*男/).textContent).not.toMatch(/章起/);
  });

  it("**一个字段一行，行内一串带章号的值**，同格里最新那个打「（最新）」", async () => {
    // ⚠️ **这一格 2026-09-06 重排过。** 从前是一行一个值、按章号平铺
    // （40 行「字段：值 · 第 N 章起」），作者原话：「状态下一个字段就对应多个可填，
    // 然后最新的有个最新标记就好，然后要带章节，这样会美观清晰一些」。
    //
    // 重排要的是**历史**，所以这一格换成读 `state_history`——按 `states` 分组的话
    // 每格只有当前那一条，40 个各含一条的组，屏幕上一个字都不会变。
    useCoords.setState({ projectId: PID, chapter: 20 });
    renderWithApi(<CharacterStatus characterId={HERO} />, [
      {
        match: /\/characters\/[^/]+\/state/,
        body: stateWith([
          dim("武功境界", "三阶", 18),
          dim("武功境界", "一阶", 5),
          dim("年龄", "十二", 3),
        ]),
      },
    ]);

    const card = (await screen.findByText(/武功境界/)).closest(".statecard") as HTMLElement;
    // `:not(.st-loc)` 把「所在地」那一行排除掉——它 2026-09-06 起也用同一套布局
    // （同一个 `st-dim`），但它不是状态字段。
    const rows = [...card.querySelectorAll<HTMLElement>(".st-dim:not(.st-loc)")];

    // 同一个字段只占一行，两个值都在那一行上，新的在前。
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("武功境界");
    expect(rows[0]).toHaveTextContent("三阶");
    expect(rows[0]).toHaveTextContent("第 18 章");
    expect(rows[0]).toHaveTextContent("一阶");
    expect(rows[0]).toHaveTextContent("第 5 章");
    expect(rows[0].textContent!.indexOf("三阶")).toBeLessThan(
      rows[0].textContent!.indexOf("一阶"),
    );

    // **「最新」是每一格自己的最新**：它标在「三阶」上，不标在「一阶」上。
    const values = [...rows[0].querySelectorAll<HTMLElement>(".st-val")];
    expect(values[0]).toHaveTextContent("（最新）");
    expect(values[1]).not.toHaveTextContent("（最新）");

    // 组间排序：这一格最近一次变化更晚的在上面。
    expect(rows[1]).toHaveTextContent("年龄");
  });

  it("一个字段只有一个值时不打「最新」——那时它什么也没区分", async () => {
    // **判据是「这一格里有几个值」，不是「全卡有几条」**：从前那一版比的是全卡最新
    // 章号，于是一格填过三次的字段，中间那次和别的字段的当前值长得一样重要。
    useCoords.setState({ projectId: PID, chapter: 20 });
    renderWithApi(<CharacterStatus characterId={HERO} />, [
      {
        match: /\/characters\/[^/]+\/state/,
        body: stateWith([dim("武功境界", "三阶", 18), dim("年龄", "十二", 3)]),
      },
    ]);

    expect(await screen.findByText(/武功境界/)).toBeInTheDocument();
    expect(screen.queryByText("（最新）")).toBeNull();
  });
});
