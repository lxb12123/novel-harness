import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { engineWords, machineWords, rawIds, screenText } from "../test/screenGuard";
import { useCoords } from "../store";
import { RightPanel } from "./RightPanel";

// **对抗性验证（浏览器一侧）**：两个「改一条已生效事实」的入口有没有踩到四条线。
//
// `tests/test_canon_edit_boundary.py` 量的是**源码**（`.mutate({…})` 的键、`<input>` 标签）
// 和**后端出参**。这份量的是中间那一段：**浏览器真的发出去了什么、屏幕上真的印出了什么。**
// 两者拦的不是同一种失败——
//
// - 静态扫描看不见 `hooks.ts` / `client.ts`：请求体在组件里干净，在 `mutationFn` 里被
//   加一个字段，源码扫描一个字节都察觉不到。
// - 静态扫描也看不见「后端算出来的一句话被原样摆上屏」：那句话在前端源码里根本不存在。
//
// ── 判据不是词表 ──────────────────────────────────────────────────────────
//
// 这个仓库已经为「词表只覆盖写它那天想得到的几个词」吃过两次亏（`correctionError.ts`
// 那张被删掉的映射表；`ActivityLog.test.tsx` 那条漏掉 `KNOWS` / `BELIEVES` 的词表）。
// 所以线 2 量的是**形状**，而且那套判据**全仓只有一份**，住在
// `src/test/screenGuard.ts`（它的自守卫在 `screenGuard.test.ts`：五个真的上过屏的
// 违规当探针）。这里只**用**它，不在这个文件里再抄一份。

const PROJECT = "project:ID1";

/** **真后端真的会这样答，而且答里没有一句话。**
 *
 *  形状由 `tests/test_canon_edit_boundary.py::test_these_two_routes_can_answer_with_a_bare_code`
 *  从真 app 钉住（那条红了 = 这里的探针过期了）。这不是「手写夹具」那条禁令的例外，
 *  是它的反面：夹具是**给组件喂正常数据**用的，而这是一个故意的坏形态，
 *  正常数据里按定义不会有它。 */
const PROJECT_GONE = { detail: { error: "project_not_found", project_id: PROJECT } };

type Calls = { mock: { calls: unknown[][] } };
const posts = (spy: Calls) =>
  spy.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
const bodyOf = (spy: Calls, i = 0) =>
  JSON.parse(String((posts(spy)[i][1] as RequestInit).body)) as Record<string, unknown>;

const CHAPTERISH = /chapter|valid_from|valid_to|since|^ch$|^at$/i;

beforeEach(() => {
  useCoords.setState({
    projectId: PROJECT,
    chapter: 1,
    cast: "",
    activeTab: "roster",
    focusEventId: null,
  });
});

// ── 打开两个编辑器 ────────────────────────────────────────────────────────

/** 右栏「待确认」→ 展开第一条已确认情节的名单，并勾掉一个人。 */
async function openCast(user: ReturnType<typeof userEvent.setup>, extra: Parameters<typeof renderWithApi>[1] = []) {
  renderWithApi(<RightPanel />, extra);
  await user.click(await screen.findByRole("button", { name: /^待确认/ }));
  const first = fixtures.eventsCanon[0];
  const heads = await screen.findAllByRole("button", { name: first.event.summary });
  await user.click(heads[0]);
  const knowers = await screen.findByRole("group", { name: "知道这件事的人" });
  await user.click(within(knowers).getByRole("checkbox", { name: first.knowers[1].name }));
  return document.querySelector(".cast-editor") as HTMLElement;
}

// ══════════════════════════════════════════════════════════════════════════
// 线 1：章号 —— 作者永不填（约束 10 / ADR 0006）
// ══════════════════════════════════════════════════════════════════════════

describe("线 1：作者永不填章号", () => {
  it("名单那一半同理 —— 新知情人的生效章只能由那条情节自己定", async () => {
    const user = userEvent.setup();
    await openCast(user);
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(screen.getByRole("button", { name: "保存名单" }));

    await waitFor(() => expect(posts(spy)).toHaveLength(1));
    const body = bodyOf(spy);
    expect(Object.keys(body).filter((k) => CHAPTERISH.test(k))).toEqual([]);
    expect(Object.keys(body).sort()).toEqual([
      "expected_canon_version",
      "knower_ids",
      "participant_ids",
    ]);
  });

  it("编辑器里**渲染出来的**没有一个能敲数字的框", async () => {
    // 源码扫的是 `<input type="number">` 这一种写法。运行时量的是 role：
    // 一个从别处 import 来的 `<NumberBox/>` 在源码里长得完全无害。
    // （2026-08-24 之前这条量的是**两个**编辑器；矩阵那一格随秘密下线删了。）
    const user = userEvent.setup();
    const cast = await openCast(user);
    expect(within(cast).queryAllByRole("spinbutton")).toHaveLength(0);
    for (const box of within(cast).queryAllByRole("textbox")) {
      expect(box.getAttribute("inputmode")).not.toBe("numeric");
      expect((box as HTMLInputElement).placeholder).not.toMatch(/章/);
    }
  });

  it("**守卫的自守卫**：同一个判据看得见一个章号字段", () => {
    expect(["character_id", "since_chapter"].filter((k) => CHAPTERISH.test(k))).toEqual([
      "since_chapter",
    ]);
    expect(["valid_from", "ch", "at"].filter((k) => CHAPTERISH.test(k))).toHaveLength(3);
    // 干净的键不许被咬——假红会让人把守卫关掉。
    expect(["believed_value", "knower_ids", "to_type"].filter((k) => CHAPTERISH.test(k))).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 线 2：文案 —— 屏幕上不许出现机器码
// ══════════════════════════════════════════════════════════════════════════

describe("线 2：屏幕上不许出现机器码", () => {
  it("名单那一半同理", async () => {
    const user = userEvent.setup();
    await openCast(user, [
      { method: "POST", match: /\/canon\/events\/.*\/cast$/, status: 404, body: PROJECT_GONE },
    ]);
    await user.click(screen.getByRole("button", { name: "保存名单" }));

    const box = await screen.findByText((_, el) => el?.className === "err-box");
    expect(machineWords(box.textContent ?? "")).toEqual([]);
  });
  it("待确认那一格摊开时也一样", async () => {
    const user = userEvent.setup();
    await openCast(user);
    expect(machineWords(document.body.textContent ?? "")).toEqual([]);
  });

  // **这里原本有一条「守卫的自守卫」。** 判据搬进 `src/test/screenGuard.ts` 之后，
  // 它跟着搬去了 `screenGuard.test.ts`——判据只有一份，验判据的那条也只该有一份，
  // 否则改窄了网只有一半的自守卫会红。
});

// ══════════════════════════════════════════════════════════════════════════
// 线 3：秘密内容 —— 名字必须在，正文一个字都不许在
// ══════════════════════════════════════════════════════════════════════════

describe("线 3：改的是哪一条说得出来，而整个对象不许发回去", () => {
  it("名单那一半发的也只有 id，不是整个人物对象", async () => {
    const user = userEvent.setup();
    await openCast(user);
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(screen.getByRole("button", { name: "保存名单" }));

    await waitFor(() => expect(posts(spy)).toHaveLength(1));
    const knowers = bodyOf(spy).knower_ids as string[];
    expect(knowers.every((k) => typeof k === "string" && k.startsWith("character:"))).toBe(true);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 线 4：`actor` —— 前端说不了「谁改的」
// ══════════════════════════════════════════════════════════════════════════

describe("线 4：前端说不了「谁改的」", () => {
  it("名单那一半同理", async () => {
    const user = userEvent.setup();
    await openCast(user);
    const spy = vi.spyOn(globalThis, "fetch");
    await user.click(screen.getByRole("button", { name: "保存名单" }));
    await waitFor(() => expect(posts(spy)).toHaveLength(1));

    const said = /actor|author|source|by|who_/i;
    expect(Object.keys(bodyOf(spy)).filter((k) => said.test(k))).toEqual([]);
  });

  it("**守卫的自守卫**：判据看得见一个 actor 字段", () => {
    const said = /actor|author|source|by|who_/i;
    expect(["character_id", "actor"].filter((k) => said.test(k))).toEqual(["actor"]);
    expect(["knower_ids", "expected_canon_version"].filter((k) => said.test(k))).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 第二轮：把判据放宽到 `machineWords` 罩不住的两块
// ══════════════════════════════════════════════════════════════════════════
//
// `machineWords` 是 **snake_case 形状**。它天生罩不住两块，而这次要验的四条线恰好落在那两块上：
//
// 1. **大写枚举**：`KNOWS` / `BELIEVES` / `PROVISIONAL` / `Character` 一个下划线都没有。
//    （`engineWords`）
// 2. **属性里的字**：`textContent` 不含 `aria-label` / `title` / `placeholder`。
//    读屏的作者听见的正是 `aria-label`——它和屏幕上的字是同一块屏。（`screenText`）
//
// 三张网和 `screenText` 都在 `src/test/screenGuard.ts`，自守卫在它旁边那份 test 里。

describe("线 2 续：大写枚举和属性里的字", () => {
  it("名单那一半同理", async () => {
    const user = userEvent.setup();
    await openCast(user);
    const text = screenText();
    expect(engineWords(text)).toEqual([]);
    expect(machineWords(text)).toEqual([]);
    expect(rawIds(text)).toEqual([]);
  });

  it("**待确认那一格里的提案卡**也一样 —— 那儿曾经印着一串截断的内部编号", async () => {
    // `ProposalReviewTab` 拿 `subject_id` / `target_id` 去花名册里查名字，查不到就
    // `id.slice(-6)`——屏幕上是 `n:ID22`（`"location:ID22"` 的后六位）。花名册和队列
    // 是两条独立缓存，新节点在前者里缺席一拍就会走到那条兜底。今天名字由后端连着
    // 提案一起给（`node_refs`），出参自足。
    const user = userEvent.setup();
    renderWithApi(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: /^待确认/ }));
    await screen.findByText(/关系冲突/);

    const text = screenText();
    expect(rawIds(text)).toEqual([]);
    expect(machineWords(text)).toEqual([]);
    expect(engineWords(text)).toEqual([]);
    // 反面也得成立：那两个地点**说得出名字**（过度收窄一样是 bug）。
    expect(text).toContain("青云城");
    expect(text).toContain("北荒");
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 线 2 再续：**后端说什么，屏幕上就是什么**
// ══════════════════════════════════════════════════════════════════════════
//
// 上面那几条量的是「后端一句话都没写」的那一格（前端必须自己兜底）。这一条量的是反面：
// **后端写了话的时候，前端一个字都不许改。**
//
// 两条合起来才说得清「脏句子该在哪儿修」。2026-08-11 实测：名单编辑器上有三种拒绝的
// 话不是 `corrections.py` 写的，而是 `graph/sqlite_events.py` 写给维护者的诊断——
// `corrections._event_failure` 用 `str(exc)` 整句转发，于是
// `event 不存在或跨项目：event:01J…` 摆到了小说作者脸上。
//
// **这条断言故意让脏话上屏。** 它不是在说「脏话可以上屏」，是在把
// 「前端改不了这件事」变成一件 CI 事项：谁想在这儿加一张「码 → 中文」的表来遮它，
// 这条先红。脏话本身归 `tests/test_canon_edit_boundary.py::
// test_the_cast_editor_refusals_borrowed_from_the_event_store_are_clean` 管。

/** 后端**写了话**的那一类拒绝。形状（`detail.error` + `detail.message`）由
 *  `api/review.py::_correction_error` 决定，Python 那侧每一条拒绝断言都在读它。 */
const DIRTY = "event 不存在或跨项目：event:ID30";
const SPOKE = { detail: { error: "fact_not_found", message: DIRTY } };

describe("线 2 再续：措辞的源只有一个", () => {
  it("名单那一半：后端的句子原样上屏，一个字都不翻", async () => {
    const user = userEvent.setup();
    await openCast(user, [
      { method: "POST", match: /\/canon\/events\/.*\/cast$/, status: 404, body: SPOKE },
    ]);
    await user.click(screen.getByRole("button", { name: "保存名单" }));

    const box = await screen.findByText((_, el) => el?.className === "err-box");
    expect(box.textContent).toContain(DIRTY);
    // 反过来：它**没有**被洗过。洗了就等于前端有了第二份措辞源。
    expect(box.textContent).not.toContain("情节");
  });
});
