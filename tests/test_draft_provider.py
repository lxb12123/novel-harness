"""统一模型出口的单测。**不联网、不装 openai**:注入一个鸭子类型的假客户端。

核心不变式:调用参数(model/temperature/max_tokens)全从 `ProviderConfig` 来,原样传到
统一出口 —— 这是 kill-gate 三臂与生产共用一份参数的基础。
"""

from __future__ import annotations

import types

import pytest

from novel_harness.draft.provider import (
    DEFAULT_MODEL,
    CompletionResult,
    ProviderConfig,
    ProviderError,
    complete,
)


def _fake_client(content: str = "草稿正文", *, raise_exc: Exception | None = None):
    calls: dict[str, dict] = {}

    def create(**kwargs):
        calls["kwargs"] = kwargs
        if raise_exc is not None:
            raise raise_exc
        return types.SimpleNamespace(
            model="served-by-fake",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content=content),
                    finish_reason="stop",
                )
            ],
            usage=types.SimpleNamespace(prompt_tokens=12, completion_tokens=7),
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    return client, calls


# ── 配置来自环境变量,作者不碰代码 ─────────────────────────────────────────


def test_config_from_env_reads_the_three_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-xxx")
    cfg = ProviderConfig.from_env()
    assert cfg.model == "deepseek-chat"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.api_key == "sk-xxx"


def test_config_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("NH_LLM_MODEL", "NH_LLM_BASE_URL", "NH_LLM_TEMPERATURE", "NH_LLM_MAX_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    cfg = ProviderConfig.from_env()
    assert cfg.model == DEFAULT_MODEL
    assert cfg.base_url is None  # 空串 → None(而不是拿空串当端点)


def test_temperature_can_be_omitted_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_LLM_TEMPERATURE", "none")
    assert ProviderConfig.from_env().temperature is None


# ── complete():参数冻结、结果映射、错误收口 ──────────────────────────────


def test_complete_freezes_params_from_config() -> None:
    client, calls = _fake_client("这一场,他终究没有提起那件事。")
    cfg = ProviderConfig(model="qwen2.5", temperature=0.3, max_tokens=1024)
    res = complete([{"role": "user", "content": "写一段"}], config=cfg, client=client)

    assert isinstance(res, CompletionResult)
    assert res.text.startswith("这一场")
    assert res.finish_reason == "stop"
    assert res.completion_tokens == 7
    assert res.model == "served-by-fake"
    # 参数原样传到统一出口 —— kill-gate 三臂共用这一份
    assert calls["kwargs"]["model"] == "qwen2.5"
    assert calls["kwargs"]["temperature"] == 0.3
    assert calls["kwargs"]["max_tokens"] == 1024


def test_complete_omits_temperature_when_none() -> None:
    client, calls = _fake_client()
    complete([{"role": "user", "content": "x"}], config=ProviderConfig(temperature=None), client=client)
    assert "temperature" not in calls["kwargs"]


def test_complete_wraps_transport_errors() -> None:
    client, _ = _fake_client(raise_exc=RuntimeError("connection refused"))
    with pytest.raises(ProviderError, match="模型调用失败"):
        complete([{"role": "user", "content": "x"}], config=ProviderConfig(), client=client)
