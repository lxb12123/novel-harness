"""`/draft` 端点（修正案 7，实验状态，2026-08-02）。

放行 ≠ 验证：响应必须带 ``experimental`` 标注；连接参数走 AI 设置页（BYOK）
优先、环境变量兜底；没配置 / 角色解析不了 / form 非法都必须 422。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import db, project
from novel_harness.declare import Ledger
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph

DRAFT_TEXT = "萧决道：「此剑无名。」"
ZH_LENGTH = {
    "language": "zh",
    "min_units": 2000,
    "target_units": 2500,
    "max_units": 3000,
}


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    dbp = tmp_path / "book.db"
    conn = db.connect(dbp)
    db.migrate(conn)
    pid = project.create(conn, name="测试书", root_path=str(tmp_path / "书")).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    conn.close()
    return {"db": str(dbp), "pid": pid}


@pytest.fixture
def client(
    book: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", book["db"])
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("NH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("NH_LLM_MODEL", raising=False)
    monkeypatch.delenv("NH_LLM_API_KEY", raising=False)
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _url(book: dict[str, str]) -> str:
    return f"/api/projects/{book['pid']}/chapters/1/draft"


def _configure(client: TestClient) -> None:
    r = client.put(
        "/api/settings",
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
        },
    )
    assert r.status_code == 200, r.text


def _stub_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    import novel_harness.draft.generate as generate_mod
    from novel_harness.draft.provider import CompletionResult

    def fake(
        messages,
        *,
        config=None,
        plan=None,
        client=None,
    ) -> CompletionResult:
        return CompletionResult(
            text=DRAFT_TEXT, model="fake", finish_reason="stop"
        )

    monkeypatch.setattr(generate_mod, "complete", fake)


def test_draft_returns_experimental_draft(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
            "goal": "萧决看剑。",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "form": "X1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["experimental"] is True
    assert "实验状态" in body["note"]
    assert DRAFT_TEXT in body["text"]
    assert body["attempts"] == 2  # 桩文本不足下限 → 续写一次


def test_draft_without_connection_config_is_422(
    client: TestClient, book: dict[str, str]
) -> None:
    r = client.post(
        _url(book),
        json={"goal": "x", "cast": ["萧决"], "length": ZH_LENGTH},
    )
    assert r.status_code == 422
    assert "AI 设置" in r.text


def test_draft_unresolvable_cast_is_422(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={"goal": "x", "cast": ["不存在的人"], "length": ZH_LENGTH},
    )
    assert r.status_code == 422
    assert "解析不了" in r.text


def test_draft_bad_form_is_422(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
            "goal": "x",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "form": "X9",
        },
    )
    assert r.status_code == 422
    assert "X0" in r.text


def test_draft_custom_house_style_is_accepted(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
            "goal": "x",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "house_style": "文白夹杂，多用短句，对白简洁。",
        },
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("word", ["秘密", "不知道", "泄露", "剧透", "伏笔", "设定"])
def test_draft_house_style_forbidden_hints_are_422(
    client: TestClient, book: dict[str, str], word: str
) -> None:
    _configure(client)
    r = client.post(
        _url(book),
        json={
            "goal": "x",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "house_style": f"写的时候{f'不要{word}'}任何情节。",
        },
    )
    assert r.status_code == 422
    assert "三臂共用" in r.text
