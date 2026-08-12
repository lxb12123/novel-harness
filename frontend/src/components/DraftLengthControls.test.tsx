import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DraftLengthControls } from "./DraftLengthControls";

const minimum = () => screen.getByRole("spinbutton", { name: "最少" });
const target = () => screen.getByRole("spinbutton", { name: "目标" });
const maximum = () => screen.getByRole("spinbutton", { name: "最多" });

// 可用的 localStorage（Node 那个空壳的替身）和每个 test 前的清空都在 src/test/setup.ts。

describe("双语起草长度控件", () => {
  it("默认给中文 2000/2500/3000 字，只展示长度设置", () => {
    render(<DraftLengthControls />);

    expect(screen.getByRole("combobox", { name: "写作语言" })).toHaveValue("zh");
    expect(minimum()).toHaveValue(2000);
    expect(target()).toHaveValue(2500);
    expect(maximum()).toHaveValue(3000);
    expect(screen.getByText("字")).toBeInTheDocument();
    expect(screen.getByText("不足时最多自动续写一次")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.body.textContent).not.toMatch(/M2|ADR|PASS|实验/);
  });

  it("明确切到 English 后使用 1200/1500/1800 words", async () => {
    const user = userEvent.setup();
    render(<DraftLengthControls />);

    await user.selectOptions(screen.getByRole("combobox", { name: "写作语言" }), "en");

    expect(minimum()).toHaveValue(1200);
    expect(target()).toHaveValue(1500);
    expect(maximum()).toHaveValue(1800);
    expect(screen.getByText("words")).toBeInTheDocument();
  });

  it("把用户自定的语言和长度持久化到 localStorage", async () => {
    const user = userEvent.setup();
    const view = render(<DraftLengthControls />);

    await user.selectOptions(screen.getByRole("combobox", { name: "写作语言" }), "en");
    await user.clear(target());
    await user.type(target(), "2400");
    await user.clear(maximum());
    await user.type(maximum(), "3000");
    view.unmount();
    render(<DraftLengthControls />);

    expect(screen.getByRole("combobox", { name: "写作语言" })).toHaveValue("en");
    expect(minimum()).toHaveValue(1200);
    expect(target()).toHaveValue(2400);
    expect(maximum()).toHaveValue(3000);
  });

  it("在控件里拒绝中文 20001 字和英文 12001 words", async () => {
    const user = userEvent.setup();
    render(<DraftLengthControls />);

    await user.clear(maximum());
    await user.type(maximum(), "20001");
    expect(maximum()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("中文最多 20,000 字");

    await user.selectOptions(screen.getByRole("combobox", { name: "写作语言" }), "en");
    await user.clear(maximum());
    await user.type(maximum(), "12001");
    expect(maximum()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("English 最多 12,000 words");
  });
});
