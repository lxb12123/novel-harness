"""「允许模型思考」（2026-09-13，ADR 0052）：默认不开、开了按思考算预算、七处调用一处不漏。

维护者原话：「加一个按钮用于打开允许思考的开关（用户打开后给一个思考后预算的最低允许值）
然后之后依旧允许用户去调高数值的选项。设置一个最高值」「默认就是没有思考。默认不管什么
模型都这样不开思考。」

这份文件钉四件事，按层：
1. **plan 的算术**：`off` 上 `required = visible + 预留`；预留只对 `off` 成立；0 = 一字不差。
2. **线上的形状**：预留 > 0 时**一个 reasoning 字段都不发**（「允许」不是「要求」）；
   预留 = 0 时照旧按方言发「关」。
3. **装配层那一处换算**：开关关 → 0；开、没填 → 地板；开、填了 → 那个数。
4. **七处调用都从那一处拿**——漏一处的症状是「开关拨开了，一半的功能听、一半不听」。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from novel_harness.draft.capabilities import (
    THINKING_BUDGET_MAX,
    THINKING_BUDGET_MIN,
    CapabilityError,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    StructuredCallPlan,
    plan_call,
    plan_structured_call,
    resolve_capabilities,
)
from novel_harness.draft.provider import ProviderConfig, _wire_kwargs
from novel_harness.draft.summarize import SUMMARY_LENGTH

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src" / "novel_harness"

LOCAL = "http://localhost:11434/v1"
MESSAGES = [{"role": "user", "content": "x"}]


def _caps(
    dialect: ReasoningDialect = ReasoningDialect.NONE, **updates: object
) -> ProviderCapabilities:
    values: dict[str, object] = {
        "base_url": LOCAL,
        "model": "test-model",
        "source": "operator-test",
        "source_urls": ("https://example.test/model",),
        "max_context_tokens": 200_000,
        "max_output_tokens": 128_000,
        "max_tokens_field": "max_tokens",
        "reasoning_levels": (
            frozenset({ReasoningEffort.OFF})
            if dialect is ReasoningDialect.NONE
            else frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH})
        ),
        "reasoning_dialect": dialect,
        "reasoning_shares_output": dialect is not ReasoningDialect.NONE,
        "reserve_ratio_high": 0.8 if dialect is not ReasoningDialect.NONE else None,
    }
    values.update(updates)
    return ProviderCapabilities(**values)


# ── ① plan 的算术 ─────────────────────────────────────────────────────────


def test_the_floor_and_ceiling_are_the_numbers_the_author_sees() -> None:
    """地板 8,192（真书实测下界的 ~8 倍）、顶 32,768（加最大可见预算 8,192 仍在注册表最小的
    输出上限 64,000 之下）。改它们先改 `capabilities.py` 那两段注释里的论证。"""
    assert THINKING_BUDGET_MIN == 8_192
    assert THINKING_BUDGET_MAX == 32_768
    assert THINKING_BUDGET_MIN < THINKING_BUDGET_MAX


def test_zero_budget_is_byte_for_byte_the_old_plan() -> None:
    """默认关 = 一个字节都不变：这是 M2 三臂 / gate 那条路仍然成立的全部理由。"""
    before = plan_call(SUMMARY_LENGTH, ReasoningEffort.OFF, _caps())
    after = plan_call(SUMMARY_LENGTH, ReasoningEffort.OFF, _caps(), thinking_token_budget=0)
    assert before == after
    assert after.thinking_token_budget == 0
    assert after.request_token_budget == SUMMARY_LENGTH.max_units * 2 + 1_024


def test_the_budget_is_added_to_the_visible_budget_not_multiplied() -> None:
    """真书那一天：总结可见预算 1,264，思考吃掉 800～1,200 就空了。加上地板之后是 9,456。"""
    plan = plan_call(
        SUMMARY_LENGTH, ReasoningEffort.OFF, _caps(), thinking_token_budget=THINKING_BUDGET_MIN
    )
    assert plan.visible_token_budget == 1_264
    assert plan.required_token_budget == 1_264 + THINKING_BUDGET_MIN
    assert plan.request_token_budget == plan.required_token_budget
    assert plan.thinking_token_budget == THINKING_BUDGET_MIN


def test_structured_calls_get_the_same_arithmetic() -> None:
    """抽取那一档（可见 8,192）加地板之后过了 16,000 的流式阈值——**这是对的**：
    一次带思考的调用可能跑几分钟，流式正是为「一次阻塞往返扛不住」开的。"""
    plan = plan_structured_call(
        8_192, ReasoningEffort.OFF, _caps(), thinking_token_budget=THINKING_BUDGET_MIN
    )
    assert plan.required_token_budget == 8_192 + THINKING_BUDGET_MIN
    assert plan.stream is True
    small = plan_structured_call(1_024, ReasoningEffort.OFF, _caps(), thinking_token_budget=4_096)
    assert small.stream is False


def test_the_budget_only_applies_to_reasoning_off() -> None:
    """非 off 的档位走能力表上审计过的预留比；再叠一份作者的数就是两本账。"""
    shared = _caps(ReasoningDialect.DEEPSEEK)
    with pytest.raises(CapabilityError, match="only applies to reasoning=off"):
        plan_call(SUMMARY_LENGTH, ReasoningEffort.HIGH, shared, thinking_token_budget=8_192)
    with pytest.raises(CapabilityError, match="only applies to reasoning=off"):
        plan_structured_call(1_024, ReasoningEffort.HIGH, shared, thinking_token_budget=8_192)
    # 0 在 high 上仍然合法——那就是从前的 high plan。
    high = plan_call(SUMMARY_LENGTH, ReasoningEffort.HIGH, shared, thinking_token_budget=0)
    assert high.required_token_budget == 6_320  # ceil(1264 / (1 - 0.8))


@pytest.mark.parametrize("bad", [-1, 1.5, True])
def test_the_budget_must_be_a_non_negative_integer(bad: object) -> None:
    with pytest.raises(CapabilityError, match="thinking_token_budget"):
        plan_call(
            SUMMARY_LENGTH,
            ReasoningEffort.OFF,
            _caps(),
            thinking_token_budget=bad,  # type: ignore[arg-type]
        )


def test_directly_built_plans_recompute_required_with_the_budget() -> None:
    """两份 plan 的校验器各自重算 `required`——**算法只写一份**（`_expected_required`），
    直接构造的 plan 少加或多加预留都过不去。"""
    caps = _caps()
    values: dict[str, object] = {
        "base_url": caps.base_url,
        "model": caps.model,
        "length": SUMMARY_LENGTH,
        "prompt_token_budget": 0,
        "visible_token_budget": 1_264,
        "required_token_budget": 1_264 + 8_192,
        "request_token_budget": 1_264 + 8_192,
        "max_tokens_field": "max_tokens",
        "reasoning_requested": ReasoningEffort.OFF,
        "reasoning_effective": ReasoningEffort.OFF,
        "reasoning_dialect": ReasoningDialect.NONE,
        "stream": False,
        "thinking_token_budget": 8_192,
        "capability": caps,
    }
    assert ResolvedCallPlan(**values).required_token_budget == 9_456
    with pytest.raises(ValidationError, match="required token budget"):
        ResolvedCallPlan(**{**values, "thinking_token_budget": 0})

    structured: dict[str, object] = {
        "base_url": caps.base_url,
        "model": caps.model,
        "prompt_token_budget": 0,
        "visible_token_budget": 1_024,
        "required_token_budget": 1_024 + 8_192,
        "request_token_budget": 1_024 + 8_192,
        "max_tokens_field": "max_tokens",
        "reasoning_requested": ReasoningEffort.OFF,
        "reasoning_effective": ReasoningEffort.OFF,
        "reasoning_dialect": ReasoningDialect.NONE,
        "stream": False,
        "thinking_token_budget": 8_192,
        "capability": caps,
    }
    assert StructuredCallPlan(**structured).required_token_budget == 9_216
    with pytest.raises(ValidationError, match="required token budget"):
        StructuredCallPlan(**{**structured, "thinking_token_budget": 0})


def test_the_ceiling_never_pushes_a_registered_route_past_its_output_limit() -> None:
    """顶值的唯一论证：顶 + 产品最大可见预算（抽取 8,192）在每一条已登记路由的输出上限之下。
    注册表加了一条更小的上限，这条会红——那时改的是顶，不是这条测试。"""
    from novel_harness.draft.capabilities import CAPABILITY_REGISTRY

    audited = 0
    for capability in CAPABILITY_REGISTRY.values():
        if capability.max_output_tokens is None:
            # 端点没公布上限的那几条（opencode）：plan 那道预检本来就不做，顶值推不出拒绝。
            continue
        audited += 1
        assert 8_192 + THINKING_BUDGET_MAX <= capability.max_output_tokens, capability.route
    assert audited >= 4  # 四家公布了上限的至少各一条，别让这条测试在一张全是 None 的表上假绿


def test_a_too_small_window_still_refuses_instead_of_clamping() -> None:
    """作者把顶值填给一个小窗口的模型：照旧当场拒（不 clamp），后果摆在屏幕上。"""
    tiny = _caps(max_context_tokens=16_000, max_output_tokens=16_000)
    with pytest.raises(CapabilityError, match="exceeds"):
        plan_call(
            SUMMARY_LENGTH, ReasoningEffort.OFF, tiny, thinking_token_budget=THINKING_BUDGET_MAX
        )


# ── ② 线上的形状 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("dialect", "off_wire"),
    [
        (ReasoningDialect.NONE, {}),
        (ReasoningDialect.OPENAI, {"reasoning_effort": "none"}),
        (
            ReasoningDialect.OPENROUTER,
            {"extra_body": {"reasoning": {"effort": "none", "exclude": True}}},
        ),
        (ReasoningDialect.DEEPSEEK, {"extra_body": {"thinking": {"type": "disabled"}}}),
        (ReasoningDialect.ANTHROPIC_COMPAT, {}),
    ],
)
def test_allowed_thinking_sends_no_reasoning_field_and_the_bigger_budget(
    dialect: ReasoningDialect, off_wire: dict[str, object]
) -> None:
    """预留 = 0：照旧发「关」（每种方言各自的写法）。预留 > 0：**什么 reasoning 字段都不发**，
    端点按自己的默认来——「允许」不是「要求」；`max_tokens` 带上预留。"""
    caps = _caps(
        dialect,
        max_tokens_field=(
            "max_completion_tokens" if dialect is ReasoningDialect.OPENAI else "max_tokens"
        ),
    )
    config = ProviderConfig(model=caps.model, base_url=caps.base_url)
    reasoning_keys = {"reasoning_effort", "extra_body"}

    closed = plan_call(SUMMARY_LENGTH, ReasoningEffort.OFF, caps)
    wire = _wire_kwargs(config, closed, MESSAGES)
    assert {key: wire[key] for key in reasoning_keys & wire.keys()} == off_wire
    assert wire[caps.max_tokens_field] == 1_264

    allowed = plan_call(SUMMARY_LENGTH, ReasoningEffort.OFF, caps, thinking_token_budget=8_192)
    wire = _wire_kwargs(config, allowed, MESSAGES)
    assert not (reasoning_keys & wire.keys())
    assert wire[caps.max_tokens_field] == 1_264 + 8_192


def test_the_authors_real_route_is_really_off_by_default_and_gets_room_when_allowed() -> None:
    """真书那条路由（opencode Go + `deepseek-v4.1-flash`，作者手填了窗口）。

    2026-09-13 上半天它没登记：`off` 在线上什么都不发，端点默认思考，1,264 被吃空。
    同日探针查证它认 DeepSeek 方言（ADR 0052 补记那张表）之后登记进了注册表，于是：
    - 开关关着（默认）→ `thinking: {"type": "disabled"}`，1,264——**默认真的关了**；
      真书第 670 章（当天失败两次的那一章）走这条路 4.7 秒回来 99 个 token、113 个字；
    - 开关开着 → 什么都不发、9,456——端点默认思考，预算给它留出来了。
    作者手填的窗口照旧压在上面（登记的两个上限是 `None`：端点没公布就不编）。"""
    from novel_harness.draft.windows import capabilities_from_author

    registered = resolve_capabilities("https://opencode.ai/zen/go/v1", "deepseek-v4.1-flash")
    assert registered.source == "registry:opencode-go-deepseek-v4.1-flash"
    assert registered.reasoning_dialect is ReasoningDialect.DEEPSEEK
    assert registered.max_context_tokens is None and registered.max_output_tokens is None
    override = capabilities_from_author(registered, 1_000_000)
    assert override is not None
    caps = resolve_capabilities(
        "https://opencode.ai/zen/go/v1", "deepseek-v4.1-flash", operator_override=override
    )
    assert caps.reasoning_dialect is ReasoningDialect.DEEPSEEK
    assert caps.max_context_tokens == 1_000_000
    config = ProviderConfig(model=caps.model, base_url=caps.base_url)

    closed = plan_call(SUMMARY_LENGTH, ReasoningEffort.OFF, caps)
    wire = _wire_kwargs(config, closed, MESSAGES)
    assert wire["max_tokens"] == 1_264
    assert wire["extra_body"] == {"thinking": {"type": "disabled"}}

    allowed = plan_call(
        SUMMARY_LENGTH, ReasoningEffort.OFF, caps, thinking_token_budget=THINKING_BUDGET_MIN
    )
    wire = _wire_kwargs(config, allowed, MESSAGES)
    assert wire["max_tokens"] == 9_456
    assert "extra_body" not in wire and "reasoning_effort" not in wire


# ── ③ 装配层那一处换算 ────────────────────────────────────────────────────


def _settings_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **fields: object) -> None:
    import json

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"base_url": LOCAL, "model": "m", "api_key": "k", **fields}))
    monkeypatch.setenv("NH_SETTINGS_PATH", str(path))


def test_the_switch_off_means_zero_whatever_the_number_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novel_harness.api.deps import author_thinking_budget

    _settings_at(tmp_path, monkeypatch, allow_thinking=False, thinking_budget=16_384)
    assert author_thinking_budget() == 0


def test_the_switch_on_without_a_number_means_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novel_harness.api.deps import author_thinking_budget

    _settings_at(tmp_path, monkeypatch, allow_thinking=True)
    assert author_thinking_budget() == THINKING_BUDGET_MIN


def test_the_switch_on_with_a_number_means_that_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novel_harness.api.deps import author_thinking_budget

    _settings_at(tmp_path, monkeypatch, allow_thinking=True, thinking_budget=16_384)
    assert author_thinking_budget() == 16_384


def test_a_fresh_install_does_not_think(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """默认不管什么模型都不开思考——包括根本没有设置文件的那一天。"""
    from novel_harness.api.deps import author_thinking_budget

    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "missing.json"))
    assert author_thinking_budget() == 0


# ── ④ 七处调用都从那一处拿 ────────────────────────────────────────────────

def _plan_calls(path: Path) -> list[tuple[int, set[str]]]:
    """文件里每一处 `plan_call(...)` / `plan_structured_call(...)` 的（行号，关键字实参名）。

    走 AST 不走正则：模块 docstring 里也写着 `plan_call(..., interruptible=True)` 之类的话，
    正则会把那句当成一处调用。
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in {"plan_call", "plan_structured_call"}:
            out.append((node.lineno, {kw.arg for kw in node.keywords if kw.arg}))
    return out


def test_every_product_plan_carries_the_authors_budget() -> None:
    """产品里算 plan 的每一处都必须传 `thinking_token_budget=`——漏一处的症状是
    「开关拨开了，一半的功能听、一半不听」，而不听的那一半屏幕上只是「总结还是红的」。

    `draft/` 自己那两份定义不算；`agent/` 那两处收的是装配层递进来的数（同能力证据）。
    """
    offenders: list[str] = []
    seen = 0
    for path in sorted((SRC / "api").rglob("*.py")) + sorted((SRC / "agent").rglob("*.py")):
        for line, keywords in _plan_calls(path):
            seen += 1
            if "thinking_token_budget" not in keywords:
                offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert not offenders, "这些 plan 没带作者的思考预算：\n" + "\n".join(offenders)
    # 七处：抽取 / 总结 / 事后核对（deps）、行内续写（app）、对话摘要 + 回话（chat）、
    # 起草台两处（agent/drafting）+ 回话那份定义（agent/model）。少了说明有一处改走了别的路。
    assert seen == 8, seen


def test_the_assembly_layer_is_the_only_place_that_turns_settings_into_a_budget() -> None:
    """换算只写在 `api/deps.py::author_thinking_budget`。别处再读 `allow_thinking` /
    `thinking_budget` 去算数，就是第二份会漂的口径。"""
    readers: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name in {"deps.py", "settings.py", "app.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\.allow_thinking\b|\.thinking_budget\b", text):
            readers.append(str(path.relative_to(ROOT)))
    assert not readers, "这些文件自己读了思考设置：\n" + "\n".join(readers)
