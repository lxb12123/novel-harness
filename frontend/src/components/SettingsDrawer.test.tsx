import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { SettingsDrawer } from "./SettingsDrawer";

type Calls = { mock: { calls: [unknown, RequestInit?][] } };

/** 最后一次保存**真的发出去**的请求体。
 *
 *  为什么非看这个不可：这一格的规矩全在「发了什么」上——发 `null` 是收回那个数，
 *  不发这个键才是保持原值。只断言界面的话，两者长得一模一样。 */
function savedBody(spy: Calls): Record<string, unknown> {
  const call = spy.mock.calls
    .filter(([url, init]) => /\/api\/settings$/.test(String(url)) && init?.method === "PUT")
    .at(-1);
  return JSON.parse(String(call?.[1]?.body)) as Record<string, unknown>;
}

const watchFetch = () => vi.spyOn(globalThis, "fetch") as unknown as Calls;

/** 切到「前文长度」那一栏。
 *
 *  **左右分栏之后这一步是必须的**：那一栏的东西默认不在屏幕上，
 *  漏掉它的断言不会红，只会去查一个根本没渲染的框——那是假绿，不是通过。 */
const openLength = () => fireEvent.click(screen.getByRole("tab", { name: "前文长度" }));

describe("AI 设置（BYOK）", () => {
  it("用自然语言说明已保存的地址、模型和密钥", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    expect(await screen.findByDisplayValue("https://api.deepseek.com")).toBeInTheDocument();
    expect(screen.getByDisplayValue("deepseek-v4-flash")).toBeInTheDocument();
    expect(screen.getByText("API 密钥")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/M2|ADR|kill-gate|修正案|实验状态/);
    // 完整钥匙永不进 DOM。**2026-08-14 起这条尤其只剩测试在守**：屏幕上那句
    // 「不会显示完整密钥」按作者要求撤了，于是「它到底显没显」在界面上已经无话可说，
    // 只有这一行断言拦得住。
    expect(document.body.textContent).not.toContain("sk-contract-key");
  });

  it("粘一把新钥匙、按应用，框回到那排点（**不关窗**）", async () => {
    // 2026-08-14 脚条撤了之后「应用」不再兼职关窗：作者可能还要接着改另一栏。
    // 存完把框清空，它就回到「已经有一把钥匙」那个样子——留着他刚敲的字在框里，
    // 看起来像是还没存进去。
    const onClose = vi.fn();
    renderWithApi(<SettingsDrawer onClose={onClose} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    const key = screen.getByLabelText("API 密钥") as HTMLInputElement;
    fireEvent.change(key, { target: { value: "sk-new-key" } });
    fireEvent.click(screen.getByRole("button", { name: "应用" }));

    await waitFor(() => expect(key.value).toBe(""));
    // 框空了 = 又没东西可露，那只眼睛跟着走（它只在框里有字的时候在）。
    expect(screen.queryByRole("button", { name: /密钥$/ })).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("已经设过的那把：框里是一排点，而**那儿没有眼睛**", async () => {
    // 眼睛只能露「作者刚敲进去的这一把」：已存的那把后端从不回吐，框里那排点是
    // placeholder **不是值**——所以在这一档按下去，屏幕上不会有任何变化。
    // 一颗点了不动的按钮就是一句假话（作者：「能点也没有用，这个点击之后没有变化，
    // 说明默认就是黑点你这个是错误的设计」）。做成灰的（点不动）此前也被否了。
    // **对的做法只剩一个：没得露的时候它根本不在，在的时候它一定管用。**
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    // **先等服务器那份落地**：标签在数据回来之前就渲染好了，抢在它前面拿到的
    // 是「一把都没设过」那一档的框——那时候本来就不该有点。
    await screen.findByDisplayValue("deepseek-v4-flash");
    const key = screen.getByLabelText("API 密钥") as HTMLInputElement;
    expect(key.value).toBe(""); // 空 = 保持原钥匙，这是后端的判据
    // **只有点，没有尾巴**：后端那份 `api_key_preview` 一个字符都不上屏。
    expect(key.placeholder).toBe("•".repeat(12));
    expect(screenText()).not.toContain(fixtures.settingsSaved.api_key_preview.replace(/^\**/, ""));
    expect(key).toHaveAttribute("type", "password");
    expect(screen.queryByRole("button", { name: /密钥$/ })).toBeNull();

    // 敲进去一把，它才出现——**出现即可用**。
    fireEvent.change(key, { target: { value: "sk-new-key" } });
    fireEvent.click(screen.getByRole("button", { name: "显示密钥" }));
    expect(key).toHaveAttribute("type", "text");
    // 露出来之后名字得跟着变，否则读屏一直念「显示密钥」而它已经在显示了。
    fireEvent.click(screen.getByRole("button", { name: "隐藏密钥" }));
    expect(key).toHaveAttribute("type", "password");
  });

  it("框清空 = 眼睛跟着收走，**而且明文那一档也收回去**", async () => {
    // 留着「正在显示明文」这一档的话，下一把粘进来的钥匙会**直接以明文出现**——
    // 而作者没有按过任何一个键，屏幕上凭空多出一串谁都看得见的字符。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    const key = screen.getByLabelText("API 密钥") as HTMLInputElement;

    fireEvent.change(key, { target: { value: "sk-new-key" } });
    fireEvent.click(screen.getByRole("button", { name: "显示密钥" }));
    expect(key).toHaveAttribute("type", "text");

    fireEvent.change(key, { target: { value: "" } });
    expect(screen.queryByRole("button", { name: /密钥$/ })).toBeNull();
    fireEvent.change(key, { target: { value: "sk-another" } });
    expect(key).toHaveAttribute("type", "password");
  });

  it("一把都没设过的时候，那排点不出现（它说的是「这儿本来有东西」）", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settings },
    ]);
    const key = (await screen.findByLabelText("API 密钥")) as HTMLInputElement;
    expect(key.placeholder).not.toContain("•");
    // 空框同样没有眼睛：一个字都没敲，它照样什么都露不出来。
    expect(screen.queryByRole("button", { name: /密钥$/ })).toBeNull();
  });

  it("框空着提交时**不带** `api_key` 这一位（= 保持原钥匙）", async () => {
    // 屏幕上原来那句「留空则保留原来的密钥」2026-08-14 撤了，界面不再解释这件事——
    // **那件事本身还在**，而它现在只由这条断言守着。发一个空串上去就是把钥匙清掉。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    const spy = watchFetch();
    fireEvent.change(await screen.findByDisplayValue("deepseek-v4-flash"), {
      target: { value: "deepseek-v4" }, // 只改模型，钥匙一个字没碰
    });
    fireEvent.click(screen.getByRole("button", { name: "应用" }));
    await waitFor(() => expect(savedBody(spy).model).toBe("deepseek-v4"));
    expect(savedBody(spy)).not.toHaveProperty("api_key");
  });
});

describe("手填「一次能读多少」", () => {
  // 这一格是**自建端点唯一的出路**：那份公开表按主机名一刀切，本机/内网的服务永远
  // 认不出，于是上文一直是最短的 800 字。差 50 倍，而且变短了不报错，只显得模型变笨。

  it("没填时是空框，填过就把他填的那个数显示回来", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    openLength();
    expect(await screen.findByDisplayValue("128000")).toBeInTheDocument();

    // 另一份真 dump：一个字都没填过的时候，框里不许出现 0（那是另一个意思）。
    expect(fixtures.settings.context_window).toBeNull();
  });

  it("填一个数，保存时按数字发出去", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    const spy = watchFetch();
    // **先等已保存的那份落地再改**：这扇窗在拿到服务器那份时会重置整张表单，
    // 抢在它前面敲的字会被抹掉（作者手快时也一样，只是他会以为自己没按对）。
    openLength();
    await screen.findByDisplayValue("128000");
    fireEvent.change(screen.getByPlaceholderText(/32768/), { target: { value: "131072" } });

    const save = screen.getByRole("button", { name: "应用" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    // 发的是数字不是字符串：后端那一位是整数，字符串会被 422 顶回来。
    await waitFor(() => expect(savedBody(spy).context_window).toBe(131072));
  });

  it("清空那个框 = 收回这个数（**不是保持原值**）", async () => {
    // 钥匙留空是「保持原值」，因为屏幕上根本不回显它；这个数回显着，
    // 同样的规矩会让框变成只进不出的洞——填错一次就再也改不回自动推断。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settings },
    ]);
    const spy = watchFetch();
    openLength();
    fireEvent.change(await screen.findByDisplayValue("128000"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "应用" }));
    // **键必须在**：后端拿「带没带这个键」当判据，漏掉它就成了「保持原值」。
    await waitFor(() => expect(savedBody(spy)).toHaveProperty("context_window", null));
  });

  it("认不出来的输入当没填，不替他猜一个数", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settings },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settings },
    ]);
    openLength();
    await screen.findByText("模型一次能读多少");
    fireEvent.change(screen.getByPlaceholderText(/32768/), { target: { value: "32k" } });
    // 猜错的方向没有一个是安全的：猜大了请求被拒，猜小了上文悄悄变短。
    expect(screen.getByRole("button", { name: "应用" })).toBeDisabled();
  });

  it("屏幕上不出现研发术语，也不给这个数安一个假单位", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    // **两栏各扫一遍。** 分栏之后只扫默认那一栏，另一栏等于没有守卫——
    // 而「模型说明里那个数」整段话正好在没被扫的那一栏里。
    await screen.findByDisplayValue("deepseek-v4-flash");
    expect(devTerms(screenText())).toEqual([]);

    openLength();
    await screen.findByDisplayValue("128000");
    const text = screenText();
    expect(devTerms(text)).toEqual([]);
    expect(text).not.toMatch(/token|context|窗口/i);
    // 「多少字」会是句假话（中文一个字不到一个单位），所以文案只说「照模型说明抄」。
    expect(text).toMatch(/模型说明/);
  });
});

describe("自动更新模型清单（那颗开关）", () => {
  // 这一栏 2026-08-14 从「一颗按钮 + 两段折叠说明」改成「标题 + 小字 + 右边一个开关」
  //（作者：「不用解释那么多」）。**默认关着**那条规矩没变，只是从「没有自动」变成
  // 「作者自己拨」——`settings.py` 那一位的注释写着这条为什么推翻得起。
  const settings = { match: /\/api\/settings$/, body: fixtures.settingsSaved };
  const saved = { method: "PUT" as const, match: /\/api\/settings$/, body: fixtures.settingsSaved };

  it("默认是关着的 —— **不自动跑**", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [settings]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    openLength();

    const knob = screen.getByRole("switch", { name: "自动更新模型清单" });
    expect(knob).toHaveAttribute("aria-checked", "false");
    // 屏幕上不许出现这份数据的来路（作者不需要认识 litellm / GitHub）。
    expect(document.body.textContent).not.toMatch(/litellm|github|json|快照/i);
  });

  it("拨开 = 只发它自己那一位，**而且当场更新一次**", async () => {
    // 只发一位：作者可能正把服务地址敲了一半，拨个开关不该把那半截提交上去。
    // 当场更新：「打开」的意思是「从现在起保持最新」，让他等到下次重开才生效，
    // 这一下看着就像什么都没发生。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      settings,
      saved,
      {
        method: "POST",
        match: /model-windows\/refresh$/,
        body: { fetched: "2026-11-01", total: 2206, added: 12, changed: 3, removed: 1, path: "" },
      },
    ]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    const spy = watchFetch();
    openLength();
    fireEvent.click(screen.getByRole("switch", { name: "自动更新模型清单" }));

    await waitFor(() => expect(savedBody(spy)).toEqual({ auto_update_model_windows: true }));
    await waitFor(() => expect(document.body.textContent).toContain("2206"));
    // **`changed` 必须摆出来**：一个模型的窗口被上游改小了，作者的上文会跟着变短，
    // 而那件事没有别的观测点。
    expect(document.body.textContent).toMatch(/变化 3/);
    expect(document.body.textContent).toMatch(/新增 12/);
  });

  it("拉不到时说人话，并且说清楚「原来那份还在用」", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      settings,
      saved,
      {
        method: "POST",
        match: /model-windows\/refresh$/,
        status: 502,
        body: { detail: "没能拉到那份公开的模型表（URLError）。原来那份还在用，什么都没改。网络好了再试一次。" },
      },
    ]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    openLength();
    fireEvent.click(screen.getByRole("switch", { name: "自动更新模型清单" }));

    await waitFor(() => expect(document.body.textContent).toMatch(/原来那份还在用/));
  });

  it("已经开着的时候，界面上说得出它开着", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      {
        match: /\/api\/settings$/,
        body: { ...fixtures.settingsSaved, auto_update_model_windows: true },
      },
    ]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    openLength();
    expect(screen.getByRole("switch", { name: "自动更新模型清单" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
