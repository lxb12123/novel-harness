"""缓存命中量：**一次测量的接线，从响应一路到日志页那一行。**

供应商的响应里一直带着「这次有多少输入 token 是缓存命中的」，而 `draft/provider.py`
在这一版之前只读 `prompt_tokens` / `completion_tokens` —— 那个数一直被扔掉。
这份文件钉住的是那条线不许再断，以及**它只是一条读取线**。

四件事，各对应一种真的会发生的坏法：

1. **三家三个形状，认的是响应里有哪个字段**，不是「我配的是哪个供应商」——
   后者一换中转（OpenRouter 转 Anthropic）就错。
2. **流式和非流式是两个函数**，只补一边的话，长稿（>16k 预算走流式）会永远说
   「不知道」——而长稿恰恰是最该看缓存的那一档。
3. **「不知道」和「0」必须分得开**：端点不报 ⇒ `None`；报了 0 ⇒ 那是真的一次都没命中。
   这两档指向两个**相反**的动作（去查端点支不支持 / 去查前缀被谁弄脏了），
   糊成一个数不是显示得难看，是把人指向错误的一件事。
4. **发出去的东西一个字节都没变。** DeepSeek 的前缀缓存全自动，发送侧不需要标任何东西；
   这一刀真要是碰了请求，M2 判分链和 `tests/test_draft_boundary.py` 靠的
   「wire shape 逐字节稳定」就没了。
"""

from __future__ import annotations

import sqlite3
import types
from pathlib import Path
from typing import Any

import pytest

from novel_harness import activity
from novel_harness.db import connect, migrate
from novel_harness.draft.capabilities import (
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    plan_call,
)
from novel_harness.draft.length import LengthSpec
from novel_harness.draft.provider import (
    CacheShape,
    CacheUsage,
    CompletionResult,
    ProviderConfig,
    _cache_usage,
    _wire_kwargs,
    complete,
)
from novel_harness.extract.call_audit import ModelCallReceipt, record_call
from novel_harness.extract.control import AuditedCompletion
from novel_harness.ids import EntityType, new_id, new_project_id

LOCAL = "http://localhost:11434/v1"
SHORT = LengthSpec(language="zh", min_units=800, target_units=1_000, max_units=1_200)
LONG = LengthSpec(language="zh", min_units=8_000, target_units=9_000, max_units=9_000)


def _capability(*, streaming: bool = True) -> ProviderCapabilities:
    return ProviderCapabilities(
        base_url=LOCAL,
        model="qwen2.5",
        source="test:cache-usage",
        source_urls=("https://example.invalid/docs",),
        max_context_tokens=200_000,
        max_output_tokens=100_000,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({ReasoningEffort.OFF}),
        reasoning_dialect=ReasoningDialect.NONE,
        reasoning_shares_output=False,
    )


def _client(usage: Any, *, stream: bool = False) -> Any:
    """一个只回一句话的假端点，`usage` 由调用方自己捏成某一家的形状。"""
    seen: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        seen.update(kwargs)
        if stream:
            return iter(
                [
                    types.SimpleNamespace(
                        model="fake",
                        choices=[
                            types.SimpleNamespace(
                                delta=types.SimpleNamespace(content="正文", tool_calls=None),
                                finish_reason="stop",
                            )
                        ],
                        usage=None,
                    ),
                    types.SimpleNamespace(model="fake", choices=[], usage=usage),
                ]
            )
        return types.SimpleNamespace(
            model="fake",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="正文", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=usage,
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    client.seen = seen  # type: ignore[attr-defined]
    return client


def _run(usage: Any, *, stream: bool) -> tuple[CompletionResult, dict[str, Any]]:
    capability = _capability()
    plan = plan_call(LONG if stream else SHORT, ReasoningEffort.OFF, capability)
    assert plan.stream is stream, "这条用例挑的长度必须真的落在想验的那条路上"
    client = _client(usage, stream=stream)
    result = complete(
        [{"role": "user", "content": "写第 12 章"}],
        config=ProviderConfig(model=plan.model, base_url=plan.base_url),
        plan=plan,
        client=client,
    )
    return result, client.seen  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════════
# 一、三家三个形状
# ══════════════════════════════════════════════════════════════════════════

DEEPSEEK_USAGE = types.SimpleNamespace(
    prompt_tokens=1_200,
    completion_tokens=400,
    prompt_cache_hit_tokens=960,
    prompt_cache_miss_tokens=240,
)
OPENAI_USAGE = types.SimpleNamespace(
    prompt_tokens=1_200,
    completion_tokens=400,
    prompt_tokens_details=types.SimpleNamespace(cached_tokens=768),
)
ANTHROPIC_USAGE = types.SimpleNamespace(
    prompt_tokens=1_200,
    completion_tokens=400,
    cache_read_input_tokens=1_024,
    cache_creation_input_tokens=176,
)

SHAPES = [
    pytest.param(DEEPSEEK_USAGE, CacheShape.DEEPSEEK, 960, None, id="deepseek"),
    pytest.param(OPENAI_USAGE, CacheShape.OPENAI, 768, None, id="openai"),
    pytest.param(ANTHROPIC_USAGE, CacheShape.ANTHROPIC, 1_024, 176, id="anthropic"),
]


@pytest.mark.parametrize(("usage", "shape", "read", "written"), SHAPES)
@pytest.mark.parametrize("stream", [False, True], ids=["non-streaming", "streaming"])
def test_each_vendor_shape_normalizes_to_one_outparam(
    usage: Any, shape: CacheShape, read: int, written: int | None, stream: bool
) -> None:
    """三家的字段名不同，**出参只有一份**；两条路都要读。

    第二个参数化就是「漏一边」那条：`_from_stream` 和 `_from_non_streaming` 是两个
    函数，各读各的，只补一边的后果是长稿永远说「不知道」。
    """
    result, _ = _run(usage, stream=stream)
    assert result.cache == CacheUsage(shape=shape, read_tokens=read, written_tokens=written)


def test_the_deepseek_miss_count_is_not_the_write_count() -> None:
    """`prompt_cache_miss_tokens` **不是**「写了多少」，别顺手往那个槽里映。

    它 = `prompt_tokens - prompt_cache_hit_tokens`，是个派生量。映过去的话 DeepSeek
    看起来每次都在写缓存，而那恰好污染「恒为 0 ⇒ 去查端点支不支持」那条判读。
    """
    result, _ = _run(DEEPSEEK_USAGE, stream=False)
    assert result.cache is not None
    assert result.cache.written_tokens is None
    # 自守卫：那个字段真的在样本里，否则这条断言搜的是空气。
    assert DEEPSEEK_USAGE.prompt_cache_miss_tokens == 240


def test_the_shape_is_read_off_the_response_not_off_the_configured_route() -> None:
    """判据是「响应里有哪个字段」。中转能转任何一家，配置说了不算。

    这里配的是一个本地 Ollama 路由，而响应回的是 Anthropic 的写法——认出来的必须是后者。
    """
    result, _ = _run(ANTHROPIC_USAGE, stream=False)
    assert result.cache is not None and result.cache.shape is CacheShape.ANTHROPIC


# ══════════════════════════════════════════════════════════════════════════
# 二、「不知道」和「0」
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("stream", [False, True], ids=["non-streaming", "streaming"])
def test_an_endpoint_that_never_mentions_caching_reports_nothing_not_zero(stream: bool) -> None:
    """端点不报 ⇒ 整个 `cache` 是 `None`。**不是 0** —— 0 会被读成「一次都没命中」。"""
    result, _ = _run(
        types.SimpleNamespace(prompt_tokens=1_200, completion_tokens=400), stream=stream
    )
    assert result.cache is None


def test_a_reported_zero_is_a_real_zero() -> None:
    """报了 0 就是真的 0：这一档存在，才谈得上「恒为 0 ⇒ 去查怎么开启」。"""
    result, _ = _run(
        types.SimpleNamespace(
            prompt_tokens=1_200, completion_tokens=400, prompt_cache_hit_tokens=0
        ),
        stream=False,
    )
    assert result.cache == CacheUsage(shape=CacheShape.DEEPSEEK, read_tokens=0, written_tokens=None)


@pytest.mark.parametrize(
    "value",
    ["960", True, -1, 12.5, None, object()],
    ids=["string", "bool", "negative", "float", "null", "object"],
)
def test_a_value_we_cannot_read_is_not_a_measurement(value: Any) -> None:
    """读不懂的值 ⇒ 当作没报（`None`），**而且绝不抛**。

    一次已经生成完、已经花过钱的调用，不该因为 usage 里多了个怪值就变成 `ProviderError`——
    那是拿一笔真花掉的钱换一条日志。
    """
    result, _ = _run(
        types.SimpleNamespace(
            prompt_tokens=1_200, completion_tokens=400, prompt_cache_hit_tokens=value
        ),
        stream=False,
    )
    assert result.text == "正文"  # 生成本身照旧成立
    assert result.cache is None


def test_a_usage_object_that_is_not_there_at_all_is_survivable() -> None:
    result, _ = _run(None, stream=False)
    assert result.cache is None and result.prompt_tokens is None


def test_a_later_empty_usage_chunk_does_not_erase_what_was_already_read() -> None:
    """流式下用「认出的最后一次」，不是「最后一次」。

    有的端点每个 chunk 都挂一个 usage，只有其中一个填满——照「最后一次」取会把已经
    读到的数抹成 `None`，而那个 `None` 在日志页上写作「未记录」，一条骗人的诚实。
    """
    from novel_harness.draft.provider import _from_stream

    chunks = [
        types.SimpleNamespace(
            model="fake",
            choices=[
                types.SimpleNamespace(
                    delta=types.SimpleNamespace(content="正文", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=DEEPSEEK_USAGE,
        ),
        types.SimpleNamespace(
            model="fake",
            choices=[],
            usage=types.SimpleNamespace(prompt_tokens=1_200, completion_tokens=400),
        ),
    ]
    result = _from_stream(iter(chunks), "fallback")
    assert result.cache is not None and result.cache.read_tokens == 960


def test_recognition_needs_only_one_readable_number() -> None:
    """字段在、但值读不懂 ⇒ 那一家不算认出来，继续试下一家。"""
    mixed = types.SimpleNamespace(
        prompt_cache_hit_tokens="坏了",
        cache_read_input_tokens=512,
        cache_creation_input_tokens=64,
    )
    assert _cache_usage(mixed) == CacheUsage(
        shape=CacheShape.ANTHROPIC, read_tokens=512, written_tokens=64
    )


# ══════════════════════════════════════════════════════════════════════════
# 三、发出去的东西一个字节都没变
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("stream", [False, True], ids=["non-streaming", "streaming"])
@pytest.mark.parametrize(
    "usage",
    [None, DEEPSEEK_USAGE, OPENAI_USAGE, ANTHROPIC_USAGE],
    ids=["silent", "deepseek", "openai", "anthropic"],
)
def test_reading_the_cache_numbers_changes_nothing_about_the_request(
    usage: Any, stream: bool
) -> None:
    """**这一刀只读响应。** 发出去的 kwargs 必须和 `_wire_kwargs` 逐键相同，
    而且不管对面回的是哪一家的形状（回什么都不该反过来影响发什么）。

    DeepSeek 的前缀缓存是全自动的，发送侧一个字都不用改；哪天有人想「显式标记一下」，
    这条会红——那时该先开一份 ADR，而不是顺手往 kwargs 里塞一个键。
    """
    capability = _capability()
    plan = plan_call(LONG if stream else SHORT, ReasoningEffort.OFF, capability)
    config = ProviderConfig(model=plan.model, base_url=plan.base_url)
    messages = [{"role": "user", "content": "写第 12 章"}]
    expected = _wire_kwargs(config, plan, messages)

    client = _client(usage, stream=stream)
    complete(messages, config=config, plan=plan, client=client)
    assert client.seen == expected  # type: ignore[attr-defined]

    sent = " ".join(str(key) for key in client.seen)  # type: ignore[attr-defined]
    assert "cache" not in sent and "cach" not in sent


# ══════════════════════════════════════════════════════════════════════════
# 四、一路接到日志页那一行
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def book(tmp_path: Path) -> Any:
    conn = connect(tmp_path / "nh.db")
    migrate(conn)
    pid = new_project_id()
    conn.execute(
        "INSERT INTO project (id, name, root_path) VALUES (?, ?, ?)", (pid, "青云", str(tmp_path))
    )
    conn.commit()
    yield conn, pid
    conn.close()


def _bill(conn: sqlite3.Connection, pid: str, receipt: ModelCallReceipt) -> str:
    call_id = record_call(
        conn,
        project_id=pid,
        capability=receipt.capability,
        model=receipt.model,
        finish_reason=receipt.finish_reason,
        schema_version=receipt.schema_version,
        prompt_hash=receipt.prompt_hash,
        prompt_bytes=receipt.prompt_bytes,
        text=receipt.text,
        prompt_tokens=receipt.prompt_tokens,
        completion_tokens=receipt.completion_tokens,
        cache_read_tokens=receipt.cache_read_tokens,
        cache_write_tokens=receipt.cache_write_tokens,
        elapsed_ms=receipt.elapsed_ms,
        call_id_factory=lambda project_id: new_id(EntityType.CALL, project_id),
    )
    conn.commit()
    return call_id


def _receipt(**over: Any) -> ModelCallReceipt:
    base: dict[str, Any] = {
        "capability": "writer",
        "schema_version": "nh.draft.v1",
        "model": "deepseek-v4-pro",
        "finish_reason": "stop",
        "prompt_hash": "ph",
        "prompt_bytes": b"prompt",
        "text": "正文",
        "prompt_tokens": 1_200,
        "completion_tokens": 400,
        "elapsed_ms": 900,
    }
    base.update(over)
    return ModelCallReceipt(**base)


def test_the_ledger_copies_the_number_the_provider_reported(book: Any) -> None:
    """账本只照抄。`record_call` 是全库唯一写入口，这里验它真的把两个数落进了那两列。"""
    conn, pid = book
    call_id = _bill(conn, pid, _receipt(cache_read_tokens=960, cache_write_tokens=None))
    row = conn.execute(
        "SELECT cache_read_tokens, cache_write_tokens FROM model_call WHERE id = ?", (call_id,)
    ).fetchone()
    assert tuple(row) == (960, None)


def test_the_audited_copy_flattens_the_normalized_outparam() -> None:
    """抽取那一路走 `AuditedCompletion`，它把 `CacheUsage` 拍成两个纯量。

    **少这一步的话，抽取和章节总结两条路会静默地永远不报**——它们不经过
    `ModelCallReceipt`，`record_call` 那两个必填参数只能从这儿来。
    """
    audited = AuditedCompletion.from_result(
        CompletionResult(
            text="x",
            model="m",
            cache=CacheUsage(shape=CacheShape.ANTHROPIC, read_tokens=1_024, written_tokens=176),
        )
    )
    assert (audited.cache_read_tokens, audited.cache_write_tokens) == (1_024, 176)

    silent = AuditedCompletion.from_result(CompletionResult(text="x", model="m"))
    assert (silent.cache_read_tokens, silent.cache_write_tokens) == (None, None)


@pytest.mark.parametrize(
    ("read", "written", "expected"),
    [
        (None, None, "未记录"),
        (0, None, "这次没接上，整段输入都重新算了"),
        (960, None, "960 token 接着上次，没有重新算"),
        (1_024, 176, "1024 token 接着上次，没有重新算 · 另存下 176 token 供下次接"),
        (1_024, 0, "1024 token 接着上次，没有重新算 · 这次没有新存下内容"),
    ],
    ids=["unreported", "reported-zero", "hit", "hit-and-write", "hit-no-write"],
)
def test_the_log_page_says_which_of_the_three_it_is(
    book: Any, read: int | None, written: int | None, expected: str
) -> None:
    """展开层那一行：**三档必须分得开**（§10 约束 8：零要带着理由一起出现）。

    「未记录」和「这次没接上」指向两个相反的动作，把前者渲染成 0 不是显示得难看一点，
    是把作者指向错误的一件事。
    """
    conn, pid = book
    call_id = _bill(conn, pid, _receipt(cache_read_tokens=read, cache_write_tokens=written))
    detail = activity.read_entry(conn, pid, call_id)
    assert detail is not None
    values = {row.label: row.value for row in detail.rows}
    assert values["接着上次的输入"] == expected


def test_the_log_page_keeps_saying_it_for_rows_written_before_the_columns_existed(
    book: Any,
) -> None:
    """这两列出现之前记下的那些行是 `NULL` —— 它们必须读作「未记录」，不是「没命中」。

    迁移是 `ALTER TABLE ADD COLUMN` 且**没有 DEFAULT 0**，这条钉的就是那个「没有」。
    """
    conn, pid = book
    call_id = new_id(EntityType.CALL, pid)
    conn.execute(
        """
        INSERT INTO model_call (id, project_id, capability, model, prompt_hash, tokens_in)
        VALUES (?, ?, 'extractor', 'deepseek-v4-pro', 'ph', 1200)
        """,
        (call_id, pid),
    )
    conn.commit()
    detail = activity.read_entry(conn, pid, call_id)
    assert detail is not None
    values = {row.label: row.value for row in detail.rows}
    assert values["接着上次的输入"] == "未记录"


def test_the_screen_never_says_cache_hit_in_engineering_terms(book: Any) -> None:
    """屏幕上说的是**结果**（省下的那部分不用重新算），不是缓存这个机制。

    判据文件在 `frontend/src/test/screenGuard.ts` / `tests/test_wording_guard.py`，
    这里只钉这一行自己：不许出现 snake_case、不许出现引擎枚举、不许英文长句。
    """
    conn, pid = book
    call_id = _bill(conn, pid, _receipt(cache_read_tokens=960, cache_write_tokens=176))
    detail = activity.read_entry(conn, pid, call_id)
    assert detail is not None
    line = " ".join(f"{row.label} {row.value}" for row in detail.rows)
    assert "cache" not in line.lower()
    assert "_" not in line
