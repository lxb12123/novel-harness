import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { Setup } from "./Setup";

const chapterText = "第一章 青云初现\n\n萧决踏进青云城。\n";

function chooseImport() {
  fireEvent.click(screen.getByRole("button", { name: /导入现有小说/ }));
}

function upload(name = "青云记.txt", text = chapterText) {
  fireEvent.change(screen.getByLabelText("选择 TXT"), {
    target: { files: [new File([text], name, { type: "text/plain" })] },
  });
}

describe("引导建书", () => {
  beforeEach(() => {
    useCoords.setState({ projectId: null, chapter: 1 });
  });

  it("起始页提供导入和空白小说入口，而不是书名表单", () => {
    renderWithApi(<Setup />);

    expect(screen.getByRole("button", { name: /导入现有小说/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建空白小说/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("书名")).not.toBeInTheDocument();
    expect(screen.getByLabelText("选择 TXT")).toHaveClass("setup-file-input");
  });

  it("选择 TXT 后显示可编辑的推导书名，尚未设置项目", async () => {
    renderWithApi(<Setup />);
    chooseImport();
    upload();

    expect(await screen.findByText("青云记.txt")).toBeInTheDocument();
    expect(screen.getByLabelText("书名")).toHaveValue("青云记");
    expect(screen.getByRole("button", { name: /导入并进入工作台/ })).toBeEnabled();
    expect(useCoords.getState().projectId).toBeNull();
  });

  it("导入后进入返回的项目首章", async () => {
    const user = userEvent.setup();
    renderWithApi(<Setup />);
    chooseImport();
    upload();
    await screen.findByText("青云记.txt");
    await user.click(screen.getByRole("button", { name: /导入并进入工作台/ }));

    await waitFor(() => {
      expect(useCoords.getState().projectId).toBe(fixtures.bootstrapImport.project.id);
      expect(useCoords.getState().chapter).toBe(1);
    });
  });

  it("空白小说以书名创建并进入首章", async () => {
    const user = userEvent.setup();
    renderWithApi(<Setup />);
    await user.click(screen.getByRole("button", { name: /新建空白小说/ }));

    const name = screen.getByLabelText("书名");
    expect(name).toHaveFocus();
    await user.type(name, "新手稿");
    await user.click(screen.getByRole("button", { name: /创建并进入工作台/ }));

    await waitFor(() => {
      expect(useCoords.getState().projectId).toBe(fixtures.bootstrapImport.project.id);
      expect(useCoords.getState().chapter).toBe(1);
    });
  });

  it("导入失败时保留文件与书名，并以 alert 呈现错误", async () => {
    const user = userEvent.setup();
    renderWithApi(<Setup />, [
      {
        method: "POST",
        match: /\/api\/projects\/bootstrap$/,
        status: 409,
        body: { error: "import_refused", message: "切不出章节" },
      },
    ]);
    chooseImport();
    upload("坏书.txt", "没有任何章节标题");
    await screen.findByText("坏书.txt");
    await user.click(screen.getByRole("button", { name: /导入并进入工作台/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("切不出章节");
    expect(screen.getByText("坏书.txt")).toBeInTheDocument();
    expect(screen.getByLabelText("书名")).toHaveValue("坏书");
  });

  it("抽屉模式不渲染品牌文案，并在空白小说成功后只关闭一次", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithApi(<Setup onClose={onClose} />);

    expect(screen.queryByText("让长篇小说中的每个人，只知道此刻该知道的事。")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /新建空白小说/ }));
    await user.type(screen.getByLabelText("书名"), "抽屉新书");
    await user.click(screen.getByRole("button", { name: /创建并进入工作台/ }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("键盘先打开 TXT 选择，再跳到空白小说并自动聚焦书名", async () => {
    const user = userEvent.setup();
    renderWithApi(<Setup />);
    const picker = screen.getByLabelText("选择 TXT") as HTMLInputElement;
    const click = vi.spyOn(picker, "click");

    await user.tab();
    expect(screen.getByRole("button", { name: /导入现有小说/ })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(click).toHaveBeenCalledOnce();
    await user.tab();
    expect(screen.getByRole("button", { name: /新建空白小说/ })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByLabelText("书名")).toHaveFocus();
  });
});
