import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { RightPanel } from "./RightPanel";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, cast: "", activeTab: "roster" });
});

describe("右侧信息区", () => {
  it("默认停在角色册，而且它是第一格", async () => {
    renderWithApi(<RightPanel />);
    const tabs = await screen.findAllByRole("button");
    expect(tabs[0]).toHaveAccessibleName("角色册");
    expect(tabs[0]).toHaveClass("on");
    expect(await screen.findByText(fixtures.roster[0].name)).toBeInTheDocument();
  });

  it("「通知」那个数字 = 这一格底下真有几件事等你看（含待确认的情节）", async () => {
    // 作者 2026-09-05：「这个页面的通知数字，应该也包括待确认的事件」。
    // 夹具：`/notifications/count` 回 3（通知 + 提案，后端整本书的口径），
    // 这一章的 PROVISIONAL 情节 2 条 → badge 应该是 5。
    //
    // **待确认情节那一半只能在前端加**：后端那条 count 是整本书的，而这一摞是
    // 「到当前章为止」的（和面板底下画出来的那一份同一个 query）。混进一个数里
    // 就会出现「badge 说 5、底下只画得出 3」——2026-08-31 把提案算进这个数时立的
    // 规矩就是这一条：badge 和它底下真画出来的条目数必须对得上。
    renderWithApi(<RightPanel />);
    expect(
      await screen.findByRole("button", { name: `通知 ${3 + fixtures.eventsProvisional.length}` }),
    ).toBeInTheDocument();
  });

  it("一件都没有的时候，「通知」后面不挂一个 0", async () => {
    // 「通知 0」是把「没事」写成一个需要读的数字。没有就不显示。
    renderWithApi(<RightPanel />, [
      { match: /\/notifications\/count$/, body: { open: 0 } },
      { match: /\/chapters\/\d+\/events\?scope=PROVISIONAL/, body: [] },
    ]);
    expect(await screen.findByRole("button", { name: "通知" })).toBeInTheDocument();
  });

  it("空白新书只显示作者能理解的下一步：手动加一条，或让分析从正文整理", async () => {
    // 作者 2026-09-13（装好桌面版、导入一本书之后）：「角色栏的角色他现在是只能手动添加吗，
    // 我记得之前是有扫描添加」「应该加个 or」——空态只写「添加第一个条目」等于说人物
    // 只能手填，而这一格平时是「分析本章」从正文里整理出来填满的。
    renderWithApi(<RightPanel />, [
      { match: /\/roster$/, body: [] },
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);

    expect(await screen.findByText(/添加第一个条目/)).toBeInTheDocument();
    expect(await screen.findByText(/在「事件」中分析本章/)).toBeInTheDocument();
    expect(screen.getByText(/保存正文后也会自动分析/)).toBeInTheDocument();
    // 钥匙填了就不再叫他去填。
    expect(screen.queryByText(/填入 API 密钥/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/R[1-4]|must_not_reveal|valid_from|issue/);
  });

  it("模型服务没配好的时候，空态第一句是「先连接模型」，那条路一站一站画出来", async () => {
    // 作者 2026-09-13：「这边的文字最应该首选是引导用户配置一个模型……比如画个设置的图标
    // -> connection -> endpoint -> 应用……在引导前边应该加个说明的语句」。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      { match: /\/roster$/, body: [] },
      { match: /\/api\/settings$/, body: fixtures.settings }, // model_configured: false
    ]);
    expect(
      await screen.findByText("角色册由模型从正文中整理，需先连接模型服务："),
    ).toBeInTheDocument();
    const steps = screen.getByText(/模型服务/, { selector: ".model-guide-steps" });
    expect(steps.textContent).toBe("AI 设置 → 模型服务 → 服务地址 · 模型 · API 密钥 → 应用");
    // 手动那条路退成第二句；「在「事件」中分析本章」那条不在（没配好点了也白点）
    expect(screen.getByText(/手动添加第一个条目/)).toBeInTheDocument();
    expect(screen.queryByText(/在「事件」中分析本章/)).toBeNull();
    // 「AI 设置」是一颗链接：点它直接开到「模型服务」那一栏
    await user.click(screen.getByRole("button", { name: "AI 设置" }));
    expect(useCoords.getState().settingsOpen).toBe("link");
  });

  it("角色册空态里的「在「事件」中分析本章」是一条路：点它就到「事件」那一格", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      { match: /\/roster$/, body: [] },
      { match: /\/chapters\/\d+\/events\?scope=CANON/, body: [] },
    ]);
    await user.click(await screen.findByText(/在「事件」中分析本章/));
    expect(useCoords.getState().activeTab).toBe("review");
    // 到了那一格，入口就在工具栏上（作者 2026-09-05 定的位置）。
    expect(await screen.findByRole("button", { name: "分析本章" })).toBeInTheDocument();
  });

  it("角色册空着的时候，别的格不再被一句「先去加人」挡住", async () => {
    // 2026-09-13 之前这儿有一道闸：角色册一空，「检验规则」和「事件」整格换成
    // 「添加人物或设定后……在「角色册」中点击「＋」添加」。检验规则查的是正文里的字面，
    // 跟有没有人无关；「事件」那一格的工具栏上就是「分析本章」——把人物整理进角色册的
    // 入口。闸门把入口一起藏了，作者只看得见「＋」，以为人物只能手动加。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      { match: /\/roster$/, body: [] },
      { match: /\/chapters\/\d+\/events\?scope=CANON/, body: [] },
    ]);

    expect(
      await screen.findByRole("button", { name: "建人物 / 地点 / 势力…" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "检验规则" }));
    expect(await screen.findByPlaceholderText("添加章节检验规则，回车保存")).toBeInTheDocument();
    expect(screen.queryByText(/添加人物或设定后/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "事件" }));
    expect(await screen.findByRole("button", { name: "分析本章" })).toBeInTheDocument();
    expect(await screen.findByText(/点击上方「分析本章」从正文整理/)).toBeInTheDocument();
    expect(screen.queryByText(/添加人物或设定后/)).toBeNull();
  });

  it("检查面板不显示规则编号或内部字段", async () => {
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      { method: "POST", match: /\/check$/, body: fixtures.check },
    ]);

    await user.click(screen.getByRole("button", { name: "检验规则" }));
    await user.click(screen.getByRole("button", { name: "快速检验本章" }));
    // **不许说「已检查 N 个场景」**：「场景」是作者要在正文里手写的标记块，
    // 导进来的真书上永远是零个；而它旁边那句「无法进行内容检查」从 R2/R3 进
    // `ALL_CHECKS`（2026-08-02）起就是假的——零场景块照样查了两条规则。
    expect(await screen.findByText(/没有发现需要处理的问题/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/场景/);
    expect(document.body.textContent).not.toMatch(/R[1-4]|issue|location_conflict|future_leak/);
  });

  it("检验规则那一栏：系统那一组空着就不出现，作者自己那一组能加能删", async () => {
    // 作者 2026-09-04：「这种默认的规则就应该写上去，而不是屏幕上没写的那个默认的
    // 规则」——所以这一栏点开就写着会查什么。次日（ADR 0042）系统规则全砍了，
    // 维护者的话：「我们只是把系统那个隐藏了……因为指不定我们以后也有规则」，
    // 于是判据是**空不空**，不是写死「没有系统规则」。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      { match: /\/validation-rules$/, body: [] },
      {
        method: "POST",
        match: /\/validation-rules$/,
        body: fixtures.validationRuleCreated,
      },
    ]);
    await user.click(await screen.findByRole("button", { name: "检验规则" }));

    // 系统那一组：一条都没有 ⇒ 连标题都不出现。
    expect(screen.queryByText("内置规则")).toBeNull();
    // 作者那一组：在，且空态说得出下一步是什么（不是「暂无数据」）。
    expect(screen.getByText("自定义规则")).toBeInTheDocument();
    // **空列表下面直接就是输入框**：不摆一行「尚未添加」占位（作者：「画的很丑啊，
    // 不要的内容，就是精简好看的那种」）。
    expect(screen.queryByText("尚未添加")).toBeNull();

    // 加一条：走真路由（POST /validation-rules），回车提交。
    const input = screen.getByPlaceholderText("添加章节检验规则，回车保存");
    await user.type(input, "玄铁令{Enter}");
    await waitFor(() => expect(input).toHaveValue(""));

    // **规则编号不上屏**（机制词），而那颗按钮照旧在。
    expect(document.body.textContent).not.toMatch(/vrule|R3/);
    expect(screen.getByRole("button", { name: "快速检验本章" })).toBeInTheDocument();
  });

  it("每条规则都能停用、能改、能删，停用之后仍然列着", async () => {
    // 作者拿参考稿点名要的三件事（「人家还能编辑，还能启用还能打叉」）。
    // **停用之后还在列表里**是这一条真正的判据：目录那条读法 2026-09-05 改成返回全部
    // （运行时那条仍然只读启用的），否则关掉的规则从屏幕上消失，作者再也开不回来。
    const user = userEvent.setup();
    const rule = {
      rule_id: "vrule:1",
      title: "玄铁令",
      description: "",
      enabled: false,
      blocks_downstream: true,
      template: "forbidden_literal",
      config: { literal: "玄铁令" },
    };
    renderWithApi(<RightPanel />, [
      { match: /\/validation-rules$/, body: [rule] },
      {
        method: "PATCH",
        match: /\/validation-rules\//,
        body: { rule_id: rule.rule_id, updated: true },
      },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(await screen.findByRole("button", { name: "检验规则" }));

    // 停用的那条还在，序号照排，开关是关着的。
    expect(await screen.findByText("不许出现「玄铁令」")).toBeInTheDocument();
    expect(screen.getByText("01")).toBeInTheDocument();
    const toggle = screen.getByRole("switch");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("button", { name: "改这条规则" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删掉这条规则" })).toBeInTheDocument();

    await user.click(toggle);
    await waitFor(() =>
      expect(
        spy.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PATCH"),
      ).toHaveLength(1),
    );

    // 改：点 ✎ 就地变输入框，回车发 PATCH，改的是**那个词**（`literal`）。
    await user.click(screen.getByRole("button", { name: "改这条规则" }));
    const box = screen.getByDisplayValue("玄铁令");
    await user.clear(box);
    await user.type(box, "寒铁令{Enter}");
    await waitFor(() => {
      const patches = spy.mock.calls.filter(
        (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patches).toHaveLength(2);
      expect(String((patches[1][1] as RequestInit).body)).toContain("寒铁令");
    });
  });

  it("系统规则不是空的时候，那一组照旧写出来", async () => {
    // 目录里再放一条系统规则时这一格要自己回来——`SYSTEM_RULE_TEXT` 按 rule_id 覆盖成
    // 作者读得懂的话（后端那两句仍是中文硬编码）。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      {
        match: /\/validation-rules$/,
        body: [
          {
            rule_id: "R3",
            title: "人物开口时机",
            description: "已死或尚未登场的角色在说话人标签位置开口。",
            enabled: true,
            blocks_downstream: true,
            template: "system",
          },
        ],
      },
    ]);
    await user.click(await screen.findByRole("button", { name: "检验规则" }));

    expect(await screen.findByText("内置规则")).toBeInTheDocument();
    const row = (await screen.findByText(/人物开口时机/)).closest(".rule-row") as HTMLElement;
    expect(row).toHaveTextContent("已死亡的人物在正文中说话");
    expect(document.body.textContent).not.toMatch(/\bR3\b/);
  });

  it("**检查跑不起来时不许把裸码摆上屏**（曾经直接读 `(check.error as Error).message`）", async () => {
    // 真发生过的那次：这一章的正文文件已经不在了，后端只回一个码——
    // `(check.error as Error).message` 在没有 `.message` 字段时会退回 `body.error`，
    // 屏幕上出现的就是原样的 `chapter_not_found` 五个字母（国际化第四批·裸错误码审计
    // 撞见的真回归，这一格此前没有任何失败路径测试）。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />, [
      {
        method: "POST",
        match: /\/check$/,
        status: 404,
        body: { detail: { error: "chapter_not_found", params: { chapter: 1 } } },
      },
    ]);

    await user.click(await screen.findByRole("button", { name: "检验规则" }));
    await user.click(screen.getByRole("button", { name: "快速检验本章" }));

    expect(
      await screen.findByText("第 1 章已不存在，可能已被删除或更改章号。请刷新页面。"),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/chapter_not_found/);
  });
});

// ⚠️ **2026-08-31：「在场是数出来的，不是作者填的」这一组删了**——它测的是
// `CastLine`（「这一章提到」显示 + cast 过滤退出条），随「写作提醒」tab 一起没了
// 唯一的挂载点（见 ADR 0041）。`mentioned.py`/`useMentioned` 本身没有删，只是现在
// 没有任何右栏 tab 在渲染它——这份收尾原样带过去，不在这份改动里重新找地方安置。
