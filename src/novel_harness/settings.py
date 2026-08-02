"""AI 设置（BYOK）：base_url / model / api_key 的本机存储（2026-08-02）。

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

from pydantic import BaseModel, ConfigDict, Field

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
