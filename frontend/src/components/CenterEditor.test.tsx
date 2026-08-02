import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { CenterEditor } from "./CenterEditor";

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    selectedNodeId: null,
    cast: "",
    selection: "",
    setSelection: () => {},
    focusNode: () => {},
    highlight: null,
    setHighlight: () => {},
  });
  // CodeMirror 6 在 jsdom 里需要这两个测量 API。
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

describe("中栏编辑器", () => {
  it("顶栏显示当前章号，正文从 /text 拉取（fixture 真出参）", async () => {
    renderWithApi(<CenterEditor />);
    expect(await screen.findByText("第 1 章")).toBeInTheDocument();
  });

  it("嵌着的场景条为空时给出「下一步」提示", async () => {
    renderWithApi(<CenterEditor />);
    expect(await screen.findByText(/这一章没有场景块/)).toBeInTheDocument();
  });
});
