"""对抗性验证：缓存命中量这条测量线，到底立不立得住。

**这份文件不复核「代码写了什么」，它复核「哪种坏法今天真的会发生」。** 六条网各罩一种：

1. **「不知道」和「0」分得开** —— 端点不报 ⇒ `None`；报了 0 ⇒ 真的一次都没命中。
   两档指向两个**相反**的动作（去查端点支不支持 / 去查前缀被谁弄脏了），
   而这个仓库为「把两者糊成 0」栽过四次。三处（出参 / 账上 / 日志页）逐处验，
   **配一个把「认不出」改成 0 的探针**：网抓不住它就说明网是摆设。
2. **流式和非流式是两个函数** —— 只补一边的话，长稿（>16k 预算走流式）永远说「不知道」。
3. **发出去的东西一个字节没变** —— 判据是「客户端真正收到的 kwargs」，不是「测试绿了」。
   一份跨形状的金指纹钉住它，**探针往 wire 上加一个字段就必须变色**。
4. **账本只照抄** —— `_estimate_tokens` 是闸门的东西，它渗进这两列就是编数。
5. **三家形状认得对，而且只认一遍** —— 用 **openai SDK 自己**解析出来的 usage 对象，
   不是手捏的 `SimpleNamespace`：真实响应先过 SDK 的 pydantic 层，
   手捏的替身在「嵌套字段是对象还是 dict」这件事上和真的不一样。
6. **屏幕** —— 三档都过同一份形状判据（`tests/test_wording_guard.py::dev_shapes`），
   **不许在这儿抄第二份**。

判据来源全部是既有的那一份：屏幕用 `test_wording_guard`，夹具用 `test_activity::seed_call`。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.completion_usage import CompletionUsage

from test_activity import seed_call
from test_wording_guard import dev_shapes

from novel_harness import activity
from novel_harness.db import connect, migrate
from novel_harness.draft import capabilities as caps
from novel_harness.draft import provider as prov
from novel_harness.draft.length import LengthSpec
from novel_harness.extract.call_audit import ModelCallReceipt, record_call
from novel_harness.extract.control import AuditedCompletion
from novel_harness.ids import EntityType, new_id

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "novel_harness"

LOCAL = "http://localhost:11434/v1"


# ══════════════════════════════════════════════════════════════════════════
# 器材 —— 响应按**供应商真的会发的 JSON** 捏，然后交给 openai SDK 自己解析
# ══════════════════════════════════════════════════════════════════════════

#: 三家的 `usage` 原文。字段名来自各家文档/实测，**这份表是样本不是判据**——
#: 判据在 `provider.py::_CACHE_SHAPES`，两边对不上时红的是下面那条形状测试。
VENDOR_USAGE: dict[str, dict[str, Any]] = {
    "deepseek": {
        "prompt_tokens": 1_200,
        "completion_tokens": 400,
        "total_tokens": 1_600,
        "prompt_cache_hit_tokens": 960,
        "prompt_cache_miss_tokens": 240,
    },
    "openai": {
        "prompt_tokens": 1_200,
        "completion_tokens": 400,
        "total_tokens": 1_600,
        "prompt_tokens_details": {"cached_tokens": 960, "audio_tokens": 0},
    },
    "anthropic": {
        "prompt_tokens": 1_200,
        "completion_tokens": 400,
        "total_tokens": 1_600,
        "cache_read_input_tokens": 1_024,
        "cache_creation_input_tokens": 176,
    },
    #: 什么都不报的那一档（本地 llama.cpp / 旧中转的常态）。
    "silent": {"prompt_tokens": 1_200, "completion_tokens": 400, "total_tokens": 1_600},
}


def _usage(name: str, **override: Any) -> CompletionUsage:
    """让 **openai SDK 自己**把一段供应商 JSON 解析成 usage 对象。

    真实链路上这一步一定发生，而它决定了嵌套字段到底是对象还是 dict ——
    手捏 `SimpleNamespace` 的替身在这件事上比真的宽，
    **假实现比真实现宽的时候，测试绿而产品错**（`test_agent_index::FakeEvents` 那一课）。
    """
    raw = dict(VENDOR_USAGE[name])
    raw.update(override)
    return CompletionUsage.model_validate(raw)


def _completion(usage: CompletionUsage | None, *, text: str = "他推门进去。") -> ChatCompletion:
    body: dict[str, Any] = {
        "id": "chatcmpl-probe",
        "object": "chat.completion",
        "created": 0,
        "model": "qwen2.5",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage.model_dump()
    return ChatCompletion.model_validate(body)


def _chunks(usages: list[CompletionUsage | None], *, text: str = "他推门进去。") -> list[Any]:
    """一串流式 chunk。最后一片按 OpenAI 的约定只带 usage、不带 choices。"""
    out: list[Any] = []
    for i, piece in enumerate(text):
        out.append(
            ChatCompletionChunk.model_validate(
                {
                    "id": "chatcmpl-probe",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "qwen2.5",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": piece},
                            "finish_reason": "stop" if i == len(text) - 1 else None,
                        }
                    ],
                }
            )
        )
    for usage in usages:
        body: dict[str, Any] = {
            "id": "chatcmpl-probe",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "qwen2.5",
            "choices": [],
        }
        if usage is not None:
            body["usage"] = usage.model_dump()
        out.append(ChatCompletionChunk.model_validate(body))
    return out


class _Endpoint:
    """一个假端点：记下收到的 kwargs，回一份指定的响应。"""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.seen: list[dict[str, Any]] = []
        self.chat = self

    @property
    def completions(self) -> "_Endpoint":
        return self

    def create(self, **kwargs: Any) -> Any:
        self.seen.append(kwargs)
        return self.response


def _capability(*, streaming: bool = True) -> caps.ProviderCapabilities:
    return caps.ProviderCapabilities(
        base_url=LOCAL,
        model="qwen2.5",
        source="test:cache-metering",
        source_urls=("https://example.invalid/docs",),
        max_context_tokens=400_000,
        max_output_tokens=200_000,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({caps.ReasoningEffort.OFF}),
        reasoning_dialect=caps.ReasoningDialect.NONE,
        reasoning_shares_output=False,
        supports_streaming=streaming,
        supports_stream_usage=True if streaming else None,
    )


SHORT = LengthSpec(language="zh", min_units=800, target_units=1_000, max_units=1_200)
"""预算 3,424 < 16k ⇒ 非流式。"""

LONG = LengthSpec(language="zh", min_units=8_000, target_units=9_000, max_units=9_000)
"""预算 19,024 > 16k ⇒ 流式（`plan_call` 自己推的，不是这儿拧的开关）。"""


def _call(response: Any, *, length: LengthSpec = SHORT) -> tuple[prov.CompletionResult, _Endpoint]:
    config = prov.ProviderConfig(base_url=LOCAL, model="qwen2.5", api_key="k")
    plan = caps.plan_call(length, caps.ReasoningEffort.OFF, _capability())
    endpoint = _Endpoint(response)
    result = prov.complete(
        [{"role": "user", "content": "写第 89 章"}],
        config=config,
        plan=plan,
        client=endpoint,
    )
    return result, endpoint


# ══════════════════════════════════════════════════════════════════════════
# 1. 「不知道」和「0」分得开 —— 出参 / 账上 / 日志页三处
# ══════════════════════════════════════════════════════════════════════════

#: 三档 ×（这一档在三处各长什么样）。**这张表就是本文件的主张。**
THREE_STATES = ("silent", "zero", "hit")


def _result_for(state: str) -> prov.CompletionResult:
    if state == "silent":
        usage = _usage("silent")
    elif state == "zero":
        usage = _usage("deepseek", prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1_200)
    else:
        usage = _usage("deepseek")
    return _call(_completion(usage))[0]


def test_the_three_states_are_three_different_things_on_the_wire_out() -> None:
    """**出参**：没报 / 报了 0 / 报了数，是三个不同的值。"""
    silent, zero, hit = (_result_for(s) for s in THREE_STATES)

    assert silent.cache is None, "端点没报这件事 ⇒ 整份 None（不是一个读数为 0 的对象）"
    assert zero.cache is not None and zero.cache.read_tokens == 0, "报了 0 ⇒ 那是真的 0"
    assert hit.cache is not None and hit.cache.read_tokens == 960

    seen = {None if r.cache is None else r.cache.read_tokens for r in (silent, zero, hit)}
    assert seen == {None, 0, 960}, f"三档在出参上塌成了 {seen}"


@pytest.fixture
def ledger_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(tmp_path / "cache.db")
    migrate(conn)
    from novel_harness import project

    project.create(conn, name="青云记", root_path=str(tmp_path / "book"))
    yield conn
    conn.close()


def _pid(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT id FROM project").fetchone()[0])


def _bill(conn: sqlite3.Connection, result: prov.CompletionResult) -> str:
    """走**真的**那条路：结果 → 审计拷贝 → `record_call`（全库唯一写入口）。"""
    audited = AuditedCompletion.from_result(result)
    pid = _pid(conn)
    call_id = record_call(
        conn,
        project_id=pid,
        capability="extractor",
        schema_version="v1",
        model=audited.model,
        finish_reason=audited.finish_reason,
        prompt_hash="ph",
        prompt_bytes=b"prompt",
        text=audited.text,
        prompt_tokens=audited.prompt_tokens,
        completion_tokens=audited.completion_tokens,
        cache_read_tokens=audited.cache_read_tokens,
        cache_write_tokens=audited.cache_write_tokens,
        elapsed_ms=900,
        call_id_factory=lambda p: new_id(EntityType.CALL, p),
    )
    conn.commit()
    return call_id


def test_the_three_states_are_three_different_things_on_the_bill(
    ledger_db: sqlite3.Connection,
) -> None:
    """**账上**：NULL / 0 / 960，一个都没被折成另一个。"""
    landed: dict[str, Any] = {}
    for state in THREE_STATES:
        call_id = _bill(ledger_db, _result_for(state))
        row = ledger_db.execute(
            "SELECT cache_read_tokens FROM model_call WHERE id = ?", (call_id,)
        ).fetchone()
        landed[state] = row[0]

    assert landed["silent"] is None, f"没报的那一档落库成了 {landed['silent']!r}"
    assert landed["zero"] == 0
    assert landed["hit"] == 960
    assert len({repr(v) for v in landed.values()}) == 3, f"三档在账上塌成了 {landed}"


def _detail_row(conn: sqlite3.Connection, call_id: str, label: str) -> str:
    detail = activity.read_entry(conn, _pid(conn), call_id)
    assert detail is not None
    for row in detail.rows:
        if row.label == label:
            return row.value
    raise AssertionError(f"日志页展开层里没有「{label}」这一行：{[r.label for r in detail.rows]}")


CACHE_ROW_LABEL = "接着上次的输入"


def test_the_three_states_are_three_different_sentences_on_the_screen(
    ledger_db: sqlite3.Connection,
) -> None:
    """**日志页**：三档三句话，而且「0」那句必须带着理由（约束 8）。"""
    said: dict[str, str] = {}
    for state in THREE_STATES:
        call_id = _bill(ledger_db, _result_for(state))
        said[state] = _detail_row(ledger_db, call_id, CACHE_ROW_LABEL)

    assert len(set(said.values())) == 3, f"三档在屏幕上塌成了 {said}"
    assert said["silent"] == "未记录"
    assert "0" not in said["zero"], "报了 0 的那一档不该把裸 0 摆上屏（零要带着理由）"
    assert said["zero"] != said["silent"]
    assert "960" in said["hit"]


# ── 自守卫：把「认不出 ⇒ None」改成「认不出 ⇒ 0」，上面三条必须当场红 ──────────


@pytest.mark.parametrize(
    "which",
    ["out", "bill", "screen"],
)
def test_a_probe_that_folds_unknown_into_zero_is_caught_at_all_three_places(
    which: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**探针**：让认不出的形状返回 `read_tokens=0`（那正是本仓栽过四次的那一刀）。

    三处各自都必须自己抓得住 —— 只有一处抓得住的话，删掉那一处网就没了。
    """
    real = prov._cache_usage

    def folded(usage: Any) -> prov.CacheUsage | None:
        got = real(usage)
        if got is None:
            return prov.CacheUsage(shape=prov.CacheShape.DEEPSEEK, read_tokens=0)
        return got

    monkeypatch.setattr(prov, "_cache_usage", folded)

    if which == "out":
        with pytest.raises(AssertionError):
            test_the_three_states_are_three_different_things_on_the_wire_out()
        return

    conn = connect(tmp_path / "probe.db")
    migrate(conn)
    from novel_harness import project

    project.create(conn, name="青云记", root_path=str(tmp_path / "book"))
    try:
        with pytest.raises(AssertionError):
            if which == "bill":
                test_the_three_states_are_three_different_things_on_the_bill(conn)
            else:
                test_the_three_states_are_three_different_sentences_on_the_screen(conn)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 2. 流式那条路 —— `_from_stream` 和 `_from_non_streaming` 是两个函数
# ══════════════════════════════════════════════════════════════════════════


def test_a_streamed_call_reads_the_cache_numbers_too() -> None:
    """长稿走流式，而长稿恰恰是最该看缓存的那一档。"""
    result, endpoint = _call(_chunks([_usage("deepseek")]), length=LONG)
    assert endpoint.seen[0]["stream"] is True, "这条路没走成流式，等于在验另一件事"
    assert result.cache is not None, "流式下读不到 ⇒ 长稿永远说「不知道」"
    assert result.cache.read_tokens == 960


def test_a_streamed_call_that_reports_nothing_still_says_it_does_not_know() -> None:
    result, _ = _call(_chunks([_usage("silent")]), length=LONG)
    assert result.cache is None


def test_a_late_empty_usage_chunk_does_not_erase_what_was_already_read() -> None:
    """有的端点每片都挂 usage，只有一片填满 —— 照「最后一次」取会把数抹掉。"""
    result, _ = _call(_chunks([_usage("deepseek"), _usage("silent")]), length=LONG)
    assert result.cache is not None and result.cache.read_tokens == 960


def test_the_streaming_half_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """**探针**：把流式那一半的读取掐掉，上面三条里至少有两条必须红。

    掐法是「让 `_from_stream` 看到的 usage 永远认不出」，等价于那一半没接线。
    """
    calls = {"n": 0}

    def only_non_streaming(usage: Any) -> prov.CacheUsage | None:
        calls["n"] += 1
        return None

    monkeypatch.setattr(prov, "_cache_usage", only_non_streaming)
    result, _ = _call(_chunks([_usage("deepseek")]), length=LONG)
    assert calls["n"] > 0, "流式那条路根本没调读取函数 —— 那一半从来没接过线"
    assert result.cache is None


def test_on_the_authors_own_provider_a_streamed_call_is_never_asked_to_report() -> None:
    """**接了线，不等于量得到。** 作者用的是 DeepSeek，而那两条路由在能力表上
    `supports_stream_usage` 是 `None`（没审计过），于是流式请求**不发**
    `stream_options={"include_usage": True}` —— OpenAI 兼容协议下不要就不给，
    对面一个 usage chunk 都不会送。

    ── 这条为什么值得钉住 ────────────────────────────────────────────────
    `_from_stream` 那段注释给的理由是「只补非流式那一条，长稿就会永远说『不知道』」。
    **在作者自己的端点上，长稿本来就永远说「不知道」**：不是因为那一半没接线，
    是因为我们从来没问。今天无害（产品的输出预算 7,024 远在 16,000 阈值之下，
    没有一条生产路径真的走流式）；**危险的是哪天有人为了写长章把预算抬过阈值**——
    屏幕上会多出一批「未记录」，而「未记录」在这一页上的意思是**端点没报**，
    读的人会去查「DeepSeek 支不支持缓存」，而真正该改的是这儿。

    `gpt-5.6` 那四条路由 `supports_stream_usage=True`，所以流式那一半在它们身上是活的
    —— 这条不是说那半白写了，是说**它在作者的端点上量不到**。

    这个 `None` 本身**不是 bug**：能力表对没审计过的东西一律 fail-closed，
    不许凭空声称支持（`ProviderCapabilities` 的既有立场）。所以处置是「说出来」，
    不是把它改成 `True`。哪天真去审计了、改成 `True` 了，这条会红 —— 那时删掉它。
    """
    streaming_but_unmeasured: list[str] = []
    for (base_url, model), capability in sorted(caps.CAPABILITY_REGISTRY.items()):
        if capability.supports_streaming is not True or capability.supports_stream_usage is True:
            continue
        try:
            plan = caps.plan_call(LONG, caps.ReasoningEffort.OFF, capability)
        except caps.CapabilityError:
            continue
        if not plan.stream:
            continue
        config = prov.ProviderConfig(base_url=base_url, model=model, api_key="k")
        wire = prov._wire_kwargs(config, plan, [{"role": "user", "content": "写第 89 章"}])
        assert "stream_options" not in wire
        streaming_but_unmeasured.append(model)

    assert any("deepseek" in m for m in streaming_but_unmeasured), (
        "作者用的那两条 DeepSeek 路由不再落在「能流式但不要 usage」这一档了 —— "
        "若是有人补了审计并改成 supports_stream_usage=True，这条测试和 `_from_stream` "
        "那段注释一起删掉/改掉；若是路由没了，同理。"
    )

    # 而这就是那一档在库里和屏幕上的样子：三个数一起「未记录」。
    result, _ = _call(_chunks([]), length=LONG)
    assert (result.prompt_tokens, result.completion_tokens, result.cache) == (None, None, None)


# ══════════════════════════════════════════════════════════════════════════
# 3. 请求侧零变化 —— 判据是客户端真正收到的那份 kwargs
# ══════════════════════════════════════════════════════════════════════════


def _wire_matrix() -> list[dict[str, Any]]:
    """一张跨形状的 wire 快照。

    **用合成 capability 而不是注册表**：注册表里加一个新模型是件正常的事，
    拿它当金指纹的输入等于给自己埋一颗假红的雷 —— 而假红的下场是守卫被关掉。
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "chapter_text",
                "description": "读一章正文",
                "parameters": {
                    "type": "object",
                    "properties": {"chapter": {"type": "integer"}},
                    "required": ["chapter"],
                },
            },
        }
    ]
    messages = [
        {"role": "system", "content": "你是中文长篇小说的写作助手。"},
        {"role": "user", "content": "第 89 章，萧决进城。"},
    ]
    out: list[dict[str, Any]] = []
    for length_name, length in (("short", SHORT), ("long", LONG)):
        for temperature in (None, 0.7):
            for tools_name, tool_list, choice in (
                ("none", None, None),
                ("tools", tools, None),
                ("tools+required", tools, "required"),
            ):
                config = prov.ProviderConfig(
                    base_url=LOCAL, model="qwen2.5", api_key="k", temperature=temperature
                )
                plan = caps.plan_call(length, caps.ReasoningEffort.OFF, _capability())
                endpoint = _Endpoint(
                    _chunks([_usage("deepseek")])
                    if plan.stream
                    else _completion(_usage("deepseek"))
                )
                prov.complete(
                    messages,
                    config=config,
                    plan=plan,
                    client=endpoint,
                    tools=tool_list,
                    tool_choice=choice,
                )
                out.append(
                    {
                        "shape": f"{length_name}/{temperature}/{tools_name}",
                        "wire": json.loads(json.dumps(endpoint.seen[0], sort_keys=True)),
                    }
                )
    return out


WIRE_FINGERPRINT = "92f9c5b0b6a80b9df3977799398d073f24f84ded5c521520022dec557f00b33b"
"""12 种请求形状下、客户端真正收到的那份 kwargs 的 sha256。

**这个数是跨树对拷出来的**：把 `git archive HEAD src`（缓存这一刀之前那棵树）解到别处、
同一份 `_wire_matrix()` 跑一遍，算出来是同一个值。它不是「从当前代码抄下来的」——
从当前代码抄一个数只能钉住「以后别再变」，钉不住「这次没变过」。
"""


def _fingerprint() -> str:
    blob = json.dumps(_wire_matrix(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def test_nothing_about_the_cache_reading_leaks_into_what_gets_sent() -> None:
    """发出去的 kwargs 里不许有任何一个和缓存有关的键。

    这条是给**将来**写的：哪天有人想「顺手显式标记一下缓存」（`prompt_cache_key`
    之类），它当场红 —— 而 M2 判分链押的正是「wire shape 逐字节不变」。
    """
    for entry in _wire_matrix():
        keys = " ".join(entry["wire"].keys()).lower()
        assert "cach" not in keys, f"{entry['shape']} 的 wire 上长出了缓存字段：{keys}"


def test_what_the_client_receives_is_exactly_what_wire_kwargs_builds() -> None:
    """`complete()` 不许在 `_wire_kwargs` 之外自己加一个字段。

    并且**对面回哪一家的形状，不许反过来影响发什么**：同一份请求，
    分别让端点回四种 usage，发出去的东西必须逐字节相同。
    """
    messages = [{"role": "user", "content": "写第 89 章"}]
    config = prov.ProviderConfig(base_url=LOCAL, model="qwen2.5", api_key="k")
    plan = caps.plan_call(SHORT, caps.ReasoningEffort.OFF, _capability())
    expected = prov._wire_kwargs(config, plan, messages)

    sent: list[str] = []
    for name in ("deepseek", "openai", "anthropic", "silent"):
        endpoint = _Endpoint(_completion(_usage(name)))
        prov.complete(messages, config=config, plan=plan, client=endpoint)
        assert endpoint.seen[0] == expected, f"回 {name} 的时候发出去的东西变了"
        sent.append(json.dumps(endpoint.seen[0], sort_keys=True, ensure_ascii=False))
    assert len(set(sent)) == 1, "对面回什么，反过来改了我们发什么"


def test_the_wire_fingerprint_is_pinned(request: pytest.FixtureRequest) -> None:
    """跨 12 种请求形状的金指纹。

    它和上面两条不是重复：那两条罩「有没有多一个键」，这条罩**任何一位的改动**
    （值、顺序、类型）。改 wire 是可以的，但必须是有人**明确决定**改，
    而不是读几个响应字段的副作用。
    """
    assert _fingerprint() == WIRE_FINGERPRINT, (
        "发出去的东西变了。若这是一次有意的 wire 改动，把新指纹填进 WIRE_FINGERPRINT；"
        "若不是 —— M2 判分链和 tests/test_draft_boundary.py 押的就是这一条。"
    )


def test_the_fingerprint_actually_catches_a_one_field_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**探针**：往 wire 上加一个字段，指纹必须变。不变的话上面那条是摆设。"""
    real = prov._wire_kwargs_from_validated

    def with_extra(*args: Any, **kwargs: Any) -> dict[str, Any]:
        out = real(*args, **kwargs)
        out["prompt_cache_key"] = "nh"
        return out

    monkeypatch.setattr(prov, "_wire_kwargs_from_validated", with_extra)
    assert _fingerprint() != WIRE_FINGERPRINT


# ══════════════════════════════════════════════════════════════════════════
# 4. 账本只照抄 —— 估算是闸门的东西，不许渗进这两列
# ══════════════════════════════════════════════════════════════════════════


def test_an_endpoint_that_reports_no_usage_lands_null_not_an_estimate(
    ledger_db: sqlite3.Connection,
) -> None:
    """端点一个数都不报时，两列都是 NULL —— 哪怕闸门那侧正在用估算顶着。"""
    result, _ = _call(_completion(None))
    assert result.prompt_tokens is None and result.cache is None
    call_id = _bill(ledger_db, result)
    row = ledger_db.execute(
        "SELECT tokens_in, cache_read_tokens, cache_write_tokens FROM model_call WHERE id = ?",
        (call_id,),
    ).fetchone()
    assert tuple(row) == (None, None, None), f"账上被编了数：{tuple(row)}"


def test_the_cost_gate_estimates_but_the_bill_still_says_it_does_not_know() -> None:
    """闸门用估算、账本照抄 —— 两个消费者两套规矩，这条钉住它们没有串线。"""
    loop = importlib.import_module("novel_harness.agent.loop")
    for node, _path in _receipt_constructions():
        for kw in node.keywords:
            if kw.arg in {"cache_read_tokens", "cache_write_tokens"}:
                assert "_estimate_tokens" not in ast.dump(kw.value), "估算渗进了账上的缓存列"
    assert loop._estimate_tokens("一二三四") > 0, "估算函数本身还在（它是闸门的东西）"


def _receipt_constructions() -> list[tuple[ast.Call, Path]]:
    found: list[tuple[ast.Call, Path]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ModelCallReceipt":
                found.append((node, path))
    return found


def test_every_receipt_producer_says_its_answer_out_loud() -> None:
    """**每一个**造回执的地方都必须显式给出这两个数，包括「我这条路不知道」。

    ── 这条为什么不是「和 `record_call` 那条重复」 ──────────────────────────
    `record_call` 的入参没有默认值，所以漏传是 `TypeError`；而 `ModelCallReceipt`
    的这两个字段**有默认值 `None`**，漏传是**静默降级** —— 那一条路从此永远说
    「未记录」，而屏幕上「未记录」的意思是「端点没报」，于是它开始骗人。

    实测：把 `draft/product_draft.py::_receipt` 里那两行删掉，**2173 条测试全绿**
    （包括 `tests/test_cache_usage.py`）。而那条路恰恰是作者花钱最多的一条
    （agent 起草，一轮最多六稿正文）。所以这条网罩的是**生产者**，不是写入口。
    """
    constructions = _receipt_constructions()
    assert len(constructions) >= 2, "回执生产者一个都没找到 —— 这条网扫的是一块空地"
    for node, path in constructions:
        keys = {kw.arg for kw in node.keywords}
        missing = {"cache_read_tokens", "cache_write_tokens"} - keys
        assert not missing, (
            f"{path.relative_to(SRC)} 造回执时漏了 {sorted(missing)} —— "
            "这两个字段有默认值，漏传不会报错，那条路会静默地永远说「未记录」"
        )


def test_the_drafting_producer_really_carries_the_numbers() -> None:
    """行为面：**产品档起草**那份回执上，两个数是从结果里照抄过来的。

    上面那条是结构网（挡新长出来的生产者），这条是它的行为对照：
    结构对了但抄错了字段（比如抄成 `prompt_tokens`）它才拦得住。
    """
    from novel_harness.draft.generate import DraftAttempt
    from novel_harness.draft.length import measure

    result, _ = _call(_completion(_usage("anthropic")))
    attempt = DraftAttempt(
        number=1,
        messages=[{"role": "user", "content": "写第 89 章"}],
        result=result,
        measurement=measure(result.text, SHORT),
    )
    receipt = _product_receipt(attempt)
    assert receipt.cache_read_tokens == 1_024
    assert receipt.cache_write_tokens == 176

    silent, _ = _call(_completion(_usage("silent")))
    quiet = _product_receipt(
        DraftAttempt(
            number=1,
            messages=[{"role": "user", "content": "写第 89 章"}],
            result=silent,
            measurement=measure(silent.text, SHORT),
        )
    )
    assert quiet.cache_read_tokens is None and quiet.cache_write_tokens is None


def _product_receipt(attempt: Any) -> ModelCallReceipt:
    from novel_harness.draft import product_draft

    return product_draft._receipt(attempt, 900)


def test_the_only_writer_demands_an_answer_from_every_caller() -> None:
    """`record_call` 的两个缓存参数**不许有默认值**。

    有默认值 = 一条新长出来的路径可以**静默**地不报，而不报和报 0 在这两列上
    是两个相反的结论。
    """
    import inspect

    params = inspect.signature(record_call).parameters
    for name in ("cache_read_tokens", "cache_write_tokens"):
        assert name in params, f"{name} 根本不是 record_call 的入参"
        assert params[name].default is inspect.Parameter.empty, (
            f"{name} 有默认值 —— 下一条路径会静默地不报"
        )


def test_the_database_refuses_a_negative_count(ledger_db: sqlite3.Connection) -> None:
    """CHECK 真的咬 —— 「加了个 CHECK」和「CHECK 生效」是两件事。"""
    pid = _pid(ledger_db)
    with pytest.raises(sqlite3.IntegrityError):
        ledger_db.execute(
            "INSERT INTO model_call (id, project_id, capability, model, prompt_hash, "
            "cache_read_tokens) VALUES (?, ?, 'extractor', 'm', 'ph', -1)",
            (new_id(EntityType.CALL, pid), pid),
        )


# ══════════════════════════════════════════════════════════════════════════
# 5. 三家形状 —— 认得对，而且全库只认这一遍
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("vendor", "shape", "read", "written"),
    [
        ("deepseek", prov.CacheShape.DEEPSEEK, 960, None),
        ("openai", prov.CacheShape.OPENAI, 960, None),
        ("anthropic", prov.CacheShape.ANTHROPIC, 1_024, 176),
    ],
)
def test_each_vendor_shape_normalizes_to_the_same_two_numbers(
    vendor: str, shape: prov.CacheShape, read: int, written: int | None
) -> None:
    result, _ = _call(_completion(_usage(vendor)))
    assert result.cache is not None, f"{vendor} 的写法没被认出来"
    assert result.cache.shape is shape
    assert result.cache.read_tokens == read
    assert result.cache.written_tokens == written


def test_the_deepseek_miss_count_is_never_mistaken_for_a_write() -> None:
    """`prompt_cache_miss_tokens` 是派生量，不是「写了多少」。

    映错的话 DeepSeek 会看起来**每次都在写缓存**，而那恰好把
    「恒为 0 ⇒ 去查怎么开启」这条判读污染掉。**自守卫**：先确认样本里真有这个字段。
    """
    sample = _usage("deepseek")
    assert getattr(sample, "prompt_cache_miss_tokens", None) == 240, "样本里根本没这个字段"
    result, _ = _call(_completion(sample))
    assert result.cache is not None
    assert result.cache.written_tokens is None, "把 miss 当成了写入量"


def test_the_shape_is_read_off_the_response_not_off_the_configured_vendor() -> None:
    """判据必须是「响应里有哪个字段」—— 配的是本地 Ollama，对面回 Anthropic 写法。

    整套 provider 押的就是 OpenAI 兼容 + 中转（OpenRouter 能转 Anthropic），
    照「我配的是谁」认形状的话，一换中转就认错。
    """
    result, endpoint = _call(_completion(_usage("anthropic")))
    assert endpoint.seen[0]["model"] == "qwen2.5"
    assert result.cache is not None and result.cache.shape is prov.CacheShape.ANTHROPIC


def test_an_unreadable_value_is_not_knowledge() -> None:
    """字段在、值读不懂（字符串 / 布尔 / 负数）⇒ 继续试下一家，都不中就是「不知道」。"""
    for bad in ("960", True, -1, 9.5):
        usage = _usage("deepseek", prompt_cache_hit_tokens=bad, prompt_cache_miss_tokens=bad)
        result, _ = _call(_completion(usage))
        assert result.cache is None, f"{bad!r} 被当成了一次真实测量"


def test_a_weird_usage_never_turns_a_paid_call_into_an_error() -> None:
    """已经生成完、已经花过钱的调用，不该因为 usage 里多个怪值就变成 `ProviderError`。"""
    usage = _usage("deepseek", prompt_cache_hit_tokens={"nested": 1})
    result, _ = _call(_completion(usage))
    assert result.text
    assert result.cache is None


VENDOR_FIELD_NAMES = (
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "prompt_tokens_details",
    "cached_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def test_nobody_downstream_recognizes_a_vendor_shape_a_second_time() -> None:
    """各家的字段名**全库只许出现在 `draft/provider.py` 里**。

    第二处认形状 = 第二张会漂的映射表，而它漂的那天两处会对同一次调用给两个答案。
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "draft" / "provider.py":
            continue
        text = path.read_text(encoding="utf-8")
        found = [name for name in VENDOR_FIELD_NAMES if name in text]
        if found:
            offenders[str(path.relative_to(SRC))] = found
    assert not offenders, f"下游又认了一遍供应商形状：{offenders}"


def test_the_shape_table_is_the_only_place_the_names_live() -> None:
    """连 `provider.py` 内部也只许有一份 —— 每个字段名只能出现在 `_CACHE_SHAPES` 那个赋值里。

    判据走 AST 而不是文本：注释和 docstring 里当然会写这些名字（那是解释，不是判据），
    拿正则数行会把「改了一段注释」变成一次假红。
    """
    tree = ast.parse((SRC / "draft" / "provider.py").read_text(encoding="utf-8"))
    table: ast.AST | None = None
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(getattr(t, "id", "") == "_CACHE_SHAPES" for t in targets):
            table = node
    assert table is not None, "`_CACHE_SHAPES` 不在模块顶层了 —— 这条网扫的是一块空地"

    inside = {id(n) for n in ast.walk(table)}
    strays: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in VENDOR_FIELD_NAMES:
            if id(node) not in inside:
                strays[str(node.value)] = node.lineno
    assert not strays, f"供应商字段名在表外还有一份（行号）：{strays}"


# ══════════════════════════════════════════════════════════════════════════
# 6. 屏幕 —— 三档都过同一份形状判据（判据在 test_wording_guard，不许抄第二份）
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("name", "read", "written"),
    [
        ("没报", None, None),
        ("报了 0", 0, None),
        ("报了数", 960, None),
        ("读写都有", 1_024, 176),
        ("写了 0", 1_024, 0),
        ("只有写", None, 176),
    ],
)
def test_every_branch_of_that_sentence_is_written_for_a_novelist(
    book: dict[str, str], name: str, read: int | None, written: int | None
) -> None:
    """六档都上屏，判据是形状（`dev_shapes`），**含兜底那一档**。

    夹具里只躺着一种形状的样本 = 屏幕守卫扫的是一块永远长一个样的屏幕，
    这个仓库上一次栽在这上面的现场记在 `test_activity::seed_call` 的 docstring 里。
    """
    call_id = seed_call(book, cache_read_tokens=read, cache_write_tokens=written)
    conn = connect(book["db"])
    try:
        detail = activity.read_entry(conn, book["pid"], call_id)
        assert detail is not None
        row = next(r for r in detail.rows if r.label == CACHE_ROW_LABEL)
    finally:
        conn.close()
    said = f"{row.label}：{row.value}"
    assert not dev_shapes(said), f"「{name}」这一档把引擎的词摆到了作者脸上：{said}"
    assert "缓存" not in said, "屏幕上不该出现机制的名字，作者关心的是结果"


def test_the_screen_judge_is_the_repo_wide_one_not_a_second_copy() -> None:
    """自守卫：上面那条用的判据必须真的会咬人 —— 否则它只是一句摆设。"""
    assert dev_shapes("接着上次的输入：cache_read_tokens 960")
    assert not dev_shapes("接着上次的输入：960 token 接着上次，没有重新算")
