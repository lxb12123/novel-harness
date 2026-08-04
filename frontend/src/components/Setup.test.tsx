import { act, fireEvent, screen, waitFor } from "@testing-library/react";
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
  uploadFile(new File([text], name, { type: "text/plain" }));
}

function uploadFile(file: File) {
  fireEvent.change(screen.getByLabelText("选择 TXT"), {
    target: { files: [file] },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function deferredTextFile(name: string, text: string) {
  const gate = deferred<ArrayBuffer>();
  const bytes = new TextEncoder().encode(text);
  const file = new File([], name, { type: "text/plain" });
  Object.defineProperty(file, "arrayBuffer", { value: () => gate.promise });
  return { file, resolve: () => gate.resolve(bytes.buffer as ArrayBuffer) };
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

  it("为壳层和流程元素提供 onboarding 样式契约", async () => {
    renderWithApi(<Setup />);

    for (const name of [
      "shell",
      "identity",
      "logo",
      "brand",
      "tagline",
      "promises",
      "promise",
      "promise-mark",
      "flow",
      "panel",
      "footer",
      "eyebrow",
      "title",
      "lead",
      "actions",
      "action",
      "action-icon",
      "action-copy",
      "action-name",
      "action-description",
    ]) {
      expect(document.querySelector(`.onboarding-${name}`)).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: /导入现有小说/ })).toHaveClass(
      "onboarding-action",
      "primary",
    );

    chooseImport();
    upload();
    await screen.findByText("青云记.txt");
    for (const name of ["form", "file", "file-name", "file-meta", "form-actions"]) {
      expect(document.querySelector(`.onboarding-${name}`)).toBeInTheDocument();
    }
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
    expect(screen.getByRole("alert")).toHaveClass("onboarding-error");
    expect(screen.getByText("坏书.txt")).toBeInTheDocument();
    expect(screen.getByLabelText("书名")).toHaveValue("坏书");

    await user.type(screen.getByLabelText("书名"), "修订");
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("忽略返回后才完成的旧文件读取，不覆盖后选文件", async () => {
    const user = userEvent.setup();
    const oldFile = deferredTextFile("旧稿.txt", "第一章 旧稿\n\n旧文本");
    const newFile = deferredTextFile("新稿.txt", "第一章 新稿\n\n新文本");
    renderWithApi(<Setup />);
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    chooseImport();
    uploadFile(oldFile.file);
    await screen.findByText("旧稿.txt");
    await user.click(screen.getByRole("button", { name: "返回" }));
    chooseImport();
    uploadFile(newFile.file);
    await screen.findByText("新稿.txt");
    await act(async () => newFile.resolve());
    await waitFor(() => expect(screen.getByRole("button", { name: /导入并进入工作台/ })).toBeEnabled());
    await act(async () => oldFile.resolve());

    expect(screen.getByText("新稿.txt")).toBeInTheDocument();
    expect(screen.getByLabelText("书名")).toHaveValue("新稿");
    await user.click(screen.getByRole("button", { name: /导入并进入工作台/ }));
    await waitFor(() => expect(useCoords.getState().projectId).toBe(fixtures.bootstrapImport.project.id));
    const bootstrapCall = fetchSpy.mock.calls.find(([url, init]) =>
      String(url).endsWith("/api/projects/bootstrap") && init?.method === "POST",
    );
    expect(JSON.parse(String(bootstrapCall?.[1]?.body))).toMatchObject({
      mode: "import",
      name: "新稿",
      text: "第一章 新稿\n\n新文本",
    });
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

  it("抽屉暴露 dialog 语义，并支持关闭按钮和 Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithApi(<Setup onClose={onClose} />);

    expect(screen.getByRole("dialog", { name: "新建 / 导入小说" })).toHaveAttribute("aria-modal", "true");
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("提交期间 backdrop、Escape 和关闭按钮都不会提前关闭抽屉", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const response = deferred<Response>();
    renderWithApi(<Setup onClose={onClose} />);
    vi.stubGlobal("fetch", vi.fn(() => response.promise));

    await user.click(screen.getByRole("button", { name: /新建空白小说/ }));
    await user.type(screen.getByLabelText("书名"), "后台新书");
    await user.click(screen.getByRole("button", { name: /创建并进入工作台/ }));
    const close = screen.getByRole("button", { name: "关闭" });
    await waitFor(() => expect(close).toBeDisabled());
    fireEvent.click(document.querySelector(".backdrop")!);
    await user.keyboard("{Escape}");
    await user.click(close);
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      response.resolve({
        ok: true,
        status: 200,
        json: async () => fixtures.bootstrapImport,
      } as Response);
    });
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
