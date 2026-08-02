"""AI 设置（BYOK）：本机存取 + HTTP 边界遮蔽（2026-08-02）。

三条铁律：
1. 完整 key **只在本机文件里**，任何 HTTP 出参都不许带（本地环回也算出口）。
2. `api_key` 留空 = 保持原值：改地址/模型不用重粘钥匙。
3. 文件权限 0600——钥匙是私人物品。
"""

from __future__ import annotations

import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import settings as settings_module
from novel_harness.db import connect, migrate


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    return tmp_path / "settings.json"


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    db = tmp_path / "book.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    monkeypatch.setenv("NH_DB", str(db))
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


# ══════════════════════════════════════════════════════════════════════════
# HTTP 边界：遮蔽
# ══════════════════════════════════════════════════════════════════════════


def test_get_settings_starts_empty(client: TestClient) -> None:
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json() == {
        "base_url": "",
        "model": "",
        "api_key_set": False,
        "api_key_preview": "",
    }


def test_put_saves_and_never_returns_the_full_key(client: TestClient) -> None:
    r = client.put(
        "/api/settings",
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-abcdef1234",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["base_url"] == "https://api.deepseek.com"
    assert body["model"] == "deepseek-v4-flash"
    assert body["api_key_set"] is True
    assert body["api_key_preview"] == "*********1234"
    assert "sk-abcdef1234" not in r.text

    again = client.get("/api/settings")
    assert again.status_code == 200
    assert "sk-abcdef1234" not in again.text
    assert again.json()["api_key_preview"] == "*********1234"


def test_empty_key_keeps_the_existing_one(client: TestClient) -> None:
    client.put(
        "/api/settings",
        json={"base_url": "https://a.example", "model": "m1", "api_key": "sk-keep-me"},
    )
    r = client.put(
        "/api/settings",
        json={"base_url": "https://b.example", "model": "m2", "api_key": ""},
    )
    body = r.json()
    assert body["base_url"] == "https://b.example"
    assert body["model"] == "m2"
    assert body["api_key_set"] is True
    assert "sk-keep-me" not in r.text
    assert body["api_key_preview"].endswith("p-me")


def test_empty_fields_keep_existing_values(client: TestClient) -> None:
    client.put(
        "/api/settings",
        json={"base_url": "https://a.example", "model": "m1", "api_key": "sk-x"},
    )
    r = client.put(
        "/api/settings",
        json={"base_url": "", "model": "", "api_key": ""},
    )
    body = r.json()
    assert body["base_url"] == "https://a.example"
    assert body["model"] == "m1"


# ══════════════════════════════════════════════════════════════════════════
# 本机存储：权限与容错
# ══════════════════════════════════════════════════════════════════════════


def test_saved_file_is_owner_only_and_round_trips(
    settings_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NH_SETTINGS_PATH", str(settings_path))
    saved = settings_module.save(
        settings_module.Settings(
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="sk-secret",
        )
    )
    assert saved.api_key == "sk-secret"
    mode = stat.S_IMODE(settings_path.stat().st_mode)
    assert mode == 0o600
    # 磁盘上确实有完整 key（这是本机存储的本意）——但 HTTP 侧永不回吐。
    assert "sk-secret" in settings_path.read_text(encoding="utf-8")

    loaded = settings_module.load()
    assert loaded == saved


def test_missing_or_corrupt_file_loads_empty(
    settings_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NH_SETTINGS_PATH", str(settings_path))
    assert settings_module.load() == settings_module.Settings()
    settings_path.write_text("{ 这不是 json", encoding="utf-8")
    assert settings_module.load() == settings_module.Settings()


def test_default_path_is_user_config_not_the_book() -> None:
    assert settings_module.DEFAULT_PATH == (
        Path.home() / ".config" / "novel-harness" / "settings.json"
    )
