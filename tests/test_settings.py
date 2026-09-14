"""AI 设置（BYOK）：本机存取 + HTTP 边界遮蔽（2026-08-02）。

三条铁律：
1. 完整 key **只在本机文件里**，任何 HTTP 出参都不许带（本地环回也算出口）。
2. `api_key` 留空 = 保持原值：改地址/模型不用重粘钥匙。
3. 文件权限 0600——钥匙是私人物品。
"""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

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
    # `continuation_tail_limit` 走的是「设置页优先、环境变量兜底」那条链，
    # 所以跑测试的人 shell 里那几位会渗进出参里来。清干净，出参才只由设置文件决定。
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
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
        # 没填是 `null`，**不是 0**：屏幕上「没填」和「填了个 0」是两件事，
        # 混成一个数就再也分不开了（0 那个数会让起草当场抛）。
        "context_window": None,
        # **默认关着**：那份模型表决定上文给作者 800 字还是 40,000 字，
        # 而它来自一个我们不控制的仓库。开着它的只能是作者本人。
        "auto_update_model_windows": False,
        "review_card_edits": False,
        # **默认关着**：写作助手开着时不续写，是作者 2026-09-10 定的；要它开的人自己拨。
        "continuation_in_agent_mode": False,
        # 关系图一次最多画几个人。同 `context_window`：没填是 `null`。
        # **默认值单独回一位**：那个空框在屏幕上得说得出「不填会是多少」，
        # 而那个数是引擎的常量（`graph.store.MAX_SUBGRAPH_NODES`），前端不许自己抄一份
        # ——抄了就有两个真相源，而引擎改了之后设置页会安静地说一个旧数。
        "graph_max_nodes": None,
        "graph_max_nodes_default": 1000,
        # **默认不开思考，不管什么模型**（维护者 2026-09-13 的原话）。数没填是 `null`
        # （那时用地板值）；地板 / 顶两个数由后端回，前端不许自己抄一份。
        "allow_thinking": False,
        "thinking_budget": None,
        "thinking_budget_min": 8192,
        "thinking_budget_max": 32768,
        # 续写能带多少上文（后端按模型窗口算，前端不许存第二份）。一个字都没配过时
        # 它是地板值，**而 `basis` 说得出为什么是地板值**——「认不出模型」和「还没填」
        # 算出来的数一模一样，不区分开的话，少给上文就是一件没人看得见的事。
        # 这条缝两头由 `tests/test_continuation_tail_limit.py` 钉着。
        "continuation_tail_limit": 800,
        "continuation_tail_basis": "unconfigured",
        # 三样都在了没有——右上那盏灯和每一格的空态问的是这一位（判在后端，环境变量
        # 兜底那一档前端看不见）。一个字都没配过 = False。
        "model_configured": False,
    }


def test_model_configured_needs_all_three(client: TestClient) -> None:
    """服务地址 / 模型 / 钥匙缺任何一样都不算配好——少一样模型就调不起来，而灯要说实话。"""
    assert client.get("/api/settings").json()["model_configured"] is False
    r = client.put("/api/settings", json={"base_url": "https://x.example", "model": "m"})
    assert r.status_code == 200 and r.json()["model_configured"] is False
    r = client.put("/api/settings", json={"api_key": "sk-123456"})
    assert r.status_code == 200 and r.json()["model_configured"] is True


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


# ══════════════════════════════════════════════════════════════════════════
# 手填的上下文窗口：**空值语义和上面三个字段相反**
# ══════════════════════════════════════════════════════════════════════════
#
# 这一位决定逐字上文给作者 800 字还是上万字（`draft/windows.py` 那份自动推断对
# 自建端点永远答不出来）。它和钥匙的区别只有一条：**钥匙不回显，这个数回显着**——
# 所以「留空 = 保持原值」会让那个框变成一个只进不出的洞，作者收不回一个填错的数。


def test_the_window_round_trips_through_http(client: TestClient) -> None:
    r = client.put(
        "/api/settings",
        json={"base_url": "https://a.example", "model": "m1", "context_window": 131072},
    )
    assert r.status_code == 200
    assert r.json()["context_window"] == 131072
    assert client.get("/api/settings").json()["context_window"] == 131072


def test_a_request_without_the_key_keeps_it(client: TestClient) -> None:
    """**判据是「这次请求里带没带这个键」，不是它的值。**

    否则任何一个不认识这一位的老客户端（或者别处一段只想改地址的代码）都会把它悄悄抹掉，
    而抹掉的症状是上文塌回 800 字，没有任何一处会红。
    """
    client.put(
        "/api/settings",
        json={"base_url": "https://a.example", "model": "m1", "context_window": 65536},
    )
    r = client.put("/api/settings", json={"base_url": "https://b.example", "model": "m2"})
    assert r.json()["context_window"] == 65536


@pytest.mark.parametrize("cleared", [None, 0, -1])
def test_sending_an_empty_value_clears_it(client: TestClient, cleared: int | None) -> None:
    """作者必须收得回一个填错的数：**发空 = 清掉**（0 / 负数同样是「没填」）。"""
    client.put(
        "/api/settings",
        json={"base_url": "https://a.example", "model": "m1", "context_window": 65536},
    )
    r = client.put(
        "/api/settings",
        json={"base_url": "", "model": "", "context_window": cleared},
    )
    assert r.json()["context_window"] is None
    assert client.get("/api/settings").json()["context_window"] is None


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


def test_a_newer_versions_keys_on_disk_do_not_break_this_version(
    settings_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """盘上多出来的键（新版存的、旧版不认识的）读时丢掉，认识的那几位原样在。

    0.0.6 的桌面包就是这么打不开的：调试线写进两个新键，桌面包在 `load()` 里抛
    `ValidationError`，`Application startup failed`，作者看到的只是「装了打不开」。
    """
    monkeypatch.setenv("NH_SETTINGS_PATH", str(settings_path))
    settings_path.write_text(
        json.dumps(
            {
                "base_url": "https://a.example",
                "model": "m1",
                "api_key": "sk-keep-me",
                "a_key_from_the_future": {"nested": True},
                "another_one": 42,
            }
        ),
        encoding="utf-8",
    )
    loaded = settings_module.load()
    assert loaded.base_url == "https://a.example"
    assert loaded.model == "m1"
    assert loaded.api_key == "sk-keep-me"
    assert "a_key_from_the_future" not in loaded.model_dump()

    # HTTP 那一层照旧不收陌生键——「别把打错的键当成设置存进去」那道守卫搬到了这儿。
    from novel_harness.api.app import SettingsBody

    with pytest.raises(ValidationError):
        SettingsBody(a_key_from_the_future=1)  # type: ignore[call-arg]


def test_default_path_is_user_config_not_the_book() -> None:
    assert settings_module.DEFAULT_PATH == (
        Path.home() / ".config" / "novel-harness" / "settings.json"
    )


# ══════════════════════════════════════════════════════════════════════════
# 「每次打开工作台自动更新模型表」那颗开关（2026-08-14）
# ══════════════════════════════════════════════════════════════════════════
#
# `refresh_model_windows` 那条路由的注释写着「只在这儿点，绝不自动跑」，理由是那份表
# 决定上文给作者 800 字还是 40,000 字，自动更新 = 别人改一行、作者明天的稿子变了、
# **而他不知道为什么**。这颗开关**默认关着、由作者自己拨开**——拨开的那一刻他就知道了
# 为什么，那条理由的要害（最后半句）不再成立。下面这几条钉的就是「默认关着」。


def test_auto_update_round_trips(client: TestClient) -> None:
    r = client.put("/api/settings", json={"auto_update_model_windows": True})
    assert r.json()["auto_update_model_windows"] is True
    assert client.get("/api/settings").json()["auto_update_model_windows"] is True

    off = client.put("/api/settings", json={"auto_update_model_windows": False})
    assert off.json()["auto_update_model_windows"] is False


def test_toggling_it_does_not_touch_anything_else(client: TestClient) -> None:
    """**那颗开关只发它自己那一位。**

    界面上它和「服务地址」在同一扇窗里，而作者可能正敲了一半——拨一下开关就把
    半截地址提交上去，是这一层最容易漏的那种错。
    """
    client.put(
        "/api/settings",
        json={"base_url": "https://a.example", "model": "m1", "context_window": 65536},
    )
    r = client.put("/api/settings", json={"auto_update_model_windows": True})
    body = r.json()
    assert body["base_url"] == "https://a.example"
    assert body["model"] == "m1"
    assert body["context_window"] == 65536


def test_a_request_without_the_switch_keeps_it(client: TestClient) -> None:
    """同 `context_window`：判据是**带没带这个键**，不是它的值。

    不这样的话，任何一个只想改地址的请求都会顺手把作者拨开的那个开关关回去。
    """
    client.put("/api/settings", json={"auto_update_model_windows": True})
    r = client.put("/api/settings", json={"base_url": "https://b.example"})
    assert r.json()["auto_update_model_windows"] is True


def test_continuation_in_agent_mode_round_trips_and_survives_other_writes(
    client: TestClient,
) -> None:
    """「写作助手开着时续写」那颗开关：默认关、拨开记得住、别的请求不会顺手关回去。

    判据同 `auto_update_model_windows`：**带没带这个键**，不是它的值——只想改地址的
    请求发的是一个没有这一位的 body，它不该把作者拨开的开关抹掉。
    """
    assert client.get("/api/settings").json()["continuation_in_agent_mode"] is False

    on = client.put("/api/settings", json={"continuation_in_agent_mode": True})
    assert on.json()["continuation_in_agent_mode"] is True
    assert client.get("/api/settings").json()["continuation_in_agent_mode"] is True

    other = client.put("/api/settings", json={"base_url": "https://b.example"})
    assert other.json()["continuation_in_agent_mode"] is True

    off = client.put("/api/settings", json={"continuation_in_agent_mode": False})
    assert off.json()["continuation_in_agent_mode"] is False


def test_allow_thinking_round_trips_and_survives_other_writes(client: TestClient) -> None:
    """「允许模型思考」那颗开关：默认关、拨开记得住、别的请求不会顺手关回去。

    判据同上面两颗：**带没带这个键**，不是它的值。
    """
    assert client.get("/api/settings").json()["allow_thinking"] is False

    on = client.put("/api/settings", json={"allow_thinking": True})
    assert on.json()["allow_thinking"] is True
    assert client.get("/api/settings").json()["allow_thinking"] is True

    other = client.put("/api/settings", json={"base_url": "https://b.example"})
    assert other.json()["allow_thinking"] is True

    off = client.put("/api/settings", json={"allow_thinking": False})
    assert off.json()["allow_thinking"] is False


def test_thinking_budget_round_trips_clears_and_outlives_the_switch(client: TestClient) -> None:
    """那个数：写得进、读得回、发空清掉、**关掉开关也不丢**（下次拨开还是他调过的数）。"""
    r = client.put("/api/settings", json={"allow_thinking": True, "thinking_budget": 16384})
    assert r.json()["thinking_budget"] == 16384
    assert client.get("/api/settings").json()["thinking_budget"] == 16384

    # 关掉开关只发开关那一位：数留着。
    off = client.put("/api/settings", json={"allow_thinking": False})
    assert off.json()["allow_thinking"] is False
    assert off.json()["thinking_budget"] == 16384

    # 发空 = 清掉 = 回落地板值（屏幕上显示成空框 + 占位符，不是一个数）。
    cleared = client.put("/api/settings", json={"thinking_budget": None})
    assert cleared.json()["thinking_budget"] is None


@pytest.mark.parametrize("bad", [1, 8191, 32769, 1_000_000])
def test_thinking_budget_outside_the_range_is_refused(client: TestClient, bad: int) -> None:
    """地板之下 = 开关拨开却失效（屏幕上只是「总结还是红的」）；顶之上 = 一份在已登记
    路由上必被拒的 plan。两头都当场 422，不悄悄夹回范围里（这个仓库不 clamp）。"""
    r = client.put("/api/settings", json={"thinking_budget": bad})
    assert r.status_code == 422
    assert client.get("/api/settings").json()["thinking_budget"] is None


def test_startup_pulls_only_when_the_switch_is_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novel_harness.api import app as app_module

    pulls: list[int] = []
    monkeypatch.setattr(app_module, "pull_model_windows", lambda: pulls.append(1))

    # 关着（默认）：**一次都不许拉**。它是一次跨公网的请求，也是一次会改上文长度的更新。
    assert app_module._auto_refresh_windows() is None
    assert pulls == []

    client.put("/api/settings", json={"auto_update_model_windows": True})
    thread = app_module._auto_refresh_windows()
    assert thread is not None
    thread.join(timeout=5)
    assert pulls == [1]


def test_startup_pull_failure_is_not_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动时没网是常态。为它拦住工作台，就是把锦上添花变成一道门槛——
    作者断网时连自己的稿子都打不开了。"""
    from novel_harness.api import app as app_module

    def boom() -> None:
        raise app_module.ModelWindowsPullFailed("没网")

    monkeypatch.setattr(app_module, "pull_model_windows", boom)
    client.put("/api/settings", json={"auto_update_model_windows": True})

    thread = app_module._auto_refresh_windows()
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()  # 线程里那一下没把异常带出来
