import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { saidToTheAuthor } from "../correctionError";
import {
  useAiSettings,
  useRefreshModelWindows,
  useSaveAiSettings,
} from "../api/hooks";
import type { AiSettings, AiSettingsInput } from "../api/types";
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

/** 上面那两样**买到了什么**：写着写着让 AI 接一段时，它读得到你前面多少字。
 *
 *  这个数是**后端算的**（`continuation_tail_limit`，按模型窗口伸缩），这儿一个公式
 *  都不许有——前端自己算一份，就是这一整条改动要修掉的那个 bug。
 *
 *  **「短」这件事必须说得出原因。** 认不出这个模型和还没配服务，算出来的数一模一样
 *  （都是最短那一档），而作者看到的症状只有「AI 好像没在看我前面写的」——不说原因，
 *  他会以为是模型不行，其实差的只是上面那个框里的一个数。
 *
 *  写成**类型上全列**的表而不是一串三元：后端哪天多一档，这儿不补 `tsc` 当场红。 */
function tailNote(settings: AiSettings): string {
  const many = settings.continuation_tail_limit.toLocaleString("zh-CN");
  return {
    model_window: `写着写着让它接一段时，它会先读一遍你光标前的约 ${many} 字。`,
    unknown_window:
      `还认不出这个模型一次能读多少，所以让它接一段时，它只读得到你光标前的 ` +
      `${many} 字。把上面那个数填上就会变长。`,
    unconfigured: `还没填服务地址和模型，让它接一段时，它只读得到你光标前的 ${many} 字。`,
  }[settings.continuation_tail_basis];
}

const TABS = [
  { key: "link", name: "连接服务" },
  { key: "length", name: "前文长度" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export function SettingsDrawer({ onClose }: { onClose: () => void }) {
  const settings = useAiSettings();
  const save = useSaveAiSettings();
  const refresh = useRefreshModelWindows();
  const [tab, setTab] = useState<TabKey>("link");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const [memory, setMemory] = useState("");

  useEffect(() => {
    if (settings.data) {
      setBaseUrl(settings.data.base_url);
      setModel(settings.data.model);
      setMemory(settings.data.context_window?.toString() ?? "");
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

  /** 应用「连接服务」那张卡。**只发这张卡上的三位**——见 `AiSettingsInput`。 */
  function applyLink() {
    const input: AiSettingsInput = { base_url: baseUrl.trim(), model: model.trim() };
    if (apiKey.trim()) input.api_key = apiKey.trim();
    save.mutate(input, {
      onSuccess: () => {
        setApiKey("");
        setReveal(false);
      },
    });
  }

  /** 应用「模型一次能读多少」。
   *
   *  **这一位每次都带上**：后端「没带这个键 = 保持原值」，不带的话作者清空那个框
   *  之后什么都不会发生，那个框就成了只进不出的洞。 */
  function applyMemory() {
    save.mutate({ context_window: memoryValue });
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

  return (
    <>
      <div className="backdrop" onClick={close} />
      {/* **没有标题栏。** 名字走 `aria-label`：屏幕上不再顶一行「AI 设置」
          （顶栏那颗齿轮已经说过一遍了），但读屏进来仍然念得出这扇窗叫什么。 */}
      <div className="set-modal" role="dialog" aria-modal="true" aria-label="AI 设置">
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
              {t.name}
            </button>
          ))}
        </div>

        <div className="set-right">
          {/* 名字是「关闭设置」不是「关闭」：这是全窗唯一一颗关窗按钮，
              说清楚它关的是什么，读屏才不会把它念成一个孤零零的「关闭」。 */}
          <button
            className="set-close"
            type="button"
            aria-label="关闭设置"
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
            {tab === "link" ? (
              /* 三个框是**一组**（作者要的）：它们回答的是同一个问题——连哪儿、
                 用哪个模型、拿什么钥匙。装进一张卡，「应用」钉在卡的右下角。 */
              <div className="set-card">
                <div className="set-field">
                  <label htmlFor="set-base-url">服务地址</label>
                  <input
                    id="set-base-url"
                    value={baseUrl}
                    placeholder="https://api.deepseek.com"
                    onChange={(e) => setBaseUrl(e.target.value)}
                  />
                </div>

                <div className="set-field">
                  <label htmlFor="set-model">模型</label>
                  <input
                    id="set-model"
                    value={model}
                    placeholder="deepseek-v4-flash"
                    onChange={(e) => setModel(e.target.value)}
                  />
                </div>

                <div className="set-field">
                  <label htmlFor="set-api-key">API 密钥</label>
                  <div className="set-key">
                    <input
                      id="set-api-key"
                      className={current?.api_key_set ? "on" : undefined}
                      type={reveal ? "text" : "password"}
                      value={apiKey}
                      placeholder={current?.api_key_set ? MASK : "粘贴密钥"}
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
                        aria-label={reveal ? "隐藏密钥" : "显示密钥"}
                        aria-pressed={reveal}
                        onClick={() => setReveal((on) => !on)}
                      >
                        <EyeIcon off={reveal} />
                      </button>
                    )}
                  </div>
                </div>

                {err && <div className="err-box">{saidToTheAuthor(err) ?? err.message}</div>}

                <div className="set-card-foot">
                  <button
                    type="button"
                    className="set-apply"
                    disabled={!linkDirty || save.isPending}
                    onClick={applyLink}
                  >
                    {save.isPending ? "应用中…" : "应用"}
                  </button>
                </div>
              </div>
            ) : (
              <>
                {/* ── 自动更新那份模型表 ────────────────────────────────────
                    这份表决定**上文给作者多长**（同一个模型，认得出是上万字，认不出是 800）。
                    它来自一个我们不控制的公开仓库，所以**默认关着**——自动更新等于别人
                    改一行、作者明天的稿子上下文就变了。拨开它的是作者本人，那一刻他知道
                    自己换了什么（`settings.py` 那一位的注释写着这条为什么推翻得起）。 */}
                <div className="set-card set-row-card">
                  <div className="set-row-text">
                    <span className="set-row-title" id="set-auto-label">
                      自动更新模型清单
                    </span>
                    <span className="set-row-sub">
                      每次打开工作台时更新一次，好让它认得新出的模型。
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
                  <div className="set-note">正在看有没有新的…</div>
                ) : refresh.data ? (
                  <div className="set-note">
                    已更新到 {refresh.data.fetched}：认得 {refresh.data.total} 个模型
                    {refresh.data.added || refresh.data.changed || refresh.data.removed
                      ? `（新增 ${refresh.data.added}、变化 ${refresh.data.changed}、减少 ${refresh.data.removed}）`
                      : "，和原来那份一样"}
                    。
                  </div>
                ) : null}

                {/* ── 手填那一格 ────────────────────────────────────────────
                    上面那份表**永远认不出自己搭的服务**（本机、公司内网、私有网关）——
                    那是有意的：照别人家云端的规格去猜自己机器上的模型，比不猜更坏。
                    于是那些作者只剩这一条路，而这个数决定他每次能带上多少前文（差 50 倍）。

                    文案里不许出现那个数的真实单位（研发术语），也不许替它安一个「字」的
                    单位——那是句假话（中文一个字不到一个单位）。所以只说「模型说明里的那个数」。 */}
                <div className="set-card">
                  <div className="set-row-text">
                    <span className="set-row-title">
                      <label htmlFor="set-memory">模型一次能读多少</label>
                    </span>
                    <span className="set-row-sub">
                      {"自己搭的、公司内网的服务，清单里认不出，才要填这一格。" +
                        "数照模型说明抄，常见 32768、128000。"}
                    </span>
                  </div>
                  <div className="set-field">
                    <input
                      id="set-memory"
                      inputMode="numeric"
                      value={memory}
                      placeholder="例如 32768"
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
                      {save.isPending ? "应用中…" : "应用"}
                    </button>
                  </div>
                </div>

                {/* 上面那两样**买到了什么**。 */}
                {current && <div className="set-note">{tailNote(current)}</div>}
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
