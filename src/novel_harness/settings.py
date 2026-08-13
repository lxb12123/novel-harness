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

    @field_validator("context_window", mode="before")
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
