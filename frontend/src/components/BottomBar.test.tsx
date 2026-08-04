import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { BottomBar } from "./BottomBar";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
});

describe("底部信息区", () => {
  it("没有场景或选中人物时不展示空时间线", async () => {
    const { container } = renderWithApi(<BottomBar />, [
      { match: /\/chapters\/\d+\/scenes/, body: [] },
    ]);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
