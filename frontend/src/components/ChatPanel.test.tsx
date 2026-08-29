import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi, turnStream } from "../test/harness";
import { rawIds, screenText } from "../test/screenGuard";
import { CHAT_TAIL } from "../chat";
import { useCoords } from "../store";
import { ChatPanel } from "./ChatPanel";

// 写作助手面板（模式二，ADR 0019）。喂的每一个字节都来自 `tests/test_frontend_contract.py`
// 从真 app dump 出来的那六份（`chatCreated` / `chats` / `chatDetail` / `chatTurn` /
// `chatStopped` / `chatDeleted`）——**这一页最容易犯的错就是按自己以为的形状写**，
// 而那正是这套契约夹具存在的理由。

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    // 顶栏此刻停在第 2 章 —— 下面有一条断言钉住它真的被发出去了。
    chapter: 2,
    chatOpen: true,
    chatId: null,
    page: "workbench",
  });
});

/** 一个能卡住的响应：中间那一帧（还在跑）快到测不出来，得能停在原地。 */
function gated<T>(value: T) {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  return {
    handler: async () => {
      await gate;
      return value;
    },
    release: () => release(),
  };
}

const say = () => screen.getByRole("textbox", { name: "跟写作助手说" });
const sendBtn = () => screen.getByRole("button", { name: "发送" });

describe("对话摊在中栏右半边", () => {
  it("说过的话两边都在，而且分得出谁说的", async () => {
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    expect(screen.getByText(fixtures.chatDetail.messages[1].text)).toBeInTheDocument();
    expect(screen.getAllByText("你").length).toBeGreaterThan(0);
    expect(screen.getAllByText("写作助手").length).toBeGreaterThan(0);
  });

  it("🔴 **不写「按第几章回答」** —— 那句话把一条不存在的限制说成了规则", async () => {
    // 2026-08-14 反过来了。这一条原来钉的是「屏幕上显示它按第几章回答」，
    // 理由是「那是它判断能不能说破的坐标」。作者看到的却是另一件事：
    // 「按第 722 章回答」读起来是**「只准用这一章的材料」**——而助手能翻目录、
    // 翻别的章的正文和梗概（正下方那句空态自己就这么写着）。
    // 章号真正管的只有「这儿能不能说破」，那属于助手开口时该说的话。
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    expect(screen.queryByText(/按第 \d+ 章回答/)).toBeNull();
  });

  it("会话的内部标识一个字符都不上屏", async () => {
    // 真 dump 里那个 id 是 `前缀:标识` 形状（`src/test/screenGuard.ts` 第三张网）。
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    expect(fixtures.chatDetail.session.id).toMatch(/:/); // 探针：夹具里真的有这么个东西
    expect(rawIds(screenText())).toEqual([]);
  });

  it("**它有多少条历史不上屏** —— 那个数含工具往返，和屏幕上的气泡对不上", async () => {
    renderWithApi(<ChatPanel />);
    const user = userEvent.setup();
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    // 真 dump：9 条历史，2 条上得了屏。摆一个「9 条」出来就是一个看起来很正常的假数字。
    // （那一轮跑了校准 + 封存 + 查约束 + 起一稿，所以历史里有四条工具返回。）
    expect(fixtures.chats[0].message_count).toBe(9);
    expect(fixtures.chatDetail.messages).toHaveLength(2);
    expect(document.body.textContent).not.toMatch(/9 条/);
  });
});

describe("跑一轮：作者按下发送之后那段时间", () => {
  it("章号跟着请求一起发出去 —— 后端有意没给它默认值", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    const spy = vi.spyOn(globalThis, "fetch");

    await user.type(say(), "这一章能说破血脉吗");
    await user.click(sendBtn());

    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/turn/events"));
      expect(call).toBeTruthy();
      const sent = JSON.parse(String((call![1] as RequestInit).body));
      // `run_id` 每一轮都不一样（`chat.ts::newRunId`），所以这儿只能断言它**在**、
      // 而且不空——写死一个值就是把那条「每一轮换一个」的规矩反过来钉住了。
      expect(sent.run_id).toBeTruthy();
      expect({ chapter: sent.chapter, said: sent.said }).toEqual({
        chapter: 2,
        said: "这一章能说破血脉吗",
      });
    });
  });

  it("**刚开的新对话不是一片空白** —— 一句话都没有时画的是「开始写作」那块", async () => {
    // 2026-08-15 作者报的：按「＋ 开一段新的对话」之后面对一片空白。
    // 病根是那条判据写的是 `!chatId`——而新开的一段**是有 id 的**，只是没有话。
    // 判据改成「这一段里没有话」，这一条钉的就是那个差别。
    useCoords.setState({ chatId: "chat_session:ID77" });
    renderWithApi(<ChatPanel />, [
      { match: /\/chats\/[^/]+$/, body: { ...fixtures.chatDetail, messages: [] } },
    ]);

    expect(await screen.findByText("开始写作")).toBeInTheDocument();
    expect(screen.getByText(/说一句就行/)).toBeInTheDocument();
  });

  it("读不出来的时候**不许说「还没说话」** —— 那是一句它不知道真假的话", async () => {
    useCoords.setState({ chatId: "chat_session:ID77" });
    renderWithApi(<ChatPanel />, [
      { match: /\/chats\/[^/]+$/, status: 500, body: {} },
    ]);
    await screen.findByText(/没读出来/);
    expect(screen.queryByText("开始写作")).toBeNull();
  });

  // ── Enter 直接发（2026-08-15）─────────────────────────────────────────────
  //
  // 这一组里**只有第三条**（输入法正在选字）是真的难：前两条错了作者立刻看得见，
  // 第三条错了的症状是「他敲『你好』刚要选字，半句话就飞出去了」——而这个产品的
  // 作者每一句话都是中文敲的，也就是说那条路他每次说话都要走一遍。

  it("按 Enter 直接发出去", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    const spy = vi.spyOn(globalThis, "fetch");

    await user.type(say(), "这一章能说破血脉吗{Enter}");

    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/turn/events"));
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)).said).toBe("这一章能说破血脉吗");
    });
  });

  it("Shift + Enter 是换行，不发", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    const spy = vi.spyOn(globalThis, "fetch");

    await user.type(say(), "第一行{Shift>}{Enter}{/Shift}第二行");

    expect(spy.mock.calls.filter(([url]) => String(url).endsWith("/turn/events"))).toHaveLength(0);
    expect((say() as HTMLTextAreaElement).value).toBe("第一行\n第二行");
  });

  it("🔴 **输入法正在选字的那一下 Enter 不许发** —— 那是「确认这几个字」", async () => {
    // 中文敲「你好」时按下的 Enter 属于输入法，不属于这个输入框。判错了，
    // 作者每说一句话都会先飞出去半句。**两种写法都要认**：Safari 和一部分
    // 输入法在某些版本上只给得出 `isComposing` 和 `keyCode 229` 中的一个。
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    const spy = vi.spyOn(globalThis, "fetch");
    const box = say();
    fireEvent.change(box, { target: { value: "你好" } });

    const sends = () => spy.mock.calls.filter(([url]) => String(url).endsWith("/turn/events"));

    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229 });
    // **等一拍再断言**：发送是异步发出去的，紧接着断言「一次都没发」会在请求
    // 出门之前就通过——那样这条测试拆掉守卫也照样绿（写它的时候真踩了这一下）。
    await new Promise((r) => setTimeout(r, 30));
    expect(sends()).toHaveLength(0);

    // 选完字之后那一下**要发**——否则这条守卫就把发送整个关掉了，而它一样是绿的。
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(sends()).toHaveLength(1));
  });

  it("Ctrl / ⌘ + Enter 照旧能发 —— 别把老手势弄坏", async () => {
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    const spy = vi.spyOn(globalThis, "fetch");
    fireEvent.change(say(), { target: { value: "老手势" } });
    fireEvent.keyDown(say(), { key: "Enter", metaKey: true });

    await waitFor(() =>
      expect(spy.mock.calls.filter(([url]) => String(url).endsWith("/turn/events"))).toHaveLength(1),
    );
  });

  it("**回话区没有打字机**，只有真的秒表 —— 而且这块屏幕自己说清了哪一档才逐字", async () => {
    // 2026-08-12（ADR 0024）之后中间过程真的看得见了，**但两条流不是一回事**：
    // 起草那次调用是流式的，回话那次不是（`plan.stream is False`，后端那条
    // `test_todays_agent_call_is_not_streaming_…` 钉着）。所以回话区仍然一次到位，
    // 2026-08-13：这儿原来还断言屏幕上那句「回话是整段一次出现的，稿子才会一个字一个字
    // 长出来」。**那句话删了**，因为它没有任何条件 —— 端点退回一次性响应时它就是假的，
    // 而且没有一个成熟工具会向用户解释自己的流式语义。屏幕该用状态本身说话：
    // 字在流就让他看见字（下面「稿子真的一个字一个字长出来」那条钉的就是这个）。
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turn.handler }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    await user.type(say(), "先去看看第 1 章");
    await user.click(sendBtn());

    const strip = await screen.findByRole("status");
    expect(within(strip).getByText(/已经 \d+ 秒/)).toBeInTheDocument();
    // 他刚说的那句话立刻占一格：后端做的第一件事就是把它落库，这不是假装。
    expect(screen.getByText("先去看看第 1 章")).toBeInTheDocument();
    // 跑着的时候不许再发一条 —— 后端那一侧是 409。
    expect(say()).toBeDisabled();

    turn.release();
    await screen.findByText(fixtures.chatTurn.message);
  });

  it("跑完之后：后端那句话 + 查了几次，**查到了什么一个字都不给**", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    await user.type(say(), "问一句");
    await user.click(sendBtn());

    // 措辞的唯一出处是后端的 `stop_wording()`，这里原样显示。
    await screen.findByText(fixtures.chatTurn.message);
    expect(document.body.textContent).toContain(`查了 ${fixtures.chatTurn.lookups} 次资料`);
    // 停止原因是机器码（snake_case），一个字都不上屏。
    expect(document.body.textContent).not.toContain(fixtures.chatTurn.reason);
  });

  it("**有调用没报用量就说出来** —— 那笔账是少算的", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "问一句");
    await user.click(sendBtn());

    expect(fixtures.chatTurn.calls_without_usage).toBeGreaterThan(0); // 探针：夹具里真有
    await screen.findByText(/少算/);
  });

  it("跑完把这段对话重读一遍 —— 新长出来的话不能等下一次刷新", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    const spy = vi.spyOn(globalThis, "fetch");

    await user.type(say(), "问一句");
    await user.click(sendBtn());
    await screen.findByText(fixtures.chatTurn.message);

    const reread = spy.mock.calls.filter(
      ([url, init]) =>
        /\/chats\/[^/]+$/.test(String(url)) && ((init as RequestInit)?.method ?? "GET") === "GET",
    );
    expect(reread.length).toBeGreaterThan(0);
  });

  it("还没有一段对话时，发送会先开一段 —— **不预先开空会话**", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ match: /\/chats$/, body: [] }]);
    await screen.findByText(/说一句就行/);
    const spy = vi.spyOn(globalThis, "fetch");

    await user.type(say(), "第一句");
    await user.click(sendBtn());

    await waitFor(() => {
      const posts = spy.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST");
      expect(posts.map(([url]) => String(url).replace(/.*\/chats/, "/chats"))).toEqual([
        "/chats",
        `/chats/${encodeURIComponent(fixtures.chatCreated.id)}/turn/events`,
      ]);
    });
  });

  it("模型没配好那一档：把他打的字还回输入框，不丢", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, status: 422, body: { detail: "模型没配好，先去顶栏设置。" } },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    await user.type(say(), "别把这句弄丢了");
    await user.click(sendBtn());

    await screen.findByText("模型没配好，先去顶栏设置。");
    expect(say()).toHaveValue("别把这句弄丢了");
  });
});

describe("一轮没跑成 —— 那句话留在对话里，不是留在界面状态里", () => {
  // 2026-08-13 作者第一次真用就撞到的那一档：他说了两句，助手一个字都没有，
  // 屏幕上弹过一句提醒，可它活在组件状态里——他再发一句就没了。
  // 「没有必要消失」是他的原话，这一节钉的就是「它不再消失」。
  //
  // 喂的是**真 dump** 的两份：`chatDetailFailed`（库里那一行）+ 一份把回执的
  // `messages` 换成它的 `chatTurn`。手写一份「我以为它长这样」正是这条缝原本的病。
  const notice = fixtures.chatDetailFailed.messages[1];
  const failedReceipt = {
    ...fixtures.chatTurn,
    reason: "model_unreachable",
    message: notice.text,
    reply: "",
    messages: [notice],
  };

  it("**重新打开这段对话，两轮各留一行，各在各的位置**", async () => {
    // 真 dump 的就是作者那块屏幕：你好 → 没跑成 → fff → 没跑成。
    // 两行不许堆在末尾（那读成「最后这一轮失败了两次」）。
    renderWithApi(<ChatPanel />, [
      { match: /\/chats\/[^/]+$/, body: fixtures.chatDetailFailed },
    ]);
    await screen.findByText(fixtures.chatDetailFailed.messages[0].text);

    const lines = [...document.querySelectorAll(".chat-msg")].map((el) => el.textContent ?? "");
    expect(lines).toHaveLength(4);
    expect(lines[0]).toContain("你好");
    expect(lines[1]).toContain(notice.text);
    expect(lines[2]).toContain("fff");
    expect(lines[3]).toContain(notice.text);
    expect(screen.getAllByText("系统")).toHaveLength(2);
    // 它是对话里的一条，不是那个红框（两者活得不一样长，别混）。
    expect(document.querySelectorAll(".err-box")).toHaveLength(0);
  });

  it("**`seq` 撞号不许把一条画没** —— 系统那一行和它后面那句话同号", async () => {
    // 系统那一行带的数是「它前面有几条历史」，不占历史下标 —— 真 dump 里
    // 那条「没跑成」和紧跟其后的「fff」都是 `seq: 1`。拿 `seq` 当 React key，
    // React 会把这两条当成同一个东西，而作者屏幕上少的正是他自己说的那句话。
    const seqs = fixtures.chatDetailFailed.messages.map((m) => m.seq);
    expect(new Set(seqs).size).toBeLessThan(seqs.length); // 探针：夹具里真的撞了
    const complained = vi.spyOn(console, "error").mockImplementation(() => {});
    renderWithApi(<ChatPanel />, [
      { match: /\/chats\/[^/]+$/, body: fixtures.chatDetailFailed },
    ]);
    await screen.findByText(fixtures.chatDetailFailed.messages[0].text);
    const keyed = complained.mock.calls
      .map((args) => args.map(String).join(" "))
      .filter((line) => /same key|duplicate key/i.test(line));
    complained.mockRestore();
    expect(keyed).toEqual([]);
  });

  it("**跑完那一瞬间也只说一遍** —— 回执不许把同一句话再画一次", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { match: /\/chats\/[^/]+$/, body: fixtures.chatDetailFailed },
      { method: "POST", match: /\/turn\/events$/, stream: turnStream(failedReceipt) },
    ]);
    await screen.findByText(fixtures.chatDetailFailed.messages[0].text);
    await user.type(say(), "你好");
    await user.click(sendBtn());

    await waitFor(() => expect(say()).not.toBeDisabled());
    // 那句话在对话里（上面那条钉着），所以回执那一块**一个字都不该再说**：
    // 同一句话在同一块屏幕上出现两次，是这块屏幕拒绝过好几次的东西。
    const onReceipt = [...document.querySelectorAll(".chat-receipt-say")].map(
      (el) => el.textContent ?? "",
    );
    expect(onReceipt.filter((line) => line.includes(notice.text))).toEqual([]);
  });

  it("切去看另一段再切回来，它照样在（红框做不到这件事）", async () => {
    const user = userEvent.setup();
    const other = { ...fixtures.chats[0], id: "chat_session:ID99", title: "另一段对话" };
    renderWithApi(<ChatPanel />, [
      { match: /\/chats$/, body: [fixtures.chats[0], other] },
      { match: /\/chats\/[^/]+$/, body: fixtures.chatDetailFailed },
    ]);
    await screen.findAllByText(notice.text);

    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(screen.getByRole("button", { name: other.title }));
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(screen.getByRole("button", { name: fixtures.chats[0].title }));

    expect(await screen.findAllByText(notice.text)).toHaveLength(2);
  });
});

describe("「停」", () => {
  it("按下去真的打那条路由，而且不等这一轮跑完", async () => {
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turn.handler }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");
    const spy = vi.spyOn(globalThis, "fetch");

    await user.click(screen.getByRole("button", { name: "停" }));

    await waitFor(() =>
      expect(spy.mock.calls.some(([url]) => String(url).endsWith("/stop"))).toBe(true),
    );
    turn.release();
    await screen.findByText(fixtures.chatTurn.message);
  });

  it("**「停」报的是这一轮的标识** —— 跑和停必须是同一个，而且每一轮都换", async () => {
    // 不报的话，一次迟到的「停」会掐掉作者刚发出去的下一轮（`chat.ts::newRunId`
    // 写着那个序列）。这块屏幕这一侧的活儿只有一件：两个请求报同一个数。
    const user = userEvent.setup();
    const first = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: first.handler }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    const spy = vi.spyOn(globalThis, "fetch");

    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");
    await user.click(screen.getByRole("button", { name: "停" }));

    const sent = (suffix: string) =>
      spy.mock.calls
        .filter(([url]) => String(url).endsWith(suffix))
        .map(([, init]) => JSON.parse(String((init as RequestInit).body)).run_id);
    await waitFor(() => expect(sent("/stop")).toHaveLength(1));
    expect(sent("/turn/events")[0]).toBeTruthy();
    expect(sent("/stop")[0]).toBe(sent("/turn/events")[0]);

    first.release();
    await screen.findByText(fixtures.chatTurn.message);

    // **下一轮换一个新的**：上一轮那个还在的话，迟到的「停」就会认成这一轮。
    await user.type(say(), "再跑一个");
    await user.click(sendBtn());
    await waitFor(() => expect(sent("/turn/events")).toHaveLength(2));
    expect(sent("/turn/events")[1]).not.toBe(sent("/turn/events")[0]);
  });

  it("**`stopped=false` 不是失败** —— 那一刻它本来就没在跑，照后端那句话说", async () => {
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turn.handler }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");

    // 真 dump 那一份就是这一档。
    expect(fixtures.chatStopped.stopped).toBe(false);
    await user.click(screen.getByRole("button", { name: "停" }));

    await screen.findByText(fixtures.chatStopped.message);
    expect(document.querySelector(".err-box")).toBeNull(); // 它不是错误
    turn.release();
    await screen.findByText(fixtures.chatTurn.message);
  });

  it("按了停、回执却说「说完了」—— 补一句，别让按钮看起来是坏的", async () => {
    // 后端那份已知限制里写着这一档：停到的是最后一次调用之后，它报 done 是对的。
    // 但作者看到的是「我按了停，屏幕上写说完了」。
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turn.handler },
      { method: "POST", match: /\/stop$/, body: { ...fixtures.chatStopped, stopped: true } },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");
    await user.click(screen.getByRole("button", { name: "停" }));
    turn.release();

    expect(fixtures.chatTurn.reason).toBe("done"); // 探针
    await screen.findByText(/你按下停/);
  });
});

describe("多段对话：侧列表", () => {
  it("列得出来、挑得动、开得了新的", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    await user.click(screen.getByRole("button", { name: "对话列表" }));
    expect(screen.getByRole("button", { name: "＋ 开一段新的对话" })).toBeInTheDocument();
    // 标题就是作者说的第一句话（后端在标题为空时替他填的）。
    await user.click(screen.getByRole("button", { name: fixtures.chats[0].title }));
    expect(useCoords.getState().chatId).toBe(fixtures.chats[0].id);
    // 挑完自己收起来：这一列是盖在对话上面的。
    expect(screen.queryByRole("button", { name: "＋ 开一段新的对话" })).toBeNull();
  });

  it("**断在半路的那一段在列表上看得出来** —— 它和跑完的下一步动作不同", async () => {
    const user = userEvent.setup();
    const halfway = [{ ...fixtures.chats[0], pending_lookups: 2 }];
    renderWithApi(<ChatPanel />, [{ match: /\/chats$/, body: halfway }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    await user.click(screen.getByRole("button", { name: "对话列表" }));
    expect(screen.getByText(/上次断在半路/)).toBeInTheDocument();
    // 不画「恢复」按钮：接着说一句（或者按「接着往下」）就会自动把缺的补上。
    expect(screen.queryByRole("button", { name: "恢复" })).toBeNull();
  });

  it("断在半路时**没有那颗「接着往下」** —— 接着打一句就是了", async () => {
    // 2026-08-15 作者把它撤了（「就不能用户用打字的形式说继续吗」）。它发的是一句
    // 空话（`said=""` 即 resume）。**后端那条路一个字没动**，撤掉的只是这颗按钮。
    //
    // 这条测的是「撤干净了」，而**它撤掉的东西没有别处补**：断在半路那一档，
    // 作者今天只能自己说一句，补不补那几个查询由模型决定。会话列表那一行的提示
    // （下一条断言）是屏幕上仅剩的、告诉他这段对话断过的地方。
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { match: /\/chats$/, body: [{ ...fixtures.chats[0], pending_lookups: 1 }] },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    expect(screen.queryByRole("button", { name: "接着往下" })).toBeNull();

    // 断过这件事本身没被一起撤掉：列表里那一行还说得出来。
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    expect(await screen.findByText(/上次断在半路/)).toBeInTheDocument();
  });

  it("**一轮跑着的时候切去看另一段：秒表和回执不许跟过去**", async () => {
    // 一轮要跑好几分钟，而作者可以同时留着好几段对话 —— 「等的时候切过去看另一段」
    // 是常态。不把这一轮钉在它自己那一段上，另一段屏幕上会长出一句关于别处的话，
    // 而它看起来完全像这儿的事实。
    const user = userEvent.setup();
    const other = { ...fixtures.chats[0], id: "chat_session:ID99", title: "另一段对话" };
    const turn = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [
      { match: /\/chats$/, body: [fixtures.chats[0], other] },
      { method: "POST", match: /\/turn\/events$/, body: turn.handler },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");

    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(screen.getByRole("button", { name: other.title }));

    // 这一段什么都没发生：不画秒表、不画那句待发的话，但要说清输入框为什么是灰的。
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByText("跑一个")).toBeNull();
    expect(screen.getByText(/另一段对话正在跑/)).toBeInTheDocument();

    turn.release();
    await waitFor(() => expect(say()).not.toBeDisabled());
    // 回执属于它自己那一段。**切回去还在**（那一轮的结论没有因为看了一眼别处就消失）。
    expect(screen.queryByText(fixtures.chatTurn.message)).toBeNull();
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(screen.getByRole("button", { name: fixtures.chats[0].title }));
    expect(screen.getByText(fixtures.chatTurn.message)).toBeInTheDocument();
  });

  it("删一段要先确认，删完把摊开的那段放下", async () => {
    const user = userEvent.setup();
    // 删完之后列表真的少一段 —— 不然「放下坐标」会被「没挑过就停在最近那段」当场撤销，
    // 而那不是 bug：那一段确实还在。
    let listed = 0;
    renderWithApi(<ChatPanel />, [
      { match: /\/chats$/, body: () => (listed++ === 0 ? fixtures.chats : []) },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    const spy = vi.spyOn(globalThis, "fetch");

    // 点 × 只是问一句 —— 一段三个月的对话不该一下点掉。
    await user.click(
      screen.getByRole("button", { name: `删掉这段对话：${fixtures.chats[0].title}` }),
    );
    expect(spy.mock.calls.some(([, init]) => (init as RequestInit)?.method === "DELETE")).toBe(
      false,
    );

    await user.click(screen.getByRole("button", { name: "删掉" }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([url, init]) =>
            (init as RequestInit)?.method === "DELETE" &&
            String(url).endsWith(encodeURIComponent(fixtures.chats[0].id)),
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(useCoords.getState().chatId).toBeNull());
  });

  it("正在跑的那一段删不掉 —— 那句拒绝原样说出来，不静默重试", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      {
        method: "DELETE",
        match: /\/chats\//,
        status: 409,
        body: { detail: { error: "chat_busy", params: { action: "delete" } } },
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(
      screen.getByRole("button", { name: `删掉这段对话：${fixtures.chats[0].title}` }),
    );
    await user.click(screen.getByRole("button", { name: "删掉" }));

    await screen.findByText("这段对话正在跑，先按「停」再删。");
  });
});

describe("对话很长的时候", () => {
  it("默认只渲染尾巴，早先那些**一条没丢**，点一下就全在", async () => {
    const user = userEvent.setup();
    const long = {
      ...fixtures.chatDetail,
      messages: Array.from({ length: CHAT_TAIL + 5 }, (_, i) => ({
        ...fixtures.chatDetail.messages[0],
        seq: i,
        text: `第 ${i} 句`,
      })),
    };
    renderWithApi(<ChatPanel />, [{ match: /\/chats\/[^/]+$/, body: long }]);

    await screen.findByText(`第 ${CHAT_TAIL + 4} 句`);
    expect(screen.queryByText("第 0 句")).toBeNull();

    await user.click(screen.getByRole("button", { name: "看更早的 5 条" }));
    expect(screen.getByText("第 0 句")).toBeInTheDocument();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 一轮不再是黑箱（ADR 0024 第二刀）—— 中间过程真的在屏幕上
// ══════════════════════════════════════════════════════════════════════════

/** 真 dump 的那一整轮帧（`tests/test_frontend_contract.py` 抓的原始字节）。 */
const REAL_FRAMES = fixtures.chatTurnEvents;

/** 那一轮里某一条真事件的载荷。**手写一个我以为的形状 = 这条缝原本的病。** */
const realEvent = (kind: string) => {
  const frame = REAL_FRAMES.find((f) => f.includes(`"kind":"${kind}"`));
  if (!frame) throw new Error(`真 dump 里没有 ${kind}`);
  return JSON.parse(frame.split("\ndata: ")[1]);
};

const frame = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

describe("跑到一半：它在做什么，屏幕上真的看得见", () => {
  it("**中间过程一条条到，不是跑完才一起出现**", async () => {
    // 这一刀的全部主张。以前这儿只有一个秒表，一轮三分钟什么都不说。
    // **回执那一帧卡住**：不卡的话这条断言测的是一块已经跑完了的屏幕，
    // 而「跑完了才一起出现」正是它要拦的那个形态。
    const user = userEvent.setup();
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    const middle = REAL_FRAMES.filter((f) => !f.startsWith("event: receipt"));
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: [...middle, held] },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    await user.type(say(), "查一下第 2 章");
    await user.click(sendBtn());
    const strip = await screen.findByRole("status");

    // 那几行是后端写的中文（`TurnEvent.said_to_author`），这一层一个字都没翻。
    const started = realEvent("tool_started");
    await waitFor(() =>
      expect(within(strip).getByText(started.said_to_author)).toBeInTheDocument(),
    );
    // 它这一步说的那整段话也在（`reply_text`，不是逐字拼的）。
    expect(within(strip).getByText(realEvent("reply_text").text)).toBeInTheDocument();
    // **工具名是机器码，它在事件上（界面要分派用），但一个字都不上屏。**
    expect(started.tool).toBeTruthy(); // 探针：真 dump 里确实有这么个东西
    expect(strip.textContent).not.toContain(started.tool);
    // 回执还没到，所以「说完了」这会儿一个字都不该在。
    expect(screen.queryByText(fixtures.chatTurn.message)).toBeNull();

    release(frame("receipt", fixtures.chatTurn));
    await screen.findByText(fixtures.chatTurn.message);
  });

  it("**稿子真的一个字一个字长出来** —— 这是全屏幕唯一逐字的那一格", async () => {
    // 起草那次调用是流式的（`interruptible`），所以 `draft_delta` 真的会一片片来。
    // 后端那个契约夹具里没有它（起草的桩不流式），所以这一格的底子是同一轮里那条
    // **真的** `draft_started`，只改 `kind` 和 `text` —— 字段集合仍然是后端今天那一份。
    //
    // **最后一帧卡住**：那一格只活在这一轮跑着的时候（跑完之后它变成候选稿那一栏），
    // 不卡的话这条断言测的是一块已经不在了的屏幕。
    const user = userEvent.setup();
    const opened = realEvent("draft_started");
    const delta = (text: string) => ({ ...opened, kind: "draft_delta", text, said_to_author: "" });
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          frame("turn", opened),
          frame("turn", delta("风雪落在肩上，")),
          frame("turn", delta("他终于抬起头。")),
          held,
        ],
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "写一稿");
    await user.click(sendBtn());

    // 开跑那一声先到，第一个字还没落下 —— **那一格也要在**，不然一批三稿同时飞的时候
    // 作者看到的是几段凭空出现的字。
    await screen.findByText(opened.said_to_author.replace("。", "…"));
    // 两片拼在一起，中间没有任何分隔 —— 作者读到的是一段连着的正文。
    await screen.findByText("风雪落在肩上，他终于抬起头。");

    release(frame("receipt", fixtures.chatTurn));
    await screen.findByText(fixtures.chatTurn.message);
  });

  it("**流断在半路（一帧回执都没有）** —— 说一句它自己不知道为什么，不编理由", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [REAL_FRAMES[0]], // 开了个头就没了
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "问一句");
    await user.click(sendBtn());

    await screen.findByText(/这一轮没跑成，而系统没能说清是为什么/);
  });

  it("**跑到一半被拒**（那一帧带着后端那句中文）—— 照说，不换成自己的话", async () => {
    const user = userEvent.setup();
    const said = "这段对话在别的窗口里刚往前走了一步，刷新一下再说。";
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [REAL_FRAMES[0], frame("failed", { message: said })],
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "问一句");
    await user.click(sendBtn());

    await screen.findByText(said);
  });

  it("认不出的帧名丢掉就好，**不许整轮崩掉** —— 后端加一种帧不该让旧界面死", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          frame("something_new", { whatever: 1 }),
          ...REAL_FRAMES.filter((f) => !f.startsWith("event: receipt")),
          frame("receipt", fixtures.chatTurn),
        ],
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "问一句");
    await user.click(sendBtn());

    await screen.findByText(fixtures.chatTurn.message);
  });
});

describe("它停下来问了一句（ADR 0024）", () => {
  /** 一轮以「问了作者」收场。**回执那一份是持久的那一份**（刷新之后还在）。 */
  const ASKED = {
    ...fixtures.chatTurn,
    reason: "asked_author",
    message: "它有件事拿不准，问了你一句，正等着你答。",
    asked: {
      question: "这一场你想让萧决知道那件事吗？",
      options: ["让他知道", "先瞒着", "让他半信半疑"],
    },
  };
  const askedRoute = [
    { method: "POST" as const, match: /\/turn\/events$/, stream: turnStream(ASKED) },
  ];

  it("**问题摆成一张卡，不是混在一段话里**", async () => {
    // 一个问句混在 prose 里，作者会当陈述句翻过去 —— 他在读的是「助手说了什么」，
    // 不是「助手在等我」。所以它在结构上（一个有名字的分组）和普通回话分得开。
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, askedRoute);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "这一场怎么写？");
    await user.click(sendBtn());

    const card = await screen.findByRole("group", { name: "它在等你回一句" });
    expect(within(card).getByText(ASKED.asked.question)).toBeInTheDocument();
    for (const option of ASKED.asked.options) {
      expect(within(card).getByRole("button", { name: option })).toBeInTheDocument();
    }
  });

  it("**点一下就等于他答了那一句** —— 不是把选项抄进输入框让他再按一次发送", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, askedRoute);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "这一场怎么写？");
    await user.click(sendBtn());
    await screen.findByRole("group", { name: "它在等你回一句" });
    const spy = vi.spyOn(globalThis, "fetch");

    await user.click(screen.getByRole("button", { name: "先瞒着" }));

    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/turn/events"));
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)).said).toBe("先瞒着");
    });
    // 输入框仍然是空的：他没有被要求抄一遍。
    expect(say()).toHaveValue("");
  });

  it("**「它问了你一句」不算没跑完** —— 不摆那颗「接着往下」请他跳过这个问题", async () => {
    // 在对话里，停下来问就等于这一轮说到这儿了（ADR 0024「为什么不需要 interrupt/resume」）。
    // 摆一颗「接着往下」等于请他跳过去，而它问的正是「不问就得猜、猜错了他看不出来」的事。
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, askedRoute);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "这一场怎么写？");
    await user.click(sendBtn());

    await screen.findByRole("group", { name: "它在等你回一句" });
    expect(screen.queryByRole("button", { name: "接着往下" })).toBeNull();
  });

  it("**没给选项那一档**：不编两个出来，照实说去下面写", async () => {
    const user = userEvent.setup();
    const bare = { ...ASKED, asked: { question: "你想往哪个方向收？", options: [] } };
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: turnStream(bare) },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "问一句");
    await user.click(sendBtn());

    const card = await screen.findByRole("group", { name: "它在等你回一句" });
    expect(within(card).getByText("在下面写一句回它。")).toBeInTheDocument();
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
  });

  it("**流断在「问了」和「回执」之间** —— 那个问题不许因此从屏幕上消失", async () => {
    const user = userEvent.setup();
    const asking = {
      ...realEvent("turn_stopped"),
      kind: "asked_author",
      reason: null,
      said_to_author: "它有件事拿不准，问了你一句，正等着你答。",
      asked: ASKED.asked,
    };
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: [frame("turn", asking)] },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "问一句");
    await user.click(sendBtn());

    // 回执没到（那一轮的收场丢了），所以这块屏幕同时说两件事：
    // 「这一轮没跑成」**和**「它当时问了你这个」。
    await screen.findByText(/这一轮没跑成/);
    const card = screen.getByRole("group", { name: "它在等你回一句" });
    expect(within(card).getByText(ASKED.asked.question)).toBeInTheDocument();
  });
});
