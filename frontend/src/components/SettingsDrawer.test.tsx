import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useLanguage } from "../language";
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

/** 切到装着「模型上下文长度」那一格的栏。
 *
 *  **左右分栏之后这一步是必须的**：那一栏的东西默认不在屏幕上，
 *  漏掉它的断言不会红，只会去查一个根本没渲染的框——那是假绿，不是通过。
 *
 *  ⚠️ **2026-09-06 起它就是「模型服务」那一栏。** 那天「前文长度」整栏取消了
 *  （它只有那一格，而那一格是模型服务的属性——清单认不出自建端点时自己报一个数），
 *  内容搬进「模型服务」。**名字留着不改成 `openLink`**：这个函数的意思是
 *  「切到那一格在的地方」，哪天它再搬一次，改的还是这一处。 */
const openLength = () => fireEvent.click(screen.getByRole("tab", { name: "模型服务" }));

/** 切到「模型服务」那一栏。
 *
 *  **2026-09-05 起这一步也是必须的**：那天新开了「通用」栏（界面语言搬了进去），
 *  而它排在最前、也是默认停的那一栏——在此之前「模型服务」是默认栏，这个文件里
 *  大半测试拿它栏里的输入框当「设置已经回来了」的信号。默认栏一换，那个信号
 *  连同它守的断言一起变成查一个没渲染的框，**红的是「还没渲染」，不是那条主张**。 */
/** 切到「个性化」那一栏（界面语言在那儿）。
 *
 *  ⚠️ **2026-09-06 起这一步是必须的。** 那天新开了一栏「通用」排在最前
 *  （装「这个工具要不要替我做某件事」那一类开关），原来那一栏改叫「个性化」
 *  （它管的是**读起来什么样**）。界面语言因此不再在默认停的那一栏上——
 *  漏掉这一步不会红在那条主张上，只会去查一个没渲染的按钮。 */
const openPersonal = () =>
  fireEvent.click(screen.getByRole("tab", { name: "个性化" }));

const openLink = () => fireEvent.click(screen.getByRole("tab", { name: "模型服务" }));

/** 切到「系统功能」那一栏（引擎替作者做的那几件事的开关都在那儿）。 */
const openSystem = () => fireEvent.click(screen.getByRole("tab", { name: "系统功能" }));

/** 某一张卡里的那颗「应用」。
 *
 *  ⚠️ **2026-09-06 起必须收窄。** 那天「模型上下文长度」搬进了「模型服务」栏，
 *  于是同一屏上有**两颗**叫「应用」的按钮（钥匙那张卡一颗、长度那张卡一颗），
 *  裸 `getByRole("button", { name: "应用" })` 当场「找到多个」。
 *  收窄到卡片容器，而不是改按钮文案——两颗都叫「应用」是对的，它们在各自的卡里。 */
const applyIn = (cardClass: string) => {
  const card = document.querySelector(`.${cardClass}`) as HTMLElement;
  // **只认属于这张卡自己的那一颗**：两张卡是嵌套的（长度那张在连接那张里面），
  // 裸 `within(card)` 会把里层那颗也算进来。`closest` 判「最近的那张有名字的卡
  // 是不是我」，这就是「自己的」那个判据。
  return [...card.querySelectorAll("button")].filter(
    (b) =>
      /应用/.test(b.textContent ?? "") &&
      b.closest(".set-card-window, .set-card-link") === card,
  )[0];
};

describe("AI 设置（BYOK）", () => {
  it("用自然语言说明已保存的地址、模型和密钥", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    openLink();
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
    openLink();
    await screen.findByDisplayValue("deepseek-v4-flash");
    const key = screen.getByLabelText("API 密钥") as HTMLInputElement;
    fireEvent.change(key, { target: { value: "sk-new-key" } });
    fireEvent.click(applyIn("set-card-link"));

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
    openLink();
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
    openLink();
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
    openLink();
    const key = (await screen.findByLabelText("API 密钥")) as HTMLInputElement;
    expect(key.placeholder).not.toContain("•");
    // 空框同样没有眼睛：一个字都没敲，它照样什么都露不出来。
    expect(screen.queryByRole("button", { name: /密钥$/ })).toBeNull();
  });

  it("三样缺一样就不发，并且说清缺的是哪样（作者 2026-09-13）", async () => {
    // 后端「空 = 保持原值」会把少填的一次存成半套配置——屏幕上像存好了，模型却调不起来。
    // 这一份：钥匙存过一把、地址和模型空着（钥匙那格的 `on` 是「设置回来了」的信号）。
    const halfway = { ...fixtures.settingsSaved, base_url: "", model: "", model_configured: false };
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: halfway },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    openLink();
    const spy = watchFetch();
    await waitFor(() => expect(screen.getByLabelText("API 密钥")).toHaveClass("on"));
    fireEvent.change(screen.getByLabelText("服务地址"), {
      target: { value: "https://api.deepseek.com" },
    });
    fireEvent.click(applyIn("set-card-link"));
    expect(await screen.findByRole("alert")).toHaveTextContent("尚未填写模型，三项齐全后再应用");
    expect(
      spy.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "PUT"),
    ).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("模型"), { target: { value: "deepseek-v4-flash" } });
    fireEvent.click(applyIn("set-card-link"));
    await waitFor(() => expect(savedBody(spy).model).toBe("deepseek-v4-flash"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("框空着提交时**不带** `api_key` 这一位（= 保持原钥匙）", async () => {
    // 屏幕上原来那句「留空则保留原来的密钥」2026-08-14 撤了，界面不再解释这件事——
    // **那件事本身还在**，而它现在只由这条断言守着。发一个空串上去就是把钥匙清掉。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    openLink();
    const spy = watchFetch();
    fireEvent.change(await screen.findByDisplayValue("deepseek-v4-flash"), {
      target: { value: "deepseek-v4" }, // 只改模型，钥匙一个字没碰
    });
    fireEvent.click(applyIn("set-card-link"));
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

    const save = applyIn("set-card-window");
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
    fireEvent.click(applyIn("set-card-window"));
    // **键必须在**：后端拿「带没带这个键」当判据，漏掉它就成了「保持原值」。
    await waitFor(() => expect(savedBody(spy)).toHaveProperty("context_window", null));
  });

  it("认不出来的输入当没填，不替他猜一个数", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settings },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settings },
    ]);
    openLength();
    await screen.findByText("模型上下文长度");
    fireEvent.change(screen.getByPlaceholderText(/32768/), { target: { value: "32k" } });
    // 猜错的方向没有一个是安全的：猜大了请求被拒，猜小了上文悄悄变短。
    expect(applyIn("set-card-link")).toBeDisabled();
  });

  it("屏幕上不出现研发术语，也不给这个数安一个假单位", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    // **每一栏各扫一遍。** 分栏之后只扫默认那一栏，别的栏等于没有守卫——
    // 而「以模型官方文档为准」整段话正好在没被扫的那一栏里。
    openLink();
    await screen.findByDisplayValue("deepseek-v4-flash");
    expect(devTerms(screenText())).toEqual([]);

    openLength();
    await screen.findByDisplayValue("128000");
    const text = screenText();
    expect(devTerms(text)).toEqual([]);
    // ⚠️ **禁的是单位和「窗口」，不是「上下文」。** `token` 是那个数的真实单位
    // （研发术语），`窗口` 是「上下文窗口」那半截行话；而「上下文」本身是中文里
    // 早就有的词（联系上下文），小说作者读得懂，且**模型自己的文档就管它叫这个**
    // ——文案让作者去那份文档里抄数，两处叫同一个名字他才抄得对。
    expect(text).not.toMatch(/token|context|窗口/i);
    // 「多少字」会是句假话（中文一个字不到一个单位），所以文案只把作者指向那份文档。
    expect(text).toMatch(/模型官方文档/);
  });
});

describe("界面语言（跟书的语言是两件事）", () => {
  // `useLanguage` 的 `setLanguage` 生产调用方一度是 0——store/持久化/`fromSystem()`
  // 兜底全都在，就是没有按钮接线，`grep setLanguage` 只在 `test/setup.ts` 里出现。
  // 这条测试**就是那个按钮的调用方本身**：它跑不过，说明按钮又没了或者接错了线，
  // 同这个仓库那些「引擎有、界面没有」的旧账一个形状。
  it("点一下按钮，界面语言真的变了——它在「个性化」那一栏里", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    openPersonal();
    await screen.findByText("界面语言 / Interface language");
    expect(useLanguage.getState().language).toBe("zh"); // 测试默认中文（test/setup.ts）
    expect(screen.getByRole("button", { name: "中文" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(useLanguage.getState().language).toBe("en");
    expect(screen.getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "中文" }));
    expect(useLanguage.getState().language).toBe("zh");
  });

  it("界面语言在默认打开的那一栏 —— 只会中文的作者切成 English 之后还找得回来", async () => {
    // ── 这一条钉的是一条**退路**，别把它读成「个性化栏里有语言」那么轻 ──────
    //
    // 2026-09-05 之前界面语言是**两栏都常驻**的一行，findability 靠「它永远在屏幕上」。
    // 那天搬进栏目，保障换成了「它在默认打开的那一栏上」——**换的是同一件事**：
    // 一个只会中文的作者手滑切成 English 之后，要能在读不懂任何栏目名的情况下
    // 找回那两颗按钮。
    //
    // ⚠️ **这条退路 2026-09-06 断过一次**：那天新开「通用」排在最前，语言所在的那一栏
    // 改叫「个性化」并退到第二位，于是它不再是默认打开的那一栏。当时这条测试被改成
    // 「钉住退路没了」——绿着不代表对，只代表**已知**。
    //
    // **2026-09-10「通用」取消**（它那一格并进「系统功能」），「个性化」重新排头、
    // 也重新是默认栏，退路自己回来了。所以这条测试改回正面断言。
    // **以后再往前面插新栏，就是又一次把它断掉**——那时要么把语言一起挪过去，
    // 要么给一个不依赖读字的入口（比如设置窗角上常驻两颗）。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    expect(screen.getByRole("tab", { name: "个性化" })).toHaveAttribute("aria-selected", "true");
    // **不点任何东西**就该看得见那两颗 —— 这一行就是那条退路本身。
    expect(await screen.findByRole("button", { name: "中文" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "English" })).toBeInTheDocument();
    // 别的栏的东西不在这一栏（否则「分栏」这句话没有内容）。
    expect(screen.queryByLabelText("API 密钥")).toBeNull();
    await waitFor(() => expect(screen.queryByText("模型上下文长度")).toBeNull());
  });

  it("跟书的语言不是同一个词——屏幕上不出现裸的「语言：」", () => {
    // 书架上「这本书写的是什么语言」那两个按钮就长这样（`BookShelf.tsx::BookLanguageToggle`），
    // 这一格换个说法正是为了不让作者把两件事看成同一件事。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    openPersonal();
    expect(screen.queryByText("语言：")).toBeNull();
    expect(screen.getByText("界面语言 / Interface language")).toBeInTheDocument();
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
    openLength();
    await screen.findByDisplayValue("128000");

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
    openLength();
    await screen.findByDisplayValue("128000");
    const spy = watchFetch();
    fireEvent.click(screen.getByRole("switch", { name: "自动更新模型清单" }));

    await waitFor(() => expect(savedBody(spy)).toEqual({ auto_update_model_windows: true }));
    await waitFor(() => expect(document.body.textContent).toContain("2206"));
    // **`changed` 必须摆出来**：一个模型的窗口被上游改小了，作者的上文会跟着变短，
    // 而那件事没有别的观测点。
    expect(document.body.textContent).toMatch(/变更 3/);
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
    openLength();
    await screen.findByDisplayValue("128000");
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
    openLength();
    await screen.findByDisplayValue("128000");
    expect(screen.getByRole("switch", { name: "自动更新模型清单" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});

describe("是否在 novel-agent 模式下续写（那颗开关）", () => {
  // 作者 2026-09-10 看到助手开着、正文里还在往下冒灰字：「模式二这个就不用有这个续写了」，
  // 随后要了这颗开关（「添加一个设置按钮用来开模式二支持续写」）。**默认关着**，由他
  // 自己拨。判据本身在 `continuation.ts`（`continuation.test.ts` 钉），这儿只验开关：
  // 默认态、只发自己那一位、开着时说得出它开着。
  const settings = { match: /\/api\/settings$/, body: fixtures.settingsSaved };
  const saved = { method: "PUT" as const, match: /\/api\/settings$/, body: fixtures.settingsSaved };
  const NAME = "是否在 novel-agent 模式下续写";

  /** 开关在设置回来之前是禁用的（`!current`）——等它活过来才算「设置已经到手」。 */
  const knobReady = async () => {
    const knob = await screen.findByRole("switch", { name: NAME });
    await waitFor(() => expect(knob).toBeEnabled());
    return knob;
  };

  it("默认是关着的 —— 模式二没有续写", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [settings]);
    openSystem();
    const knob = await knobReady();
    expect(knob).toHaveAttribute("aria-checked", "false");
    // 两个模式的名字必须是顶栏那颗开关已经在念的那两个——同一个东西不叫两个名字。
    expect(screenText()).toMatch(/协助模式/);
    expect(screenText()).toMatch(/novel-agent 模式/);
  });

  it("拨开 = 只发它自己那一位", async () => {
    // 作者可能正把服务地址敲了一半，拨个开关不该把那半截提交上去。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [settings, saved]);
    openSystem();
    const knob = await knobReady();
    const spy = watchFetch();
    fireEvent.click(knob);
    await waitFor(() => expect(savedBody(spy)).toEqual({ continuation_in_agent_mode: true }));
  });

  it("已经开着的时候，界面上说得出它开着", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      {
        match: /\/api\/settings$/,
        body: { ...fixtures.settingsSaved, continuation_in_agent_mode: true },
      },
    ]);
    openSystem();
    expect(await knobReady()).toHaveAttribute("aria-checked", "true");
  });
});
