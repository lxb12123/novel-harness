"""AI 设置（BYOK）：base_url / model / api_key 的本机存储（2026-08-02）。

2026-08-13 多了一位 `context_window`：自建端点的上下文窗口**推断不出来**，
只能作者自己填（见那个字段的注释）。它和钥匙一样是「这台机器上的连接参数」，
所以住同一个文件，不进 `book.db`。

作者在设置页粘一把自己的钥匙（像 Cursor 的 API Keys），存在**作者自己的机器**上。
钥匙是个人凭证，不跟 `book.db` 走（ADR 0012 管的是书，这里管的是人）——所以存
用户级配置目录，不是书文件夹。

存储：`~/.config/novel-harness/settings.json`，写盘后 chmod 0600（只有本人可读）。
GET 侧**永不回吐完整 key**，只回 `api_key_set` + 后四位预览（见 api/app.py）。
M2 PASS 后 `/draft` 等 501 stub 换成真实现时，从这里读连接参数。

测试隔离：环境变量 `NH_SETTINGS_PATH` 指到临时文件（同 `NH_LLM_*` 的注入风格）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_PATH = Path.home() / ".config" / "novel-harness" / "settings.json"
"""作者机器上的默认位置。**不是书的数据，跟书走的是 `book.db`（ADR 0012）。**"""


def _path() -> Path:
    override = os.environ.get("NH_SETTINGS_PATH")
    return Path(override) if override else DEFAULT_PATH


class Settings(BaseModel):
    """连接参数。key 不进 repr / 不随 GET 出参（遮蔽在 API 层做）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = ""
    model: str = ""
    api_key: str = Field(default="")

    context_window: int | None = None
    """作者手填的上下文窗口（token）。**`None` = 没填，那时一切照旧（自动推断）。**

    为什么要留这一位：这个数决定逐字上文给他 800 字还是 40,000 字
    （`draft/assemble.py::product_tail_limit`），而**自建端点永远推断不出来**——
    `draft/windows.py` 边界二写死了「主机名不认识就一个键都不查」，
    照云端标价/规格猜自建模型比不猜更坏。localhost、公司内网、私有网关一律落到 unknown，
    于是那些作者**只有自己填这一条路**（Cline / Roo Code 也是让用户自己填）。

    它压过所有自动推断（注册表 / 端点自报 / 公共快照），走的是 `resolve_capabilities`
    本来就有的 `operator_override` 那一档——**这条路径早就在，不是为它新开的**。

    它跟着这个文件里那条路由走（这儿只存一条）：换了模型没顺手改这个数，用的还是旧数。
    没给它单独绑一条路由是因为两个框就在同一屏上——看得见的陈旧比一份看不见的映射表好。

    单位是 token 不是汉字，而设置页上**不许出现这个词**（屏幕守卫的纪律）。
    界面因此只说「模型说明里的那个数」，不替它安一个「字」的单位：那会是句假话
    （中文一个字约 0.6 token），而两个方向的错都还好——填大了端点当场拒（看得见），
    填小了只是上文短一点。
    """

    auto_update_model_windows: bool = False
    """开着的话，**每次打开工作台都去拉一次那份公开的模型表**。

    ── 这一位推翻了什么，以及为什么推翻得起 ────────────────────────────────

    `POST /api/settings/model-windows/refresh` 的注释写着「只在这儿点，绝不自动跑」，
    理由是：那份表决定上文给作者 800 字还是 40,000 字，而它来自一个我们不控制的仓库，
    自动更新 = 别人改一行、作者明天的稿子上下文就变了，**而他不知道为什么**。

    那条理由的要害是最后半句。**这一位默认关着，只有作者自己拨开它**——拨开的那一刻
    他就知道了为什么，那半句不再成立。所以撤掉的是「替他决定」，不是「让他知道」：
    默认仍然是不自动跑（2026-08-14 作者要的开关，见 `api/app.py::_auto_refresh_windows`）。

    **拉不到就当无事发生**：启动时没网是常态，为它拦住工作台是把一件锦上添花的事
    变成一道门槛。旧的那份原样留着（`draft/windows.refresh` 自己也拒绝用空表覆盖）。
    """

    review_card_edits: bool = False
    """开着的话，**作者在人物卡上改完一格之后，叫核对模型验一遍**（2026-09-06）。

    ── 为什么是开关，不是默认行为 ──────────────────────────────────────────

    作者原话：「或者在设置开一个按钮，看用户要不要启用这个功能核对模型，当他修改
    这个角色卡的时候，核对模型就去检查这个内容和本章节内容是否冲突和之前改字段的
    相关值或者之后相关值有没有冲突」。

    默认关着，两个理由都不是「省钱」那么简单：

    1. **它每改一格花一次模型调用。** 作者理顺一个人物卡可能连着改十几格，
       那是十几次调用，而他改的时候心里往往已经有数。
    2. **它判错的时候是在质疑作者的决定。** 机器判错机器，作者笑一下就过去了；
       机器判错作者，那是在说「你写的东西不对」——这一侧的误报比另一侧刺人得多，
       所以要他自己拨开这个开关，而不是替他决定。

    开着的时候落的是 `text_advisory`（026 那一档），**只告警、永不阻断、不进队列**：
    改动本身早就生效了，这一问只是事后说一句「第 N 章那句原文好像不是这个意思」。
    """

    continuation_in_agent_mode: bool = False
    """开着的话，**写作助手（novel-agent 模式）开着时行内续写照跑**（2026-09-10）。

    ── 默认关：模式二里不续写，是作者定的 ──────────────────────────────────

    行内续写（ADR 0015）原本只认编辑器自己的信号（停手 1 秒），跟助手面板开没开
    无关——两者从来没绑在一起。作者 2026-09-10 看到助手开着、正文里还在往下冒灰字，
    裁定「模式二这个就不用有这个续写了」，随后又要了这颗开关：
    「添加一个设置按钮用来开模式二支持续写」。

    所以判据是「面板开着 **且** 这一位关着 → 不问模型」，判在前端
    （`continuation.ts::shouldSuggest`，那儿是续写「什么时候该去问」唯一的一份）。
    后端只存这一位；`/draft` 本身不看它——它是编辑器的行为，不是那条路由的行为。

    ── 为什么存在这儿 ──────────────────────────────────────────────────────
    同 `graph_max_nodes`：它是**这台机器上这个人的习惯**，不是这本书的属性，
    换一本书不该重置。也不放浏览器的 localStorage——「系统功能」那一栏的每一格
    都走这份文件，一格例外就是第二套存法。
    """

    graph_max_nodes: int | None = None
    """人物卡里那张关系图**一次最多画几个人**。`None` = 用引擎的默认
    （`graph.store.MAX_SUBGRAPH_NODES`，2026-09-09 起是 1000）。

    ── 它为什么值得作者能改 ────────────────────────────────────────────────
    这个数原来写死成 30，而真书上主角有几百条关系边：图上只画得出 30 个，剩下的
    连提都没提，作者看到的是一张**沉默地删过节**的图。提到 1000 之后主角画得全了，
    但「一张图该多密才还看得清」是审美和机器性能的事，不是引擎能替他判的——
    人少的书嫌乱可以调小，机器好的想全看可以调大。

    ── 为什么存在这儿，而不是跟着书走 ──────────────────────────────────────
    同 `context_window` 那条：它是**这台机器上这个人的偏好**（他的屏幕多大、
    机器多快），不是这本书的属性。换一本书不该把它重置掉。

    `None` 的语义和 `context_window` 完全一致（空 = 没填，不是填了个 0），
    校验也复用同一个 validator——两个「空串/0/负数都读成没填」的框，不该有两套规矩。
    """

    allow_thinking: bool = False
    """开着的话，**每一次模型调用都允许模型先思考再作答**（2026-09-13）。

    ── 默认关：不管什么模型，默认都不开思考 ─────────────────────────────

    维护者原话：「默认就是没有思考。默认不管什么模型都这样不开思考。」产品对每一次
    调用声明的 reasoning 本来就是 `off`（ADR 0011 D4），这一位没有改那条纪律，它改的是
    作者能不能自己推翻它——**按模型自动判「这个会思考所以默认开」是不许的**。

    ── 它为什么值得存在（真书上的那一天）────────────────────────────────

    作者用的是一条没登记的路由（中转 + `deepseek-v4.1-flash`）。那条路由在线上表达不了
    「关」（方言 `NONE`，什么都不发），端点默认开着思考，思考和正文共用 `max_tokens`，
    而 `off` 那一档的预算按没有思考算：120 字的总结预算 1,264，思考先吃掉 800～1,200，
    75 / 99 次交回来的是空的，屏幕上一片红。拨开这一位，预算按思考算（可见预算 +
    下面那个数），线上不再发「关」——端点自己的默认怎么想就怎么想。

    存在这儿而不跟书走，理由同 `graph_max_nodes`：它是这台机器上这个人对他那条路由的
    驾驶方式，换一本书不该重置。
    """

    thinking_budget: int | None = None
    """允许思考时，为思考预留的输出预算（token）。`None` = 用地板值
    （`draft.capabilities.THINKING_BUDGET_MIN`）。

    **作者只能往上调，不能往下**：合法范围是 `[THINKING_BUDGET_MIN, THINKING_BUDGET_MAX]`，
    HTTP 层（`api/app.py::SettingsBody`）当场 422 顶回范围外的数；这儿不重复那道校验，
    同 `graph_max_nodes`——盘上那份文件被手改出一个怪数时，起草那一层会用它并把
    后果摆在屏幕上（预算超了端点当场拒），而不是让整份设置读不出来。
    `allow_thinking` 关着时这个数不起作用，但**保留**——他上次调过的数下次拨开还在。
    `None` 的语义和 `context_window` 一致（空 = 没填），校验也复用同一个 validator。
    """

    @field_validator("context_window", "graph_max_nodes", "thinking_budget", mode="before")
    @classmethod
    def _unset_is_not_zero(cls, value: object) -> object:
        """空串 / 0 / 负数一律读成「没填」。

        **不许当成「填了个 0」**：0 的窗口会让 `plan_call` 当场抛，而作者清空那个框的
        意思显然是「别用我的数」，不是「一个字都别记」。判据只写在这一处——
        HTTP 层、磁盘上那份旧文件、以后任何入口读的都是同一条。
        """
        if value == "" or (isinstance(value, int) and not isinstance(value, bool) and value <= 0):
            return None
        return value

    @property
    def api_key_set(self) -> bool:
        return bool(self.api_key)

    @property
    def api_key_preview(self) -> str:
        """后四位预览 + 星号遮罩；没设 key 时是空串。"""
        if not self.api_key:
            return ""
        return "*" * max(len(self.api_key) - 4, 0) + self.api_key[-4:]


def load(path: Path | None = None) -> Settings:
    """读设置。文件不存在 / 解不开 → 空设置（不许炸：第一次打开就该是空表单）。"""
    target = path or _path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return Settings()
    return Settings(**raw)


def save(settings: Settings, path: Path | None = None) -> Settings:
    """写设置。目录自动建，文件 chmod 0600（钥匙是私人物品）。"""
    target = path or _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump(mode="json")
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(target, 0o600)
    return settings
