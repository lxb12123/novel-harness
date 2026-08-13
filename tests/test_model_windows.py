"""对抗性验证：那份打包进来的公共快照，会不会把一个假窗口塞进作者的起草。

这个模块的收益很直白（上文从 800 字变成上万字），所以**判据全部押在「它什么时候
应该认不出」**上——认错一个窗口的后果是高估，而高估的表现是请求被拒。

五条网：

1. **它排在我们自己那张表后面**（注册表那 20 条一条都不许被公共表改写）
2. **只有两个精确的键**，一个模糊匹配都不做
3. **自建端点认不出**——这是唯一会造成真实伤害的那条路径
4. **只裁了窗口这一列**，输出上限有意不取（实测公共表滞后一整代）
5. **快照坏掉/缺失只退回 unknown**，不许把起草弄挂
"""

from __future__ import annotations

import json

import pytest

from novel_harness.draft import windows
from novel_harness.draft.assemble import GATE_TAIL_CODE_POINTS, product_tail_limit
from novel_harness.draft.capabilities import CAPABILITY_REGISTRY, resolve_capabilities
from novel_harness.draft.discovery import resolve_with_discovery
from novel_harness.draft.windows import capabilities_from_snapshot, window_for

NO_FETCH = staticmethod(lambda _url: None)


@pytest.fixture(autouse=True)
def _fresh_snapshot() -> None:
    windows._snapshot.cache_clear()


# ══════════════════════════════════════════════════════════════════════════
# 1. 排在我们自己那张表后面
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("route", sorted(CAPABILITY_REGISTRY))
def test_the_public_snapshot_never_overrides_our_own_table(route: tuple[str, str]) -> None:
    """**登记过的路由一条都不许被公共表改写。**

    这不是洁癖：公共表在输出上限那一维**实测滞后一整代**（`deepseek-v4` 写 8,192，
    官方文档 384,000，差 47 倍），而 M2 判分链和 `nh gate` 走的就是这些路由。
    """
    assert resolve_with_discovery(*route, fetch=NO_FETCH) is CAPABILITY_REGISTRY[route]


def test_our_table_and_the_snapshot_disagree_and_ours_wins() -> None:
    """把那个分歧钉成一条测试 —— 它是上一条存在的全部理由。

    公共表说 `deepseek-v4-flash` 的输出上限是 8,192；官方文档是 384,000。
    照公共表填，一次整章起草（可见预算 7,024）会被截成一小段。
    """
    ours = CAPABILITY_REGISTRY[("https://api.deepseek.com", "deepseek-v4-flash")]
    assert ours.max_output_tokens == 384_000

    payload = json.loads(windows.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert "deepseek-v4-flash" in payload["windows"], "公共表里有它，所以这个对照是活的"
    # 而快照里**只有窗口一列** —— 输出上限根本没被裁进来，见下面第 4 条。


# ══════════════════════════════════════════════════════════════════════════
# 2 & 3. 两个精确的键；自建端点认不出
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("base_url", "model"),
    [
        ("https://api.deepseek.com", "deepseek-chat"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        ("https://api.moonshot.cn/v1", "kimi-k2-0905-preview"),
        ("https://api.x.ai/v1", "grok-4"),
    ],
)
def test_a_bare_model_name_is_found_through_the_hosts_provider(base_url: str, model: str) -> None:
    """作者填的是**裸名**（`qwen-plus`），公共表的键带前缀（`dashscope/qwen-plus`）。

    补前缀用的是 `base_url` 的主机名 —— **路由的两半都用上了，不是模糊匹配**。
    不补这一层，国内那几家一条都命中不了。
    """
    found = window_for(base_url, model)
    assert found is not None and found > 8_000
    assert product_tail_limit(found, 7_024) > GATE_TAIL_CODE_POINTS


def test_a_homebrew_endpoint_with_a_famous_name_is_not_believed() -> None:
    """**这条是整个模块唯一会造成真实伤害的那条路径。**

    作者在自己机器上跑一个量化过的小模型，随手起名 `qwen-plus`。公共表里那个
    `dashscope/qwen-plus` 是 129,024 —— 照它填就是**高估**，症状是请求被拒。

    那道闸是「主机名必须在 `_HOST_PROVIDERS` 里」：`localhost` 不在，
    于是这一档安全地退回 `unknown`（上文 800 字，保守但不会坏）。
    """
    assert window_for("http://localhost:11434/v1", "qwen-plus") is None
    assert capabilities_from_snapshot("http://localhost:11434/v1", "qwen-plus") is None

    capability = resolve_with_discovery("http://localhost:11434/v1", "qwen-plus", fetch=NO_FETCH)
    assert capability.source == "unknown"
    assert product_tail_limit(capability.max_context_tokens, 7_024) == GATE_TAIL_CODE_POINTS


def test_a_lookalike_host_is_not_believed_either() -> None:
    """主机名判据是**精确相等**，不是子串 —— 同 `discovery` 那条。

    子串判据会让 `api.deepseek.com.evil.example.com` 命中，而那时一个陌生主机
    就替我们决定了上文给多长。
    """
    assert window_for("https://api.deepseek.com.evil.example.com/v1", "deepseek-chat") is None


def test_a_model_nobody_has_heard_of_is_simply_unknown() -> None:
    assert window_for("https://api.deepseek.com", "totally-made-up-model") is None


def test_an_already_qualified_name_is_not_double_prefixed() -> None:
    """作者直接填 `deepseek/deepseek-chat` 时不许拼成 `deepseek/deepseek/deepseek-chat`。"""
    assert window_for("https://api.deepseek.com", "deepseek/deepseek-chat") is not None


# ══════════════════════════════════════════════════════════════════════════
# 4. 只裁了窗口这一列
# ══════════════════════════════════════════════════════════════════════════


def test_the_snapshot_carries_exactly_one_number_per_model() -> None:
    """**输出上限有意不取。** 它在公共表里会滞后一整代，而填错的后果是稿子被截断。

    这条同时钉住快照的形状：`windows` 是「模型名 → 一个正整数」，没有嵌套对象。
    多裁一列的那天，这里会红。
    """
    payload = json.loads(windows.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert payload["schema"] == windows.SNAPSHOT_SCHEMA
    assert payload["source_license"].startswith("MIT")
    assert all(
        isinstance(name, str) and isinstance(value, int) and value > 0
        for name, value in payload["windows"].items()
    )


def test_the_snapshot_capability_claims_nothing_it_cannot_back() -> None:
    """除了窗口，一律最保守的那一档 —— **快照只知道一件事，多声称一位就是编。**"""
    capability = capabilities_from_snapshot("https://api.deepseek.com", "deepseek-chat")
    assert capability is not None
    assert capability.max_context_tokens is not None
    assert capability.max_output_tokens is None
    assert capability.source == "snapshot:litellm-model-windows"
    assert capability.source_urls and "litellm" in capability.source_urls[0]


# ══════════════════════════════════════════════════════════════════════════
# 5. 快照坏了只退回 unknown
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{ not json", id="不是 JSON"),
        pytest.param('{"schema": "something-else", "windows": {"x": 1}}', id="schema 对不上"),
        pytest.param('{"schema": "nh-model-windows-v1"}', id="没有 windows"),
        pytest.param('{"schema": "nh-model-windows-v1", "windows": []}', id="windows 不是对象"),
        pytest.param(None, id="文件不存在"),
    ],
)
def test_a_broken_snapshot_only_costs_us_the_number(
    content: str | None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**一个数据文件不该有能力把起草弄挂。** 读不出来 = 退回今天的 `unknown`。"""
    target = tmp_path / "model_windows.json"
    if content is not None:
        target.write_text(content, encoding="utf-8")
    monkeypatch.setattr(windows, "SNAPSHOT_PATH", target)
    windows._snapshot.cache_clear()

    assert window_for("https://api.deepseek.com", "deepseek-chat") is None
    capability = resolve_capabilities("https://api.deepseek.com", "deepseek-chat")
    assert capability.source == "unknown"
    windows._snapshot.cache_clear()
