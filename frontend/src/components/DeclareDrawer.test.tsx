import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithApi } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { DeclareDrawer } from "./DeclareDrawer";

/** 后端 `QuoteNotFound` 那句话的真形态（`declare.py`，经 `api/app.py` 原样进 message）。
 *  **它以前的最后一行是「先跑 nh sync」**——一条命令，摆在一位不碰命令行的作者脸上。 */
const QUOTE_NOT_FOUND = {
  error: "quote_not_found",
  quote: "他终于知道了真相。",
  message:
    "这句话在当前正文里一处都找不到：「他终于知道了真相。」\n" +
    "  这里只认一模一样的句子（标点、空格、全角半角都算）——从稿子里复制粘贴，别手打。\n" +
    "  也可能是这一章你在别的软件里改过，而这边还没读回来：\n" +
    "  让系统重新读一遍稿子，再试一次。",
};

describe("原文声明", () => {
  it("表单和成功回执不向作者展示内部字段或演示小说内容", async () => {
    const user = userEvent.setup();
    renderWithApi(<DeclareDrawer pid="project:ID1" quote="他终于知道了真相。" onClose={() => {}} />);

    expect(document.body.textContent).not.toMatch(/约束 10|valid_from|decision_log|萧决|北荒|血脉秘密/);
    await user.type(screen.getByLabelText("人物"), "主角");
    await user.type(screen.getByLabelText("秘密"), "身世真相");
    await user.click(screen.getByRole("button", { name: "保存记录" }));

    expect(await screen.findByText(/已记录/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/valid_from|decision_log|CANON|KNOWS|ch\d+/);
  });

  // ── 定位不到的那一档（2026-08-13）────────────────────────────────────────────
  //
  // 作者在 WPS 里改完第 23 章回到工作台：**正文他看得见**（那块屏幕直接读磁盘），
  // 而 `locate` 搜的是库里的快照。这一屏此前对他说「正文中没有找到这句话，
  // **请重新选择**」——让他去改一个他没做错的操作，而真正该做的那一下
  // （把稿子读回来）在整个工作台里根本没有入口。

  it("定位不到时**不说「请重新选择」**，并把「读回改动」摆在那儿", async () => {
    const user = userEvent.setup();
    renderWithApi(<DeclareDrawer pid="project:ID1" quote="他终于知道了真相。" onClose={() => {}} />, [
      { method: "POST", match: /\/locate$/, body: [] },
    ]);
    await user.click(screen.getByRole("button", { name: "检查原文位置" }));

    await screen.findByText(/在系统读到的那一版正文里没找到这句话/);
    expect(document.body.textContent).not.toContain("请重新选择");
    expect(screen.getByRole("button", { name: "读回改动" })).toBeInTheDocument();
  });

  it("保存被后端以「定位不到」拒了，同样摆出那颗按钮（两条路一个到达点）", async () => {
    const user = userEvent.setup();
    renderWithApi(<DeclareDrawer pid="project:ID1" quote="他终于知道了真相。" onClose={() => {}} />, [
      { method: "POST", match: /\/declare\/knows$/, status: 422, body: QUOTE_NOT_FOUND },
    ]);
    await user.type(screen.getByLabelText("人物"), "主角");
    await user.type(screen.getByLabelText("秘密"), "身世真相");
    await user.click(screen.getByRole("button", { name: "保存记录" }));

    await screen.findByText(/让系统重新读一遍稿子/);
    expect(screen.getByRole("button", { name: "读回改动" })).toBeInTheDocument();
  });

  it("按下去之后照说后端那句回执，不换成自己编的一句", async () => {
    const user = userEvent.setup();
    renderWithApi(<DeclareDrawer pid="project:ID1" quote="他终于知道了真相。" onClose={() => {}} />, [
      { method: "POST", match: /\/locate$/, body: [] },
    ]);
    await user.click(screen.getByRole("button", { name: "检查原文位置" }));
    await user.click(await screen.findByRole("button", { name: "读回改动" }));

    // 默认路由表里那一份 sync 回执（作者在外面改了一章、新写了一章、丢了个大纲进去）。
    await screen.findByText(/读回来了/);
    await screen.findByText(/内容变了：第 2 章/);
  });

  it("**这一整档屏幕上一个命令都没有**（第五张网就是为它加的）", async () => {
    const user = userEvent.setup();
    renderWithApi(<DeclareDrawer pid="project:ID1" quote="他终于知道了真相。" onClose={() => {}} />, [
      { method: "POST", match: /\/declare\/knows$/, status: 422, body: QUOTE_NOT_FOUND },
    ]);
    await user.type(screen.getByLabelText("人物"), "主角");
    await user.type(screen.getByLabelText("秘密"), "身世真相");
    await user.click(screen.getByRole("button", { name: "保存记录" }));
    await screen.findByText(/让系统重新读一遍稿子/);

    expect(devTerms(screenText())).toEqual([]);
  });

  it("**探针**：把旧那句话换回来，这条守卫当场红（否则它是一张空网）", () => {
    const old = "也可能是这一章还没进库：先跑 nh sync。";
    expect(devTerms(old)).toContain("nh sync");
  });

  it("同步失败时说后端那句话，且不静默当作成功", async () => {
    const user = userEvent.setup();
    renderWithApi(<DeclareDrawer pid="project:ID1" quote="他终于知道了真相。" onClose={() => {}} />, [
      { method: "POST", match: /\/locate$/, body: [] },
      {
        method: "POST",
        match: /\/sync$/,
        status: 422,
        body: { error: "sync_refused", message: "有一个章节文件切不出恰好一章。" },
      },
    ]);
    await user.click(screen.getByRole("button", { name: "检查原文位置" }));
    await user.click(await screen.findByRole("button", { name: "读回改动" }));

    await screen.findByText(/有一个章节文件切不出恰好一章/);
    expect(document.body.textContent).not.toMatch(/读回来了/);
  });

  it("按钮按下去只打一次 sync，且不顺手触发任何会花钱的东西", async () => {
    const user = userEvent.setup();
    renderWithApi(<DeclareDrawer pid="project:ID1" quote="他终于知道了真相。" onClose={() => {}} />, [
      { method: "POST", match: /\/locate$/, body: [] },
    ]);
    // **在 render 之后才挂**：`renderWithApi` 自己会 `stubGlobal("fetch", …)`，
    // 挂在前面的 spy 会被它整个换掉（同 `Setup.test.tsx` 那条的写法）。
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await user.click(screen.getByRole("button", { name: "检查原文位置" }));
    await user.click(await screen.findByRole("button", { name: "读回改动" }));
    await screen.findByText(/读回来了/);

    const urls = fetchSpy.mock.calls.map(([url]) => String(url));
    expect(urls.filter((u) => u.endsWith("/sync"))).toHaveLength(1);
    // 起草 / 整理 / 总结都要钱。**这颗按钮一条都不许替他按下**。
    await waitFor(() =>
      expect(urls.filter((u) => /\/(draft|extract|summary|turn)/.test(u))).toEqual([]),
    );
  });
});
