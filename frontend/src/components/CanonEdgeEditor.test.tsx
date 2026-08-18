import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithApi } from "../test/harness";
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
const EDGE_ID = "edge:ID69";

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

  it("改地点：发 PATCH 到真路由，成功后按回执失效并保留弹窗", async () => {
    openEdge();
    renderWithApi(<CanonEdgeEditor />);
    await awaitEdgeLoaded();
    // **spy 必须装在 `renderWithApi` 之后**：它内部 `vi.stubGlobal("fetch", …)`
    // 会把之前装好的 spy 整个换掉（`stubGlobal` 是替换不是包一层）。
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const place = screen.getByLabelText("地点") as HTMLSelectElement;
    await userEvent.selectOptions(place, "location:ID9");
    const save = screen.getByRole("button", { name: "保存修改" });
    await userEvent.click(save);

    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining(`/canon/edges/edge%3AID69`),
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
    // 保存成功 = 这份草稿已经消费掉：按钮回到禁用（没有未提交的修改），
    // 但弹窗还开着（作者可能接着改别的）。
    await waitFor(() => expect(save).toBeDisabled());
    expect(screen.getByRole("dialog", { name: "改这条事实" })).toBeInTheDocument();
  });

  it("改了人（改归属）：同样走 PATCH，且请求体里没有章号键", async () => {
    openEdge();
    renderWithApi(<CanonEdgeEditor />);
    await awaitEdgeLoaded();
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    // 花名册里 Character 那一档出现改归属下拉；选另一个人。
    const owner = screen.getByLabelText(/改归属/) as HTMLSelectElement;
    const charOptions = Array.from(owner.options).map((o) => o.value);
    expect(charOptions).toContain("character:ID6");
    await userEvent.selectOptions(owner, "character:ID6");

    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => {
      const calls = fetchSpy.mock.calls.filter(
        ([url, init]) =>
          String(url).includes(`/canon/edges/edge%3AID69`) && init?.method === "PATCH",
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
          String(url).includes(`/canon/edges/edge%3AID69`) && init?.method === "DELETE",
      ),
    ).toBe(false);

    const confirm = await screen.findByRole("button", { name: "确认撤回" });
    await userEvent.click(confirm);
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining(`/canon/edges/edge%3AID69`),
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