import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { useCoords } from "../store";
import { ChatPanel } from "./ChatPanel";
import { ChatRules } from "./ChatRules";

// **对抗性验证：规矩面板**（[ADR 0023](docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二）。
//
// `ChatRules.test.tsx` 量的是这块面板自己的行为，`DevTerms.guard.test.tsx` 量的是它上面
// 有没有研发术语。**这份文件只量那两份都够不着的三件事**，判据都是「屏幕上会不会出现
// 一句它不知道真假的话」：
//
// 1. 🔴 **翻一章之后上一章的规矩真的没了** —— ADR 0023 的安全方向是 fail-open
//    （拿不准就放掉），和 `must_not_reveal` 正好相反。界面把它翻过来的形态不是崩，
//    是「灰着但还摆在那儿」，而作者会以为它还管着。
// 2. 🔴 **零态那句「为什么没了」** —— 出参**分不出**两种过期（翻页 / 作者又开口，
//    `tests/test_rules_panel.py` 钉着这条），而屏幕上写死了其中一种，
//    且写死的**不是**默认那一种。
// 3. **兜底那几支的扫描面** —— 那两份合起来漏掉了四支：清单 404（后端只给了一个码，
//    而这个仓库真的把 `chat_not_found` 印到过屏幕上）、取消撞上 409 / 422、还在读。
//
// 喂进来的每一个字节仍然来自 `api.json`（真 dump）。**故意的坏形态（404 / 500）不算
// 手写夹具**：夹具是喂正常数据用的，坏形态按定义不在里面（同 `DevTerms.guard.test.tsx`）。

const PID = "project:ID1";
const CHAT = "chat_session:ID47";

/** 真 dump 那两条：一条章级（说到第二遍升上来的）、一条批级。 */
const [WIDE, BATCH] = fixtures.chatRules.rules;

const panel = (chapter = 2) => <ChatRules pid={PID} chatId={CHAT} chapter={chapter} />;

/** 两章各给一份真 dump：第 2 章有两条，第 7 章一条都不作数了。 */
const BY_CHAPTER = [
  { match: /\/rules\?chapter=7/, body: fixtures.chatRulesExpired },
  { match: /\/rules\?chapter=2/, body: fixtures.chatRules },
];

beforeEach(() => {
  useCoords.setState({
    projectId: PID,
    // 顶栏此刻停在第 2 章。下面几条会把它推到第 7 章去。
    chapter: 2,
    chatOpen: true,
    chatId: null,
    page: "workbench",
  });
});

/** 摊开写作助手那一栏上的规矩面板（收着的时候它一个像素都不占）。 */
async function openThePanel(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText(fixtures.chatDetail.messages[0].text);
  await user.click(await screen.findByRole("button", { name: /这一章的规矩/ }));
}

// ══════════════════════════════════════════════════════════════════════════
// 一、🔴 翻一章：上一章的规矩**没了**，不是灰着还在
// ══════════════════════════════════════════════════════════════════════════

describe("翻一章", () => {
  it("🔴 上一章那两条从面板上整个消失，不是灰着还摆在那儿", async () => {
    // ADR 0023 把方向写死了：**作者的偏好拿不准就放掉**（留着 = 第 200 章写不出打戏，
    // 而作者不知道为什么）。引擎那一侧是对的（`!=` 而不是 `>`），这一条量的是
    // **界面有没有把它翻过来** —— 最像的那种坏法是把上一章的规矩灰着留在清单上，
    // 于是作者以为它还管着，而模型那边它早就不在了。
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, BY_CHAPTER);
    await openThePanel(user);
    await screen.findByText(WIDE.text);

    act(() => useCoords.getState().setChapter(7));

    // 第 7 章那份真 dump 到屏幕上了 —— 也就是说这一次真的按新坐标重问过一次。
    await screen.findByText(/一条都不作数了/);
    expect(screen.queryByText(WIDE.text)).toBeNull();
    expect(screen.queryByText(WIDE.scope)).toBeNull();
    expect(screen.queryByText(BATCH.text)).toBeNull();
  });

  it("🔴 收起来的时候也得跟着掉 —— 按钮上那个数是唯一看得见的部分", async () => {
    // 面板收着时作者只看得见这颗按钮。它还写着「2」而清单已经空了的话，
    // **「看得见」那一半就是假的**：他会一直以为这一章有两条规矩在管着。
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, BY_CHAPTER);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await screen.findByRole("button", { name: `这一章的规矩 ${fixtures.chatRules.rules.length}` });

    act(() => useCoords.getState().setChapter(7));

    await screen.findByRole("button", { name: "这一章的规矩" });
    expect(screen.queryByRole("button", { name: /这一章的规矩 \d/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "这一章的规矩" }));
    await screen.findByText(/一条都不作数了/);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 二、🔴 零态那句「为什么没了」
// ══════════════════════════════════════════════════════════════════════════

describe("定过、这会儿都不作数了", () => {
  it("🔴 不许把原因说死成「你往后翻了」—— 默认那一档根本不涉及翻页", async () => {
    // ── 出参分不出这一档是哪一种过期 ────────────────────────────────────
    //
    // `{"chapter": 3, "rules": [], "expired": 1}` 有两条来路，**一个字节都不差**
    // （`tests/test_rules_panel.py::test_the_expired_count_cannot_say_why_they_stopped_counting`）：
    //
    // | 怎么没的 | 作者做了什么 |
    // |---|---|
    // | 翻页 | 他从第 2 章翻到了第 3 章 |
    // | **说了下一句话** | 他**一直待在第 3 章**，只是又开口了 |
    //
    // 而第二种是 ADR 0023「取窄」的**默认档**：说一遍的规矩活到他下一次开口为止。
    // 也就是说，作者最常撞见的那一次「都不作数了」根本没翻过页。
    //
    // 只说翻页的代价不是「少说了一句」，是**给他一个恰好反着的心智模型**：
    // 「只要我不翻页，规矩就还在。」下一次他就会跳过一件他以为已经交代过的事——
    // 正是这句话本来要防的那个后果。
    renderWithApi(panel(7), [{ match: /\/rules\?/, body: fixtures.chatRulesExpired }]);
    await screen.findByText(/一条都不作数了/);

    // 后端在每一条**活着的**规矩上都写清了它怎么放掉，而那是两条不同的路：
    expect(WIDE.scope).toContain("下一章"); // 章级：翻页才放掉
    expect(BATCH.scope).toContain("再说一句话"); // 批级（默认）：一页不翻也放掉

    const text = screenText();
    expect(text).toMatch(/下一章|往后翻|翻页/);
    expect(text).toMatch(/再说一句|又说了|说了下一句|又开口/);
  });

  it("「一条都没定过」仍然不许说成「定过、都不作数了」", async () => {
    // 作者刚亲手取消完也落在这一档（`expired` 不含被撤销的），说反了他会去找一条
    // 他刚刚亲手点掉的规矩。
    renderWithApi(panel(), [{ match: /\/rules\?/, body: fixtures.chatRulesNone }]);
    await screen.findByText(/还没有规矩在管着/);
    expect(screenText()).not.toMatch(/不作数/);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 三、🔴 一句关于别处的话，长得像这儿的事实
// ══════════════════════════════════════════════════════════════════════════

describe("取消失败那句红字属于它自己那一章", () => {
  it("🔴 翻到下一章之后它不许还挂在那儿", async () => {
    // 这块屏幕上有过一模一样的病，而且已经被修过一次：`ChatPanel` 里那条
    // `failureHere = runFor === chatId ? failure : null`——「那一轮的失败**属于它自己
    // 那一段**……不钉住的话，另一段上会长出一个红框，而那一段什么都没发生」。
    //
    // 规矩这一块的坐标是**（这一段对话 × 这一章）**，所以换一章和换一段对话是同一件事。
    // 换对话那一侧今天是对的（`setRulesOpen(false)` 把整块面板卸掉了），
    // **换章那一侧没人管**：面板原地留着，那条失败跟着翻了页。
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "DELETE", match: /\/rules\/\d+$/, status: 500, body: {} },
      ...BY_CHAPTER,
    ]);
    await openThePanel(user);
    await screen.findByText(WIDE.text);

    await user.click(screen.getByRole("button", { name: `取消这条规矩：${WIDE.text}` }));
    await user.click(screen.getByRole("button", { name: "取消它" }));
    await screen.findByText(/没能取消/);

    act(() => useCoords.getState().setChapter(7));

    await screen.findByText(/一条都不作数了/);
    expect(screen.queryByText(/没能取消/)).toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 四、兜底那几支的扫描面（`DevTerms.guard.test.tsx` 漏掉的四支）
// ══════════════════════════════════════════════════════════════════════════

describe("扫描面：那四支正常数据下永远不亮的分支", () => {
  it("清单 404：后端只给了一个码，屏幕上不许出现它", async () => {
    // 这段对话在另一个标签页里被删掉了。**这几条路由的 404 恰恰只有码没有话**
    // （`{"error":"chat_not_found",…}`，`ChatPanel` 那句注释写着「真 app 打过」），
    // 而 `ApiError.message` 在没有 `message` 时**退回 `body.error`**——
    // 照着它写就是把 `chat_not_found` 十四个字母摆在小说作者脸上。
    renderWithApi(panel(), [
      {
        match: /\/rules\?/,
        status: 404,
        body: { detail: { error: "chat_not_found", chat_id: CHAT } },
      },
    ]);
    await screen.findByText(/没读出来/);
    expect(devTerms(screenText())).toEqual([]);
    // 探针：那个码真的在响应里 —— 不在的话上面那句什么都没证明。
    expect(devTerms("chat_not_found")).toEqual(["chat_not_found"]);
  });

  it.each([
    [
      "正在跑那一轮",
      409,
      { error: "chat_busy", chat_id: CHAT, message: "这段对话正在跑，跑完（或者按「停」）再取消这条规矩。" },
    ],
    [
      "手上那份清单已经旧了",
      422,
      { error: "rule_not_found", chat_id: CHAT, message: "找不到要取消的那条规矩——它不在这段对话里。" },
    ],
  ] as const)("取消撞上「%s」：后端那句话原样上屏，一个码都不跟着", async (_name, status, detail) => {
    const user = userEvent.setup();
    renderWithApi(panel(), [
      { method: "DELETE", match: /\/rules\/\d+$/, status, body: { detail } },
    ]);
    await screen.findByText(WIDE.text);
    await user.click(screen.getByRole("button", { name: `取消这条规矩：${WIDE.text}` }));
    await user.click(screen.getByRole("button", { name: "取消它" }));

    await screen.findByText(detail.message);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("还在读那一档：它得说一句，而且不许说成「这一章没有规矩」", async () => {
    // 后端还没回话时屏幕上是什么，`ChatRules.test.tsx` 一次都没扫过——
    // 而「一块空面板」正是这个仓库为「静默的零」栽过五次的那个形态。
    renderWithApi(panel(), [{ match: /\/rules\?/, body: () => new Promise(() => {}) }]);
    await screen.findByText(/正在看/);
    expect(screenText()).not.toMatch(/还没有规矩在管着|不作数/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("那个 `seq` 在**二次确认摊开之后**也一个字符都不上屏", async () => {
    // `DevTerms.guard.test.tsx` 只在正常那一屏扫过它。确认那一支多画了三样东西
    // （警告句 + 两颗按钮），而它们中的任何一个顺手把「第几条」写出来都不会有东西报错：
    // **三张网都认不出一个裸数字**。
    const user = userEvent.setup();
    renderWithApi(panel());
    await screen.findByText(WIDE.text);
    await user.click(screen.getByRole("button", { name: `取消这条规矩：${WIDE.text}` }));
    await screen.findByText(/不再管着你的稿子/);

    const shown = screenText();
    for (const rule of fixtures.chatRules.rules) {
      expect(shown).not.toContain(String(rule.seq));
    }
    expect(devTerms(shown)).toEqual([]);
  });
});
