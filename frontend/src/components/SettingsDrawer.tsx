import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { saidToTheAuthor } from "../correctionError";
import {
  useAiSettings,
  useRefreshModelWindows,
  useSaveAiSettings,
} from "../api/hooks";
import type { AiSettingsInput } from "../api/types";
import { useLanguage } from "../language";
import { CloseIcon, EyeIcon } from "./icons";

// AI 设置（BYOK）——像 Cursor 的 API Keys：作者粘一把自己的钥匙，存在本机。
// 钥匙永不回显：后端只给「设没设 + 后四位」；留空提交 = 保持原钥匙。
//
// **2026-08-14：它是左右分栏的弹窗了**（样式 `.set-*`）。文件名和组件名还叫 Drawer，
// 那是句过时的话，但改名要动三处引用和两个测试文件，留给下一次。
// **它现在没有 `.drawer` 那个类了，别照着别的抽屉改这一个。**
//
// **也没有脚条了**（作者：「保存上边不需要整一条线隔离两个按钮」）。
// 「应用」跟着它管的那张卡片走，钉在卡片右下角；关窗只剩右上角那个 × 和 Esc。
// 一颗按钮**离它改的东西越近，越说得清它会改什么**——横跨整扇窗的脚条说不清
// 它是在存左边这一栏还是右边那一栏。

/** 输入框里的字 → 要发给后端的那个数。**认不出来一律当「没填」。**
 *
 *  故意不做任何「聪明」的解析（`32k`、`三万`、带逗号的 `128,000` 一律不认）：
 *  这个数猜错了的症状是上文悄悄变短或者请求被拒，而两种都追不回「他其实想填什么」。
 *  认不出就发 `null`，界面上他看得见自己填的还在框里。 */
function asPositiveInteger(text: string): number | null {
  const digits = text.trim();
  if (!/^\d+$/.test(digits)) return null;
  const value = Number(digits);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

/** 框里那一排点。**只说「这儿有一把钥匙」，不说是哪一把。**
 *
 *  后端给的 `api_key_preview`（`***********4555`）这儿一个字都不用——作者的原话。
 *  个数写死 12：星号那串是后端凑的位数、不是钥匙的真长度，照抄它等于把一个
 *  假的长度画到屏幕上。
 *
 *  **它是 placeholder，不是一个能被「露出来」的值**——这条区别是下面那只眼睛
 *  为什么只在作者敲了字之后才出现的全部原因，见 `reveal` 那一段。 */
const MASK = "•".repeat(12);

/** 左栏的三个栏目。
 *
 *  **「通用」排头，也是打开设置时默认停的那一栏**（作者 2026-09-05：「加一个通用的
 *  页面把这个语言的设置移过去」）。这条顺序不是审美：界面语言在它里面，而那一格
 *  必须在**已经切到看不懂的那种语言之后**还找得回来。搬进栏目之前它靠「两栏都常驻」
 *  换这一点，搬进来之后换成「一打开就在眼前」——两者换的是同一件事，别把默认栏
 *  改成别的，那等于把回退的路堵死。
 *
 *  **栏目名一律是名词**：「连接服务」原来是个动宾短语（读起来像一个动作），
 *  而左栏是「你在哪一栏」，不是「你要做什么」。 */
const TABS = [
  // ── 2026-09-10：「通用」这一栏**取消**，它那一格并进「系统功能」（作者点名）───
  //   走的是 2026-09-06 给「前文长度」判的同一条：一栏只剩一格、而那一格在别处有更
  //   合适的归属时，这一栏就该消失。「是否开启核对模型」是**引擎替你做的一件事**，
  //   跟「关系图画多少人」同属「引擎怎么跑」，不必自己占一栏。
  //
  //   ⚠️ **默认栏跟着挪到「个性化」**（原来是「通用」）。这不是随手补的：
  //   界面语言在「个性化」里，而那一格必须在**作者已经切到看不懂的那种语言之后**
  //   还找得回来——2026-09-05 把它搬进栏目时，换来的保障就是「它是默认打开的第一栏」。
  //   「通用」没了之后，那条保障只有让「个性化」排头+默认才继续成立。
  { key: "personal", name: { zh: "个性化", en: "Personalization" } },
  { key: "system", name: { zh: "系统功能", en: "System" } },
  { key: "link", name: { zh: "模型服务", en: "Connection" } },
] as const;

type TabKey = (typeof TABS)[number]["key"];

/** `initialTab`：从哪一栏打开。齿轮开的是「个性化」（默认、排头，见 `TABS` 的注释）；
 *  每一格「先连接模型」的空态和顶栏那盏灰灯开的是「模型服务」——作者正要填的就是那三样，
 *  让他自己再找一次栏目等于把指路的话说了一半。 */
export function SettingsDrawer({
  onClose,
  initialTab = "personal",
}: {
  onClose: () => void;
  initialTab?: TabKey;
}) {
  const settings = useAiSettings();
  const save = useSaveAiSettings();
  const refresh = useRefreshModelWindows();
  const language = useLanguage((s) => s.language);
  const setLanguage = useLanguage((s) => s.setLanguage);
  const [tab, setTab] = useState<TabKey>(initialTab);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const [memory, setMemory] = useState("");
  const [graphCap, setGraphCap] = useState("");

  useEffect(() => {
    if (settings.data) {
      setBaseUrl(settings.data.base_url);
      setModel(settings.data.model);
      setMemory(settings.data.context_window?.toString() ?? "");
      setGraphCap(settings.data.graph_max_nodes?.toString() ?? "");
    }
  }, [settings.data]);

  const err = save.error instanceof ApiError ? save.error : null;
  const refreshErr = refresh.error instanceof ApiError ? refresh.error : null;
  const current = settings.data;
  const memoryValue = asPositiveInteger(memory);
  const linkDirty =
    baseUrl.trim() !== (current?.base_url ?? "") ||
    model.trim() !== (current?.model ?? "") ||
    apiKey.trim() !== "";
  const memoryDirty = memoryValue !== (current?.context_window ?? null);
  const graphCapValue = asPositiveInteger(graphCap);
  const graphCapDirty = graphCapValue !== (current?.graph_max_nodes ?? null);

  /** 保存正在飞的时候关不掉（背景、×、Esc 三条路一起挡）——
   *  这一刻关窗，作者不知道那把钥匙到底存进去没有。同 `Setup.tsx` 的 `closeDrawer`。 */
  const close = useCallback(() => {
    if (save.isPending) return;
    onClose();
  }, [onClose, save.isPending]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [close]);

  /** 三样里缺了哪几样（服务地址 / 模型 / 钥匙）。钥匙已经存过一把、框里空着不算缺。 */
  const linkMissing = [
    !baseUrl.trim() && (language === "zh" ? "服务地址" : "the endpoint"),
    !model.trim() && (language === "zh" ? "模型" : "the model"),
    !apiKey.trim() && !current?.api_key_set && (language === "zh" ? "API 密钥" : "the API key"),
  ].filter((x): x is string => Boolean(x));
  const [linkRefused, setLinkRefused] = useState<string | null>(null);

  /** 应用「连接服务」那张卡。**只发这张卡上的三位**——见 `AiSettingsInput`。
   *
   *  **缺一样就不发，说清缺哪样**（作者 2026-09-13：「用户要是点击应用少一个就应该提醒
   *  用户」）：后端「空 = 保持原值」的合并规则会把一次少填的提交存成半套配置，屏幕上
   *  看着像存好了，模型却调不起来。 */
  function applyLink() {
    if (linkMissing.length > 0) {
      setLinkRefused(
        language === "zh"
          ? `尚未填写${linkMissing.join("、")}，三项齐全后再应用`
          : `Fill in ${linkMissing.join(", ")} before applying; all three are required`,
      );
      return;
    }
    setLinkRefused(null);
    const input: AiSettingsInput = { base_url: baseUrl.trim(), model: model.trim() };
    if (apiKey.trim()) input.api_key = apiKey.trim();
    save.mutate(input, {
      onSuccess: () => {
        setApiKey("");
        setReveal(false);
      },
    });
  }

  /** 应用「模型上下文长度」。
   *
   *  **这一位每次都带上**：后端「没带这个键 = 保持原值」，不带的话作者清空那个框
   *  之后什么都不会发生，那个框就成了只进不出的洞。 */
  function applyMemory() {
    save.mutate({ context_window: memoryValue });
  }

  /** 应用「关系图最多画几个人」。**同上：这一位每次都带上**——后端「没带这个键 =
   *  保持原值」，不带的话作者清空那个框之后什么都不会发生。 */
  function applyGraphCap() {
    save.mutate({ graph_max_nodes: graphCapValue });
  }

  /** 拨那颗开关。**只发它自己那一位**：作者刚敲了一半的服务地址不该被顺手提交上去。
   *
   *  拨开的同时**立刻更新一次**——「打开」这个动作的意思就是「从现在起保持最新」，
   *  让他等到下次重开工作台才生效，这一下看着就像什么都没发生。 */
  function toggleAuto(next: boolean) {
    save.mutate(
      { auto_update_model_windows: next },
      { onSuccess: () => next && refresh.mutate() },
    );
  }

  /** 拨「改完人物卡叫核对模型验一遍」那颗。**同上：只发它自己那一位。** */
  function toggleReviewCardEdits(next: boolean) {
    save.mutate({ review_card_edits: next });
  }

  /** 拨「novel-agent 模式下也续写」那颗。**同上：只发它自己那一位。** */
  function toggleContinuationInAgentMode(next: boolean) {
    save.mutate({ continuation_in_agent_mode: next });
  }

  return (
    <>
      <div className="backdrop" onClick={close} />
      {/* **没有标题栏。** 名字走 `aria-label`：屏幕上不再顶一行「AI 设置」
          （顶栏那颗齿轮已经说过一遍了），但读屏进来仍然念得出这扇窗叫什么。 */}
      <div
        className="set-modal"
        role="dialog"
        aria-modal="true"
        aria-label={language === "zh" ? "AI 设置" : "AI Settings"}
      >
        {/* 左边这一列是**栏目名**。它们原来是右边每一组头顶的小标题——
            同一句话搬到导航里说一次，右边就只剩控件本身了。 */}
        <div className="set-rail" role="tablist" aria-orientation="vertical">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={`set-tab-${t.key}`}
              aria-selected={tab === t.key}
              aria-controls={`set-pane-${t.key}`}
              className={tab === t.key ? "set-rail-item on" : "set-rail-item"}
              onClick={() => setTab(t.key)}
            >
              {t.name[language]}
            </button>
          ))}
        </div>

        <div className="set-right">
          {/* 名字是「关闭设置」不是「关闭」：这是全窗唯一一颗关窗按钮，
              说清楚它关的是什么，读屏才不会把它念成一个孤零零的「关闭」。 */}
          <button
            className="set-close"
            type="button"
            aria-label={language === "zh" ? "关闭设置" : "Close settings"}
            disabled={save.isPending}
            onClick={close}
          >
            <CloseIcon />
          </button>

          <div
            className="set-pane"
            role="tabpanel"
            id={`set-pane-${tab}`}
            aria-labelledby={`set-tab-${tab}`}
          >
            {tab === "system" ? (
              <div className="set-stack">
                {/* ── 是否开启核对模型 ──────────────────────────────────────
                    **默认关着，理由不是省钱那么简单**：另外两问核对的是模型写的正文，
                    这一问核对的是**作者填的字**——判错的时候是在说「你写的东西不对」，
                    那比质疑机器刺人得多。所以要他自己拨开
                    （`settings.review_card_edits` 那一位的注释写着完整论证）。

                    ⚠️ **这段说明里不提「通知」**（作者 2026-09-06：「不用再写什么会
                    展示在通知，他开启后，然后检验到他知道」）——开关的说明只说
                    **它会做什么判断**，「结果摆在哪儿」是他开起来自然会看见的事，
                    写进来只是把一句他不需要预先记住的话摆在开关旁边。 */}
                <div className="set-card set-row-card">
                  <div className="set-row-text">
                    <span className="set-row-title" id="set-review-card-label">
                      {language === "zh" ? "是否开启核对模型" : "Enable the checking model"}
                    </span>
                    <span className="set-row-sub">
                      {language === "zh" ? (
                        <>
                          修改角色卡上的信息之后，核对模型会去判断它和当前原文是否冲突，
                          以及和前后章的状态、相关原文是否冲突。每改一格花一次模型调用，
                          改动本身不受影响。
                        </>
                      ) : (
                        <>
                          After you edit information on a character card, the checking model
                          judges whether it conflicts with the chapter’s own text, and with the
                          states and related text in earlier and later chapters. Costs one model
                          call per edit; the edit itself always goes through.
                        </>
                      )}
                    </span>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    className="set-switch"
                    aria-labelledby="set-review-card-label"
                    aria-checked={!!current?.review_card_edits}
                    disabled={!current || save.isPending}
                    onClick={() => toggleReviewCardEdits(!current?.review_card_edits)}
                  >
                    <span className="set-switch-knob" />
                  </button>
                </div>
                {/* ── 是否在 novel-agent 模式下续写 ─────────────────────────
                    **默认关着**：作者 2026-09-10 看到助手开着、正文里还在往下冒灰字，
                    裁定「模式二这个就不用有这个续写了」，随后要了这颗开关
                    （「添加一个设置按钮用来开模式二支持续写」）。判据在
                    `continuation.ts::shouldSuggest`，这儿只是那一位的开关。

                    标题照上一格的句式（「是否…」），两个模式的名字用顶栏那颗开关
                    已经在念的那两个（「协助模式」「novel-agent 模式」）——同一个东西
                    在两处不叫两个名字。说明只说它做什么、花什么，不说灰字怎么采纳。 */}
                <div className="set-card set-row-card">
                  <div className="set-row-text">
                    <span className="set-row-title" id="set-continuation-agent-label">
                      {language === "zh"
                        ? "是否在 novel-agent 模式下续写"
                        : "Enable continuation in novel-agent mode"}
                    </span>
                    <span className="set-row-sub">
                      {language === "zh" ? (
                        <>
                          停笔片刻后，光标处会出现一段续写建议。默认仅在协助模式下提供；
                          开启后，novel-agent 模式下同样提供。每次建议花一次模型调用。
                        </>
                      ) : (
                        <>
                          After a short pause in typing, a continuation suggestion appears at
                          the cursor. By default it is offered only in assist mode; when
                          enabled, it is offered in novel-agent mode as well. Each suggestion
                          costs one model call.
                        </>
                      )}
                    </span>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    className="set-switch"
                    aria-labelledby="set-continuation-agent-label"
                    aria-checked={!!current?.continuation_in_agent_mode}
                    disabled={!current || save.isPending}
                    onClick={() =>
                      toggleContinuationInAgentMode(!current?.continuation_in_agent_mode)
                    }
                  >
                    <span className="set-switch-knob" />
                  </button>
                </div>
                {/* 关系图一次画多少人。**这个数原来写死成 30**，而真书上主角有几百条
                    关系边：图上只画得出 30 个，剩下的连提都没提——作者看到的是一张
                    沉默地删过节的图（2026-09-09 他指着「部分内容已折叠」问出来的）。
                    引擎的默认提到了 1000，这一格是给「嫌乱调小 / 想全看调大」留的。

                    **默认值由后端回过来填进占位符**（`graph_max_nodes_default`），
                    前端不许自己抄一个数：抄了引擎哪天改，这儿会安静地说一个旧数。

                    ── 文案 2026-09-10 重写过一次，理由记在这儿 ─────────────────
                    头一版写的是「人物卡里那张关系图一次最多画这么多人，超出的不画。
                    留空按默认。调大能看得更全，但人越多图越密、铺开也越慢」——作者一句
                    「太口语了」否掉。**病因正是 CLAUDE.md 那一节点名的那种**：拿大白话
                    去描述机制（「这么多人」「超出的不画」「越…越…」），而不是先挑词。
                    改法是照同一栏里「模型上下文长度」那条的语域走（「仅当…时需要填写」
                    「数值以…为准」）——**同一屏上的说明必须是同一个语域**，混着写比
                    单条不好读更糟。

                    仍然不出现「节点」这个词——那是引擎的说法。屏幕上说「人物与设定」，
                    那是这个产品自己已经在用的词（角色册空态：「还没有人物或设定」），
                    不是现造的近义词。 */}
                <div className="set-card set-card-window">
                  <div className="set-row-text">
                    <span className="set-row-title">
                      <label htmlFor="set-graph-cap">
                        {language === "zh" ? "关系图人数上限" : "Relationship graph limit"}
                      </label>
                    </span>
                    <span className="set-row-sub">
                      {language === "zh"
                        ? "单张人物卡的关系图最多绘制的人物与设定数量，超出部分不予显示。留空则采用默认值。数值越大覆盖越完整，绘制耗时与图形密度也随之上升。"
                        : "The maximum number of characters and settings drawn in one card's relationship graph; anything beyond that is omitted. Leave blank to use the default. A higher value gives fuller coverage, at the cost of density and drawing time."}
                    </span>
                  </div>
                  <div className="set-field">
                    <input
                      id="set-graph-cap"
                      inputMode="numeric"
                      value={graphCap}
                      placeholder={
                        current?.graph_max_nodes_default
                          ? (language === "zh" ? "默认 " : "Default ") +
                            current.graph_max_nodes_default
                          : undefined
                      }
                      onChange={(e) => setGraphCap(e.target.value)}
                    />
                  </div>

                  {err && <div className="err-box">{saidToTheAuthor(err) ?? err.message}</div>}

                  <div className="set-card-foot">
                    <button
                      type="button"
                      className="set-apply"
                      disabled={!graphCapDirty || save.isPending}
                      onClick={applyGraphCap}
                    >
                      {language === "zh"
                        ? save.isPending ? "应用中…" : "应用"
                        : save.isPending ? "Applying…" : "Apply"}
                    </button>
                  </div>
                </div>
              </div>
            ) : tab === "personal" ? (
              /* 界面语言。**2026-09-05 从「两栏都常驻」搬进「通用」这一栏**，
                 findability 换成了「它是默认打开的第一栏」——理由见 `TABS` 上面那段。

                 标题仍然写成中英对照，不跟着 `language` 变：**它自己就是找它的入口**，
                 当前是哪种界面语言都得认得出来，不能因为已经切到看不懂的那种就找不到开关。

                 **不能叫「语言」**：书架上「这本书写的是什么语言」那两个按钮已经占了这个词
                 （维护者 2026-08-27 裁定书的语言跟界面语言分开管，一个中文作者能写英文小说），
                 摆同一屏还叫同一个名字，认错的代价是把小说正文的语言给改了。 */
              <div className="set-card set-row-card">
                <div className="set-row-text">
                  <span className="set-row-title" id="set-lang-label">
                    界面语言 / Interface language
                  </span>
                  <span className="set-row-sub">
                    {language === "zh" ? (
                      <>仅影响界面文字。本书的写作语言是另一项设置，在书架中修改。</>
                    ) : (
                      <>
                        Affects interface text only. The language this book is written in is a
                        separate setting, changed on the shelf.
                      </>
                    )}
                  </span>
                </div>
                <div className="set-row" role="group" aria-labelledby="set-lang-label">
                  <button
                    type="button"
                    className={language === "zh" ? "on" : ""}
                    aria-pressed={language === "zh"}
                    onClick={() => setLanguage("zh")}
                  >
                    中文
                  </button>
                  <button
                    type="button"
                    className={language === "en" ? "on" : ""}
                    aria-pressed={language === "en"}
                    onClick={() => setLanguage("en")}
                  >
                    English
                  </button>
                </div>
              </div>
            ) : (
              /* ── 这一栏是**一摞并列的块**，不是一张大卡里再套小卡 ────────────
                 三个连接框仍是**一组**（作者要的）：它们回答的是同一个问题——连哪儿、
                 用哪个模型、拿什么钥匙，所以合在一张卡里、「应用」钉在卡的右下角。

                 但**下面那两样不属于这张卡**：自动更新清单、模型上下文长度，各是各的事。
                 它们原来被套在这张卡**里面**，屏幕上就是「卡中卡」——外面一圈框从
                 API 密钥一直包到最底下（作者 2026-09-10 指着说「这底下两个各自单独一个块」）。
                 外层换成 `.set-stack`（这一栏本来就该是个容器，不该自己也是一张卡），
                 三者就并列了；卡与卡的间距由 `.set-stack` 给，不写在卡身上。 */
              <div className="set-stack">
                <div className="set-card set-card-link">
                <div className="set-field">
                  <label htmlFor="set-base-url">
                    {language === "zh" ? "服务地址" : "Endpoint"}
                  </label>
                  <input
                    id="set-base-url"
                    value={baseUrl}
                    placeholder="https://api.deepseek.com"
                    onChange={(e) => setBaseUrl(e.target.value)}
                  />
                </div>

                <div className="set-field">
                  <label htmlFor="set-model">{language === "zh" ? "模型" : "Model"}</label>
                  <input
                    id="set-model"
                    value={model}
                    placeholder="deepseek-v4-flash"
                    onChange={(e) => setModel(e.target.value)}
                  />
                </div>

                <div className="set-field">
                  <label htmlFor="set-api-key">
                    {language === "zh" ? "API 密钥" : "API key"}
                  </label>
                  <div className="set-key">
                    <input
                      id="set-api-key"
                      className={current?.api_key_set ? "on" : undefined}
                      type={reveal ? "text" : "password"}
                      value={apiKey}
                      placeholder={
                        current?.api_key_set
                          ? MASK
                          : language === "zh" ? "粘贴密钥" : "Paste your key"
                      }
                      onChange={(e) => {
                        setApiKey(e.target.value);
                        // 框空回去 = 又没东西可露了，明文那一档跟着收掉：
                        // 留着它，下次粘进来的钥匙会**直接以明文出现**（他没按过任何键）。
                        if (!e.target.value) setReveal(false);
                      }}
                    />
                    {/* **框里有字才有这只眼睛。**

                        它只能露「作者刚敲进去的这一把」：已存的那把后端从不回吐（铁律，
                        `tests/test_settings.py` 钉着），框里那排点是 placeholder 不是值。
                        所以在「已经设过、还没动手改」那一档，它按下去屏幕上不会有任何变化——
                        **一颗点了不动的按钮就是一句假话**（作者：「能点也没有用……说明默认
                        就是黑点你这个是错误的设计」）。上一版把它做成灰的（点不动）也被否了，
                        对的做法是：**没得露的时候它根本不在**，在的时候它一定管用。 */}
                    {apiKey.length > 0 && (
                      <button
                        className="set-eye"
                        type="button"
                        aria-label={
                          language === "zh"
                            ? reveal ? "隐藏密钥" : "显示密钥"
                            : reveal ? "Hide key" : "Show key"
                        }
                        aria-pressed={reveal}
                        onClick={() => setReveal((on) => !on)}
                      >
                        <EyeIcon off={reveal} />
                      </button>
                    )}
                  </div>
                </div>

                {err && <div className="err-box">{saidToTheAuthor(err) ?? err.message}</div>}
                {linkRefused && <div className="err-box" role="alert">{linkRefused}</div>}

                <div className="set-card-foot">
                  <button
                    type="button"
                    className="set-apply"
                    disabled={!linkDirty || save.isPending}
                    onClick={applyLink}
                  >
                    {language === "zh"
                      ? save.isPending ? "应用中…" : "应用"
                      : save.isPending ? "Applying…" : "Apply"}
                  </button>
                </div>
                </div>

                {/* ── 下面这些 2026-09-06 从「前文长度」那一栏搬过来（作者点名）───
                    它们本来就是**模型服务的属性**：那份公开清单决定这个模型认不认得出，
                    手填那一格是「清单认不出自建端点时自己报一个数」。放在另一栏里，
                    作者得先想到「上文长度归模型管」才找得到它。
                    搬完「前文长度」那一栏就空了，所以那一栏一起取消了。 */}
                {/* ── 自动更新那份模型表 ────────────────────────────────────
                    这份表决定**上文给作者多长**（同一个模型，认得出是上万字，认不出是 800）。
                    它来自一个我们不控制的公开仓库，所以**默认关着**——自动更新等于别人
                    改一行、作者明天的稿子上下文就变了。拨开它的是作者本人，那一刻他知道
                    自己换了什么（`settings.py` 那一位的注释写着这条为什么推翻得起）。 */}
                <div className="set-card set-row-card">
                  <div className="set-row-text">
                    <span className="set-row-title" id="set-auto-label">
                      {language === "zh" ? "自动更新模型清单" : "Auto-update the model list"}
                    </span>
                    <span className="set-row-sub">
                      {language === "zh" ? (
                        <>每次启动工作台时检查一次更新，以收录新发布的模型。</>
                      ) : (
                        <>
                          Checks for updates each time the workbench starts, so newly released
                          models are covered.
                        </>
                      )}
                    </span>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    className="set-switch"
                    aria-labelledby="set-auto-label"
                    aria-checked={!!current?.auto_update_model_windows}
                    disabled={!current || save.isPending}
                    onClick={() => toggleAuto(!current?.auto_update_model_windows)}
                  >
                    <span className="set-switch-knob" />
                  </button>
                </div>

                {/* 更新的结果**要说出来**。没有它，那颗开关拨完什么都不响，
                    作者只能猜有没有生效——而这个仓库正在还的债有一半是那种形态。 */}
                {refreshErr ? (
                  <div className="err-box">{saidToTheAuthor(refreshErr) ?? refreshErr.message}</div>
                ) : refresh.isPending ? (
                  <div className="set-note">
                    {language === "zh" ? "正在检查更新…" : "Checking for updates…"}
                  </div>
                ) : refresh.data ? (
                  <div className="set-note">
                    {language === "zh" ? (
                      <>
                        已更新至 {refresh.data.fetched}：收录 {refresh.data.total} 个模型
                        {refresh.data.added || refresh.data.changed || refresh.data.removed
                          ? `（新增 ${refresh.data.added}、变更 ${refresh.data.changed}、移除 ${refresh.data.removed}）`
                          : "，与此前一致"}
                        。
                      </>
                    ) : (
                      <>
                        Updated to {refresh.data.fetched}: {refresh.data.total} model
                        {refresh.data.total === 1 ? "" : "s"} covered
                        {refresh.data.added || refresh.data.changed || refresh.data.removed
                          ? ` (${refresh.data.added} added, ${refresh.data.changed} changed, ${refresh.data.removed} removed)`
                          : ", unchanged"}
                        .
                      </>
                    )}
                  </div>
                ) : null}

                {/* ── 手填那一格 ────────────────────────────────────────────
                    上面那份表**永远认不出自己搭的服务**（本机、公司内网、私有网关）——
                    那是有意的：照别人家云端的规格去猜自己机器上的模型，比不猜更坏。
                    于是那些作者只剩这一条路，而这个数决定他每次能带上多少前文（差 50 倍）。

                    文案里不许出现那个数的真实单位（研发术语），也不许替它安一个「字」的
                    单位——那是句假话（中文一个字不到一个单位）。所以只说「模型说明里的那个数」。 */}
                <div className="set-card set-card-window">
                  <div className="set-row-text">
                    <span className="set-row-title">
                      <label htmlFor="set-memory">
                        {language === "zh" ? "模型上下文长度" : "Model context length"}
                      </label>
                    </span>
                    <span className="set-row-sub">
                      {language === "zh"
                        ? "仅当服务为自建或部署于内网、未被上方清单收录时需要填写。数值以模型官方文档为准，常见 32768、128000。"
                        : "Required only when the endpoint is self-hosted or on an internal network and isn't covered by the list above. Use the value from the model's official documentation — 32768 and 128000 are common."}
                    </span>
                  </div>
                  <div className="set-field">
                    <input
                      id="set-memory"
                      inputMode="numeric"
                      value={memory}
                      placeholder={language === "zh" ? "例如 32768" : "e.g. 32768"}
                      onChange={(e) => setMemory(e.target.value)}
                    />
                  </div>

                  {err && <div className="err-box">{saidToTheAuthor(err) ?? err.message}</div>}

                  <div className="set-card-foot">
                    <button
                      type="button"
                      className="set-apply"
                      disabled={!memoryDirty || save.isPending}
                      onClick={applyMemory}
                    >
                      {language === "zh"
                        ? save.isPending ? "应用中…" : "应用"
                        : save.isPending ? "Applying…" : "Apply"}
                    </button>
                  </div>
                </div>

              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
