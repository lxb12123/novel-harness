import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
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

  it("**屏幕上显示它按第几章回答** —— 那是它判断「这儿能不能说」的坐标", async () => {
    renderWithApi(<ChatPanel />);
    expect(await screen.findByText("按第 2 章回答")).toBeInTheDocument();
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
    // 真 dump：5 条历史，2 条上得了屏。摆一个「5 条」出来就是一个看起来很正常的假数字。
    // （那一轮要了两个工具：查约束 + 起一稿，所以历史里有两条工具返回。）
    expect(fixtures.chats[0].message_count).toBe(5);
    expect(fixtures.chatDetail.messages).toHaveLength(2);
    expect(document.body.textContent).not.toMatch(/5 条/);
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
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/turn"));
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

  it("**没有打字机，只有一个还在跑的信号 + 一个真的秒表**，而且明说跑完才一次性出现", async () => {
    // 这一版 HTTP 不流式（内部流式）。装成在逐字吐，作者会按那个编出来的节奏
    // 判断它是不是卡住了。
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn$/, body: turn.handler }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    await user.type(say(), "先去看看第 1 章");
    await user.click(sendBtn());

    const strip = await screen.findByRole("status");
    expect(within(strip).getByText(/已经 \d+ 秒/)).toBeInTheDocument();
    expect(strip.textContent).toContain("跑完才会一次性出现整段回话");
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
    await screen.findByText(/跟它说一句话就开始/);
    const spy = vi.spyOn(globalThis, "fetch");

    await user.type(say(), "第一句");
    await user.click(sendBtn());

    await waitFor(() => {
      const posts = spy.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST");
      expect(posts.map(([url]) => String(url).replace(/.*\/chats/, "/chats"))).toEqual([
        "/chats",
        `/chats/${encodeURIComponent(fixtures.chatCreated.id)}/turn`,
      ]);
    });
  });

  it("模型没配好那一档：把他打的字还回输入框，不丢", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn$/, status: 422, body: { detail: "模型没配好，先去顶栏设置。" } },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);

    await user.type(say(), "别把这句弄丢了");
    await user.click(sendBtn());

    await screen.findByText("模型没配好，先去顶栏设置。");
    expect(say()).toHaveValue("别把这句弄丢了");
  });
});

describe("「停」", () => {
  it("按下去真的打那条路由，而且不等这一轮跑完", async () => {
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn$/, body: turn.handler }]);
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
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn$/, body: first.handler }]);
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
    expect(sent("/turn")[0]).toBeTruthy();
    expect(sent("/stop")[0]).toBe(sent("/turn")[0]);

    first.release();
    await screen.findByText(fixtures.chatTurn.message);

    // **下一轮换一个新的**：上一轮那个还在的话，迟到的「停」就会认成这一轮。
    await user.type(say(), "再跑一个");
    await user.click(sendBtn());
    await waitFor(() => expect(sent("/turn")).toHaveLength(2));
    expect(sent("/turn")[1]).not.toBe(sent("/turn")[0]);
  });

  it("**`stopped=false` 不是失败** —— 那一刻它本来就没在跑，照后端那句话说", async () => {
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn$/, body: turn.handler }]);
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
      { method: "POST", match: /\/turn$/, body: turn.handler },
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

  it("断在半路时有一颗「接着往下」，它发的是**空话**（resume）", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { match: /\/chats$/, body: [{ ...fixtures.chats[0], pending_lookups: 1 }] },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    const spy = vi.spyOn(globalThis, "fetch");

    await user.click(screen.getByRole("button", { name: "接着往下" }));

    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/turn"));
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)).said).toBe("");
    });
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
      { method: "POST", match: /\/turn$/, body: turn.handler },
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

  it("正在跑的那一段删不掉 —— 后端那句 409 原样说出来，不静默重试", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      {
        method: "DELETE",
        match: /\/chats\//,
        status: 409,
        body: { detail: { error: "chat_busy", message: "这段对话正在跑，先按「停」再删。" } },
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
