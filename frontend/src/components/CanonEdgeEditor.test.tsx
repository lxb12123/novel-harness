import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { CanonEdgeEditor } from "./CanonEdgeEditor";

// 自动 Canon 边的纠错弹窗（Task 8 / ADR 0032）。
//
// 打开方式只有一条：store 的 `focusEdgeId`（从活动记录 `jump.edge_id` 跳来，
// 或人物当前事实点名某条边）。前端不从那行字里认哪条边。
//
// 这份测试吃的是真 dump（`fixtures.canonEdge` —— 一条 `source=extractor`、
// `author_owned=false` 的 LOCATED_AT 边）：形状变了但组件没跟上 → vitest 红。

const PID = "project:ID1";
// **从真 dump 反查而不是写死 `edge:IDn`**：id 是 dump 按写入顺序编的别名，
// 契约测试每多 dump 一个端点（如 Task 10 的通知）整串就集体前移。
const EDGE_ID = fixtures.canonEdge.edge_id;
const EDGE_URL = `/canon/edges/${encodeURIComponent(EDGE_ID)}`;

function openEdge() {
  useCoords.setState({ projectId: PID, focusEdgeId: EDGE_ID });
}

afterEach(() => {
  useCoords.setState({ projectId: null, focusEdgeId: null });
});

/** 等弹窗把那条边真的读回来（`fixtures.canonEdge` 渲染完）再动手。 */
async function awaitEdgeLoaded() {
  await screen.findByRole("dialog", { name: "改这条事实" });
  await screen.findByText(/自动提取/);
}

describe("CanonEdgeEditor（Task 8）", () => {
  beforeEach(() => {
    useCoords.setState({ projectId: null, focusEdgeId: null });
  });

  it("按 `focusEdgeId` 读回那条边，说出「自动提取」的来源", async () => {
    openEdge();
    renderWithApi(<CanonEdgeEditor />);
    await awaitEdgeLoaded();
    // 起源是 extractor 而不是 author：`author_owned: false` 时不加「作者已修改」。
    expect(screen.queryByText(/作者已修改/)).toBeNull();
    // 章号是后端给的出参，不是作者填的框。
    expect(screen.getByText(/第 1 章起/)).toBeInTheDocument();
  });

  it("改地点：发 PATCH 到真路由，成功后跟着回执换到新 id、保留弹窗", async () => {
    openEdge();
    renderWithApi(<CanonEdgeEditor />);
    await awaitEdgeLoaded();
    // **spy 必须装在 `renderWithApi` 之后**：它内部 `vi.stubGlobal("fetch", …)`
    // 会把之前装好的 spy 整个换掉（`stubGlobal` 是替换不是包一层）。
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const place = screen.getByLabelText("地点") as HTMLSelectElement;
    await userEvent.selectOptions(place, "location:ID8");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining(EDGE_URL),
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
    // 改地点是「identity 变了」（`fixtures.canonEdgeEdited.replacement_edge_id`
    // 真的和原 edge_id 不同）：旧 id 立刻失效，店里必须换到回执给的新 id 上，
    // 不能继续显示一条已经撤回的边（那会读到「读不出来」）。
    await waitFor(() =>
      expect(useCoords.getState().focusEdgeId).toBe(fixtures.canonEdgeEdited.replacement_edge_id),
    );
    // 保存成功 = 这份草稿已经消费掉：新 id 那份边一读回来，按钮回到禁用
    // （没有未提交的修改），但弹窗还开着（作者可能接着改别的）。
    const save = await screen.findByRole("button", { name: "保存修改" });
    await waitFor(() => expect(save).toBeDisabled());
    expect(screen.getByRole("dialog", { name: "改这条事实" })).toBeInTheDocument();
  });

  it("改了人（改归属）：同样走 PATCH，且请求体里没有章号键", async () => {
    openEdge();
    renderWithApi(<CanonEdgeEditor />);
    await awaitEdgeLoaded();
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    // 角色册里 Character 那一档出现改归属下拉；选另一个人。
    const owner = screen.getByLabelText(/改归属/) as HTMLSelectElement;
    const charOptions = Array.from(owner.options).map((o) => o.value);
    expect(charOptions).toContain("character:ID6");
    await userEvent.selectOptions(owner, "character:ID6");

    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => {
      const calls = fetchSpy.mock.calls.filter(
        ([url, init]) =>
          String(url).includes(EDGE_URL) && init?.method === "PATCH",
      );
      expect(calls.length).toBeGreaterThan(0);
      const body = JSON.parse(String(calls[0][1]?.body));
      expect(body).toMatchObject({ kind: "location", character_id: "character:ID6" });
      expect(body).not.toHaveProperty("valid_from_chapter");
      expect(body).not.toHaveProperty("type");
    });
  });

  it("撤回要二次确认；确认后 DELETE 到真路由并关掉弹窗", async () => {
    openEdge();
    renderWithApi(<CanonEdgeEditor />);
    await awaitEdgeLoaded();
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const retract = screen.getByRole("button", { name: "撤回这条事实" });
    await userEvent.click(retract);
    // 第一次点击只是问一句，不真发请求。
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) =>
          String(url).includes(EDGE_URL) && init?.method === "DELETE",
      ),
    ).toBe(false);

    const confirm = await screen.findByRole("button", { name: "确认撤回" });
    await userEvent.click(confirm);
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining(EDGE_URL),
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
    // 撤回后没有当前边可展示——弹窗关掉，回调用方。
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "改这条事实" })).toBeNull(),
    );
    expect(useCoords.getState().focusEdgeId).toBeNull();
  });

  it("没有 focusEdgeId 时不渲染（弹窗不是常驻）", () => {
    renderWithApi(<CanonEdgeEditor />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

// 「维度」下拉框认项目里真实的 StateDim（Task 8 补记）：多数维度是模型自由写的
// 文本、没有机器键（2026-08-27 裁定），硬编码「生死/所在」两项此前会在打开任何
// 一条自由维度的事实时，把下拉框选成一个假的、点了会误写 dim_key 的选项。
//
// 吃的是真 dump（`fixtures.canonEdgeState` —— 一条 `dst` 指向自由维度节点、
// `props.dim_key` 为 `null` 的 HAS_STATE 边；`fixtures.stateDims` —— 这个项目
// 真实的 StateDim 列表，此刻只有那一条维度）。
describe("CanonEdgeEditor 的「维度」下拉框（Task 8 补记）", () => {
  const STATE_EDGE_ID = fixtures.canonEdgeState.edge_id;
  const STATE_EDGE_URL = `/canon/edges/${encodeURIComponent(STATE_EDGE_ID)}`;
  const extraRoutes = [
    { match: new RegExp(`${STATE_EDGE_URL}$`), body: fixtures.canonEdgeState },
    { match: /\/canon\/state-dims$/, body: fixtures.stateDims },
  ];

  afterEach(() => {
    useCoords.setState({ projectId: null, focusEdgeId: null });
  });

  it("列出这个项目真实的维度，不是硬编码的「生死/所在」两项", async () => {
    useCoords.setState({ projectId: PID, focusEdgeId: STATE_EDGE_ID });
    renderWithApi(<CanonEdgeEditor />, extraRoutes);
    await screen.findByRole("dialog", { name: "改这条事实" });

    const dim = (await screen.findByLabelText("维度")) as HTMLSelectElement;
    // 维度列表是**另一条**异步查询（`useStateDims`），不跟着边一起到——
    // 等真正的选项文本出现，而不是在弹窗刚挂载那一帧就去读 `dim.options`。
    await screen.findByText(fixtures.stateDims[0].name);
    const options = Array.from(dim.options).map((o) => ({ value: o.value, text: o.text }));
    expect(options).toEqual([
      { value: fixtures.stateDims[0].id, text: fixtures.stateDims[0].name },
    ]);
    // 当前选中的是这条边真实指向的维度节点（`view.dst`）——不是空字符串,
    // 也不是碰巧撞上硬编码 "health"/"location" 里的哪一个。
    expect(dim.value).toBe(fixtures.canonEdgeState.dst);
  });

  it("保存时发的是 dim_node_id（节点 id），请求体里没有 dim_key 这个键", async () => {
    useCoords.setState({ projectId: PID, focusEdgeId: STATE_EDGE_ID });
    renderWithApi(<CanonEdgeEditor />, extraRoutes);
    await screen.findByRole("dialog", { name: "改这条事实" });
    await screen.findByLabelText("维度");
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const value = screen.getByLabelText("状态值") as HTMLInputElement;
    await userEvent.clear(value);
    await userEvent.type(value, "金丹期");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => {
      const calls = fetchSpy.mock.calls.filter(
        ([url, init]) => String(url).includes(STATE_EDGE_URL) && init?.method === "PATCH",
      );
      expect(calls.length).toBeGreaterThan(0);
      const body = JSON.parse(String(calls[0][1]?.body));
      expect(body).toMatchObject({
        kind: "state",
        dim_node_id: fixtures.canonEdgeState.dst,
        value: "金丹期",
      });
      expect(body).not.toHaveProperty("dim_key");
    });
  });
});