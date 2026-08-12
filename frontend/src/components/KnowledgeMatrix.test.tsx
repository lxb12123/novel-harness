import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import type { KnowledgeMatrix, SceneConstraints } from "../api/types";
import { useCoords } from "../store";
import { MatrixView } from "./KnowledgeMatrix";

// 头牌（README 第一行那个框）。喂的是**真后端 dump 出来的 matrix**，不是手写的形状。
//
// JSON import 会把 `state` 宽化成 string，所以这里必须 cast。那个 cast 不是白洞：
// 形状真变了的话 `tests/test_frontend_contract.py` 先红（它逐字节比对 dump），
// 这边红的是「变了之后组件还渲不渲得出来」。两头各管一半。
const matrix = fixtures.matrix as unknown as KnowledgeMatrix;
const constraints = fixtures.constraints as unknown as SceneConstraints;

// store 在同一个文件的多个 test 之间是共享的：不清的话「跳过来高亮那一格」会留到下一个 test。
beforeEach(() => useCoords.setState({ focusCell: null, projectId: "project:ID1" }));

/** 矩阵里那一格「知道」的坐标（真 dump 里唯一一格非 UNKNOWN 的）。 */
const knows = matrix.cells.find((c) => c.state === "KNOWS")!;
const who = () => matrix.characters.find((c) => c.id === knows.character_id)!;
const what = () => matrix.secrets.find((s) => s.id === knows.secret_id)!;
const openCell = () => screen.getByRole("button", { name: `改「${who().name} 对 ${what().name}」` });
/** `vi.spyOn(globalThis, "fetch")` 的调用记录，结构化收窄（`MockInstance` 的泛型在
 *  fetch 上对不齐，而这里只需要「参数数组的数组」）。 */
type Calls = { mock: { calls: unknown[][] } };

/** 最后一次 POST 的请求体。 */
const lastPost = (spy: Calls) => {
  const call = [...spy.mock.calls]
    .reverse()
    .find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
  return JSON.parse(String((call![1] as RequestInit).body));
};

describe("认知矩阵", () => {
  it("三态必须长得完全不一样 —— 这是整个产品的那句话", () => {
    render(<MatrixView matrix={matrix} constraints={constraints} />);

    // 断言的是**产品契约**（这三种写法），不是 fixture 里碰巧有什么：
    // 「✗ 不知道」写成「未知」都算破坏承诺，那不该跟着 fixture 漂。
    expect(screen.getByText("✓ 知道")).toBeInTheDocument();
    expect(screen.getAllByText("✗ 不知道").length).toBeGreaterThan(0);
  });

  it("知道状态用自然语言显示从哪一章开始", () => {
    const knows = matrix.cells.find((c) => c.state === "KNOWS");
    expect(knows, "fixture 里没有 KNOWS 单元格，这个测试就测不到东西了").toBeDefined();

    render(<MatrixView matrix={matrix} constraints={constraints} />);
    expect(screen.getByText(`第 ${knows!.since_chapter} 章起`)).toBeInTheDocument();
  });

  it("行是人、列是秘密，一个都不能少", () => {
    render(<MatrixView matrix={matrix} constraints={constraints} />);
    const table = screen.getByRole("table");
    for (const c of matrix.characters) {
      expect(within(table).getByText(c.name)).toBeInTheDocument();
    }
    for (const s of matrix.secrets) {
      expect(within(table).getByText(s.name)).toBeInTheDocument();
    }
  });

  it("不能说破的内容只显示名称，不泄漏内部字段名或秘密正文", () => {
    render(<MatrixView matrix={matrix} constraints={constraints} />);
    expect(screen.getByText("本场不能说破")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("must_not_reveal");

    // §1.2 的收窄纪律在前端这一侧的兑现：出参里就不该有 props/description，
    // 所以渲染出来的 DOM 里也不可能有。这条在 fixture 层已经被后端测试钉了一遍，
    // 这里再钉一遍是因为**它是这个项目的核心主张，值得两处都拦**。
    expect(document.body.textContent).not.toContain("在北荒");
    expect(document.body.textContent).not.toContain("萧决是魔尊之子");
  });

  it("没有在场角色时说人话，不画一张空表", () => {
    render(<MatrixView matrix={{ ...matrix, characters: [], cells: [] }} />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText(/当前没有可比较的人物和秘密/)).toBeInTheDocument();
  });

  it("从活动记录跳过来时，认得出**后端指的是哪一格**", () => {
    // 坐标是 `jump.character_id` / `jump.secret_id`（后端算的），不是从日志那行
    // 「萧决 对『血脉秘密』」里认出来的名字——名字解析可能歧义，而歧义时服务端的
    // 规矩是绝不替作者挑。
    const cell = matrix.cells[0];
    useCoords.setState({
      focusCell: { character_id: cell.character_id, secret_id: cell.secret_id },
    });
    render(<MatrixView matrix={matrix} constraints={constraints} />);

    const who = matrix.characters.find((c) => c.id === cell.character_id)!;
    const what = matrix.secrets.find((s) => s.id === cell.secret_id)!;
    expect(screen.getByLabelText(`${who.name} 对 ${what.name}（刚跳转到这一格）`)).toBeInTheDocument();
    // 只高亮一格：整列/整行亮起来等于没指向任何东西。
    expect(document.querySelectorAll("td.cell.focus")).toHaveLength(1);
  });

  it("没人跳过来的时候一格都不高亮", () => {
    render(<MatrixView matrix={matrix} constraints={constraints} />);
    expect(document.querySelectorAll("td.cell.focus")).toHaveLength(0);
  });

  it("找不到的称呼用作者语言说明", () => {
    render(<MatrixView matrix={{ ...matrix, unresolved_cast: ["师兄"] }} />);
    expect(screen.getByText(/师兄/)).toBeInTheDocument();
    expect(screen.getByText(/未在花名册中找到/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("退化值");
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 改这一格（ADR 0020 的「可改」）
// ══════════════════════════════════════════════════════════════════════════
//
// 干净的抽取结果直接进 CANON，作者第一次看见这一格时它已经生效了。这一组测的就是
// 那条退路：**看得见还不够，得改得掉。**

describe("改这一格", () => {
  it("「不知道」的格子没有编辑入口 —— 那儿没有可改的事实", async () => {
    // 改正层改的是**已经存在的那条边**，空格子必然 404（`corrections.py::FactNotFound`）。
    // 新增一条认知是「声明」，那条路要一句引语来定章号——一个功能不留两个入口。
    renderWithApi(<MatrixView matrix={matrix} constraints={constraints} />);
    const unknown = matrix.cells.filter((c) => c.state === "UNKNOWN");
    expect(unknown.length).toBeGreaterThan(0);
    for (const c of unknown) {
      const ch = matrix.characters.find((x) => x.id === c.character_id)!;
      const s = matrix.secrets.find((x) => x.id === c.secret_id)!;
      expect(screen.queryByRole("button", { name: `改「${ch.name} 对 ${s.name}」` })).toBeNull();
    }
    expect(screen.getAllByRole("button")).toHaveLength(1); // 只有那一格「知道」是按钮
  });

  it("点开只给「另一种」，而且带着作者的话说这一处不动章号", async () => {
    const user = userEvent.setup();
    renderWithApi(<MatrixView matrix={matrix} constraints={constraints} />);
    await user.click(openCell());

    expect(screen.getByText(/现在是「知道」/)).toBeInTheDocument();
    // 改成它已经是的那一种后端会拒（422），所以这儿根本不给这个选项。
    expect(screen.getByRole("button", { name: "改成「以为」" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "改成「知道」" })).toBeNull();
    // 约束 10 的界面影子：没有章号输入框，而且明说改这条不会动它从第几章开始。
    expect(screen.getByText(/不会动它是从第几章开始的/)).toBeInTheDocument();
    expect(document.querySelectorAll('input[type="number"]')).toHaveLength(0);
    expect(document.body.textContent).not.toMatch(/KNOWS|BELIEVES|valid_from|canon_version/);
  });

  it("「以为」没写内容就保存不了 —— 空着等于把错误认知显示成一片空白", async () => {
    const user = userEvent.setup();
    renderWithApi(<MatrixView matrix={matrix} constraints={constraints} />);
    await user.click(openCell());

    expect(screen.getByRole("button", { name: "改成「以为」" })).toBeDisabled();
    await user.type(screen.getByRole("textbox"), "以为那只是个传闻");
    expect(screen.getByRole("button", { name: "改成「以为」" })).toBeEnabled();
  });

  it("发出去的版本号是**这张表自己带来的那一个**", async () => {
    // `expected_canon_version` 必须是作者看到这张表那一刻的版本。从别的读端另取一次
    // 就是第二个会漂的源：中间有人升过 CANON 的话，CAS 会放过一次它本该拦下的改动。
    const user = userEvent.setup();
    renderWithApi(<MatrixView matrix={matrix} constraints={constraints} />);
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(openCell());
    await user.type(screen.getByRole("textbox"), "以为那只是个传闻");
    await user.click(screen.getByRole("button", { name: "改成「以为」" }));

    await waitFor(() => expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === "POST")).toBe(true));
    expect(lastPost(spy)).toEqual({
      character_id: knows.character_id,
      secret_id: knows.secret_id,
      to_type: "BELIEVES",
      believed_value: "以为那只是个传闻",
      expected_canon_version: matrix.version.canon_version,
    });
    expect(matrix.version.canon_version).toBeGreaterThan(0);
    // 改完就收起来：留着一张填了字的表单，作者会以为自己还没保存。
    await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());
  });

  it("「别处刚改过」要作者再看一眼，**不静默重试**", async () => {
    const user = userEvent.setup();
    const refreshed: number[] = [];
    renderWithApi(
      <MatrixView
        matrix={matrix}
        constraints={constraints}
        onRefresh={() => refreshed.push(1)}
      />,
      [
        {
          method: "POST",
          match: /\/canon\/knowledge$/,
          status: 409,
          body: fixtures.errorStaleCanon,
        },
      ],
    );
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(openCell());
    await user.type(screen.getByRole("textbox"), "以为那只是个传闻");
    await user.click(screen.getByRole("button", { name: "改成「以为」" }));

    expect(await screen.findByText(/先看一眼最新的/)).toBeInTheDocument();
    // 一次就是一次：重试等于把作者的改动盖到一份他没看过的状态上。
    const posts = spy.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(document.body.textContent).not.toContain("stale_base_version");

    await user.click(screen.getByRole("button", { name: "看看最新的" }));
    expect(refreshed).toHaveLength(1);
  });

  it("后端的拒绝**原样**照说（措辞的源只有后端一个）", async () => {
    const user = userEvent.setup();
    renderWithApi(<MatrixView matrix={matrix} constraints={constraints} />, [
      {
        method: "POST",
        match: /\/canon\/knowledge$/,
        status: 422,
        body: fixtures.errorKnowledgeRefused,
      },
    ]);
    await user.click(openCell());
    await user.type(screen.getByRole("textbox"), "以为那只是个传闻");
    await user.click(screen.getByRole("button", { name: "改成「以为」" }));

    // 断言的是「夹具里那句话原样上了屏」，不是某个前端改写过的版本——
    // 前端一旦有权改写，后端就可以一直吐 `BELIEVES`（见 `correctionError.ts` 的说明）。
    const said = (fixtures.errorKnowledgeRefused as { detail: { message: string } }).detail.message;
    expect(await screen.findByText(said)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/BELIEVES|bad_request/);
  });

  it("反过来改（「以为」→「知道」）一个字都不带", async () => {
    // 后端会拒带内容的那一边：他知道的就是真的那一版。
    const user = userEvent.setup();
    const believing: KnowledgeMatrix = {
      ...matrix,
      cells: matrix.cells.map((c) =>
        c.state === "KNOWS"
          ? { ...c, state: "BELIEVES" as const, believed_value: "以为那只是个传闻" }
          : c,
      ),
    };
    renderWithApi(<MatrixView matrix={believing} constraints={constraints} />);
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(openCell());
    expect(screen.queryByRole("textbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: "改成「知道」" }));

    await waitFor(() => expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === "POST")).toBe(true));
    expect(lastPost(spy)).not.toHaveProperty("believed_value");
    expect(lastPost(spy).to_type).toBe("KNOWS");
  });
});
