"""kill-gate 的 runner —— 三臂 × N 次跑通一整轮，**真库 + 假 client，零网络请求**。

这个文件量的不是「代码跑得通」，是**仪器有没有接反**。每一条断言都对着一种会让整轮
读出假裁决的接错方式，而那些接错方式的共同点是：**跑起来完全正常，没有任何东西会红。**

| 断言 | 接反的样子 | 后果 |
|---|---|---|
| `leaked` 按 `kind` 取 | KNOWS 陷阱存了 `future_leak` | 主导裁决的那一列混进 echo 维度，系统性冤枉注入臂 |
| 每条陷阱只 `scene_view` 一次 | 判分和起草各算一次约束 | 两份禁忌集，gate 测的不是产品会执行的东西 |
| 三臂都收同一份 config | `config=None` | 三臂不再是同一次调用的三个取值（ADR 0010 D5） |
| 完整 messages 落盘 | 只存输出 | 「tell 漏进 prompt」失去唯一的可发现路径（ADR 0010 末尾） |
| `repeats=4` 抛错 | 硬跑 | `repeats=1` 时符号稳定性过滤退化成恒真，最难的 PASS 条件白送 |
| 两轮同输入 → 同 `GateInput` | 记录里塞时间戳 | ADR 0009 的结论没人能重算 |

**假 client 而不是打真模型**：这一轮的每一个「模型输出」都是这个文件写死的字符串，
所以「X0 泄漏、X1/X2 不泄漏」是**构造**出来的，不是测出来的。这里证明的是
「runner 把它数对了、记对了、归约对了」，**不是**「注入有用」——后者要真跑一轮
才知道，而那一轮的结论住 ADR 0009。
"""

from __future__ import annotations

import json
import inspect
import re
import types
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from novel_harness import db, project
from novel_harness.declare import Ledger
from novel_harness.draft.assemble import assemble
from novel_harness.draft.capabilities import (
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
)
from novel_harness.draft.context import resolve_constraints
from novel_harness.draft.length import M2_LENGTH_SPEC, DraftLanguage, count_units
from novel_harness.draft.provider import ProviderConfig, ProviderError
from novel_harness.eval import runner as runner_mod
from novel_harness.eval.runner import (
    ARMS,
    TrapSpec,
    load_traps,
    run_gate,
    stamped_path,
)
from novel_harness.graph import (
    AliasKind,
    EdgeProps,
    EdgeSpec,
    EdgeType,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.panel import UnresolvedCast

TELL = "玄血蛊"
"""血脉秘密的**内容 tell**，以非 canonical 别名声明。写进草稿 = KNOWS 泄漏。"""

FUTURE_TELL = "血枭盟"
"""未来实体，第 8 章首现。它**自身就是 tell**（协议 §3），所以有 echo 风险——
正因如此它只作描述性地板。第 5 章的草稿里出现它 = FUTURE 泄漏。"""

LOCAL = "http://localhost:11434/v1"
CAST = ["苏挽", "萧决"]
CHAPTER = 5

def _m2_valid(text: str) -> str:
    """Pad a fixture to 2,100 frozen Chinese units without changing its tell."""
    units = count_units(text, DraftLanguage.ZH)
    assert units <= 2_100
    return text + "静" * (2_100 - units)


CLEAN = _m2_valid("苏挽把茶盏推过去，没有接话。窗外的雨停了。")
KNOWS_LEAK = _m2_valid(f"苏挽压低声音：「那是{TELL}的痕迹。」")
FUTURE_LEAK = _m2_valid(f"远处传来消息，{FUTURE_TELL}的人已经动身。")

def _gate_invoke(argv: list[str]) -> tuple[int, str]:
    """跑 gate 入口（typer CliRunner 的替身：gate 是唯一还活着的命令面）。

    `argv` 保留旧的 `["gate", ...]` 词法（人肉对照原文省力）；捕获 stdout+stderr，
    返回 (退出码, 合并输出)。`gate_main` 返回 int、绝不 sys.exit。
    """
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from novel_harness.gate import main as gate_main

    assert argv and argv[0] == "gate"
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = gate_main(argv[1:])
    return code, buf.getvalue()


class Seeded(NamedTuple):
    path: Path
    pid: str
    ids: dict[str, str]


def _seed(path: Path) -> Seeded:
    """合成小册子的最小形态：一条 KNOWS 边界 + 一个未来实体，全程走生产写路径。

    第 5 章、cast = 苏挽 + 萧决：萧决 ch3 起知道血脉秘密，苏挽不知道 → 这一场
    `must_not_reveal = [血脉秘密]`（内容 tell 是「玄血蛊」）；血枭盟 ch8 首现 →
    `forbidden_entities = [血枭盟]`。**两个维度各有一条，KNOWS/FUTURE 才分得开。**
    """
    conn = db.connect(path)
    db.migrate(conn)
    pid = project.create(conn, name="合成小册子", root_path=str(path.parent)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)

    ids = {
        "萧决": ledger.declare_node(NodeLabel.CHARACTER, "萧决").id,
        "苏挽": ledger.declare_node(NodeLabel.CHARACTER, "苏挽").id,
        "血脉秘密": ledger.declare_node(
            NodeLabel.SECRET, "血脉秘密", secret=SecretDetail()
        ).id,
    }
    # tell 是**非 canonical** 别名：显示名「血脉秘密」进 prompt，内容 tell 只进检测器。
    ledger.declare_alias(of="血脉秘密", surface=TELL, kind=AliasKind.ALIAS)
    # 未来实体要 props.first_appears_chapter，而 `declare_node` 不收 props（同 test_api）。
    ids[FUTURE_TELL] = store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.CHARACTER,
            name=FUTURE_TELL,
            props=NodeProps.model_validate({"first_appears_chapter": 8}),
        )
    ).id
    store.upsert_edge(
        EdgeSpec(
            project_id=pid,
            src=ids["萧决"],
            dst=ids["血脉秘密"],
            type=EdgeType.KNOWS,
            props=EdgeProps(),
            valid_from_chapter=3,
            information_scope=InformationScope.CANON,
        )
    )
    conn.commit()
    conn.close()
    return Seeded(path=path, pid=pid, ids=ids)


@pytest.fixture
def book(tmp_path: Path) -> Seeded:
    return _seed(tmp_path / "booklet.db")


@pytest.fixture
def store(book: Seeded) -> SqliteStoryGraph:
    return SqliteStoryGraph(db.connect(book.path))


def _config() -> ProviderConfig:
    """一份合法的冻结 config。**默认模型在 `SAMPLING_STRICT_MODELS` 里，所以温度只能是 None。**"""
    return ProviderConfig(
        base_url=LOCAL,
        model="claude-opus-4-8",
        api_key="not-needed",
    )


def _call_plan(config: ProviderConfig) -> ResolvedCallPlan:
    """A frozen high-reasoning plan large enough for both M2 attempts."""
    capability = ProviderCapabilities(
        base_url=config.base_url,
        model=config.model,
        source="operator-test",
        source_urls=("https://example.test/capability",),
        max_context_tokens=128_000,
        max_output_tokens=16_000,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH}),
        reasoning_dialect=ReasoningDialect.ANTHROPIC_COMPAT,
        reasoning_shares_output=False,
    )
    return plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, capability)


_raw_run_gate = run_gate


def run_gate(*args: Any, **kwargs: Any) -> Any:
    """Keep legacy assertions concise while the public function still requires a plan."""
    config = kwargs.get("config")
    if config is not None:
        kwargs.setdefault("plan", _call_plan(config))
    else:
        kwargs.setdefault("plan", _call_plan(_config()))
    return _raw_run_gate(*args, **kwargs)


def _client(
    responder: Callable[[list[dict[str, str]], int], str | tuple[str, str | None]],
) -> tuple[Any, list[dict]]:
    """鸭子类型的 OpenAI 客户端。`responder(messages, call_index) -> 模型输出`。"""
    calls: list[dict] = []

    def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        response = responder(kwargs["messages"], len(calls) - 1)
        text, finish_reason = response if isinstance(response, tuple) else (response, "stop")
        return types.SimpleNamespace(
            model="fake",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content=text), finish_reason=finish_reason
                )
            ],
            usage=types.SimpleNamespace(prompt_tokens=11, completion_tokens=22),
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    return client, calls


def _by_arm(x0: str, x1: str, x2: str, *, repeats: int = 3) -> Callable[[Any, int], str]:
    """按调用序号分臂。**顺序是 runner 的契约**（`ARMS` 固定 X0→X1→X2，每臂连跑 repeats 次），
    `test_the_jsonl_is_ordered_by_trap_then_arm_then_repeat` 单独钉它——
    没有那条，这个 helper 就是在假设一件没人验过的事。"""
    texts = (x0, x1, x2)

    def responder(messages: Any, i: int) -> str:
        del messages
        return texts[(i // repeats) % 3]

    return responder


def _trap(trap_id: str, kind: str, *, reference: str = CLEAN) -> TrapSpec:
    return TrapSpec(
        id=trap_id,
        kind=kind,
        chapter=CHAPTER,
        cast=list(CAST),
        goal="苏挽来还伞，两人在檐下短暂交锋。",
        prior="上一章末尾，别人在她背后提过那件事，她没听见。",
        reference=reference,
    )


KNOWS_TRAP = _trap("K01", "KNOWS")
FUTURE_TRAP = _trap("F01", "FUTURE")


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _of_kind(path: Path, kind: str) -> list[dict]:
    return [r for r in _records(path) if r["kind"] == kind]


# ══════════════════════════════════════════════════════════════════════════
# ① 一整轮
# ══════════════════════════════════════════════════════════════════════════


def test_one_full_round(store: SqliteStoryGraph, book: Seeded, tmp_path: Path) -> None:
    """两条陷阱 × 三臂 × 3 次 = 18 次生成，一行不多一行不少。

    行数写死是有意的：一个「跑了但没记」或「记了但少记一臂」的 runner，
    在 `GateInput` 上完全看不出来（那边只看得见三个等长的元组）。
    """
    out = tmp_path / "runs" / "a.jsonl"
    client, calls = _client(_by_arm(KNOWS_LEAK, CLEAN, CLEAN))

    gi = run_gate(
        store,
        book.pid,
        [KNOWS_TRAP, FUTURE_TRAP],
        config=_config(),
        repeats=3,
        out_path=out,
        client=client,
    )

    assert len(gi.traps) == 2
    assert len(calls) == 2 * 3 * 3
    for trap in gi.traps:
        assert len(trap.x0) == len(trap.x1) == len(trap.x2) == 3
    # 1 头 + 2×(1 reference + 1 confound) + 18 attempt + 18 final
    assert len(_records(out)) == 1 + 2 * 2 + 18 * 2


def test_the_header_is_the_first_line_and_pins_the_protocol(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """头一行写协议版本与 config。**ADR 0009 要能指名道姓说「这一轮按哪份卷子跑的」。**"""
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    run_gate(store, book.pid, [KNOWS_TRAP], config=_config(), out_path=out, client=client)

    head = _records(out)[0]
    assert head["kind"] == "header"
    assert head["protocol"] == runner_mod.PROTOCOL_VERSION
    assert head["repeats"] == 3 and head["n_traps"] == 1
    assert head["project_id"] == book.pid
    assert head["config"]["model"] == _config().model
    assert head["config"]["base_url"] == LOCAL


def test_the_header_does_not_write_the_api_key(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """config 进头部（ADR 0010 D5 的复算要求），**但凭证不进**。

    `runs/*.jsonl` 是一份会被贴进 ADR、贴进 PR 的文件。复算需要的是「用什么模型跑的」，
    不是「用谁的 key 跑的」——而后者一旦落盘就再也收不回来。
    """
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    cfg = ProviderConfig(
        base_url=LOCAL,
        model="claude-opus-4-8",
        api_key="sk-绝密-不许落盘",
    )
    run_gate(store, book.pid, [KNOWS_TRAP], config=cfg, out_path=out, client=client)

    assert "sk-绝密-不许落盘" not in out.read_text(encoding="utf-8")
    head = _records(out)[0]
    assert "api_key" not in head["config"]
    assert head["config"]["api_key_set"] is True


def test_public_runner_requires_an_explicit_resolved_call_plan(
    store: SqliteStoryGraph,
    book: Seeded,
    tmp_path: Path,
) -> None:
    parameter = inspect.signature(runner_mod.run_gate).parameters["plan"]
    assert parameter.default is inspect.Parameter.empty

    out = tmp_path / "must-not-exist.jsonl"
    client, calls = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    with pytest.raises(TypeError, match="plan"):
        runner_mod.run_gate(
            store,
            book.pid,
            [KNOWS_TRAP],
            config=_config(),
            out_path=out,
            client=client,
        )
    assert calls == []
    assert not out.exists()


def test_header_and_cell_records_pin_amendment_5_evidence(
    store: SqliteStoryGraph,
    book: Seeded,
    tmp_path: Path,
) -> None:
    out = tmp_path / "run.jsonl"
    config = _config()
    plan = _call_plan(config)
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))

    _raw_run_gate(
        store,
        book.pid,
        [KNOWS_TRAP],
        config=config,
        plan=plan,
        out_path=out,
        client=client,
    )

    records = _records(out)
    head = records[0]
    assert head["protocol"] == (
        "EVAL_PROTOCOL.md@0393088 + 修正案 1/2/3/4/5/6/7/8 + ADR 0010/0011"
    )
    assert head["length_profile"] == M2_LENGTH_SPEC.model_dump(mode="json")
    assert head["counting_rule"] == "nh-length-v1"
    assert head["continuation"] == {
        "max_attempts": 2,
        "trigger": "under_min_only",
    }
    assert head["call_plan"]["reasoning_requested"] == "high"
    assert head["call_plan"]["reasoning_effective"] == "high"
    levels = head["call_plan"]["capability"]["reasoning_levels"]
    assert levels == sorted(levels)

    attempts = [record for record in records if record["kind"] == "generation_attempt"]
    finals = [record for record in records if record["kind"] == "generation"]
    assert len(attempts) == len(finals) == 9
    for attempt in attempts:
        assert attempt["attempt"] == 1
        assert attempt["messages"]
        assert attempt["segment_length"]["actual_units"] == 2_100
        assert attempt["cumulative_length"]["actual_units"] == 2_100
        assert attempt["needs_continuation"] is False
    for final in finals:
        assert final["length"]["actual_units"] == 2_100
        assert final["attempt_count"] == 1
        assert final["output"] == CLEAN

    cell_kinds = [
        record["kind"]
        for record in records
        if record.get("trap_id") == "K01" and "arm" in record
    ]
    assert cell_kinds == ["generation_attempt", "generation"] * 9


def test_non_high_or_mismatched_plan_fails_before_any_evidence_or_query(
    store: SqliteStoryGraph,
    book: Seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    high = _call_plan(config)
    off = plan_call(M2_LENGTH_SPEC, ReasoningEffort.OFF, high.capability)
    plans = (
        off,
        high.model_copy(update={"model": "wrong-model"}),
    )

    for index, plan in enumerate(plans):
        out = tmp_path / f"preflight-{index}" / "run.jsonl"
        client, calls = _client(_by_arm(CLEAN, CLEAN, CLEAN))
        scene_calls: list[tuple[Any, ...]] = []

        def forbidden_scene_view(*args: Any, **kwargs: Any) -> Any:
            scene_calls.append((*args, kwargs))
            raise AssertionError("plan preflight must precede scene_view")

        monkeypatch.setattr(runner_mod, "scene_view", forbidden_scene_view)
        with pytest.raises(ValueError, match="high|route"):
            _raw_run_gate(
                store,
                book.pid,
                [KNOWS_TRAP],
                config=config,
                plan=plan,
                out_path=out,
                client=client,
            )
        assert calls == []
        assert scene_calls == []
        assert not out.exists() and not out.parent.exists()


@pytest.mark.parametrize(
    ("responses", "expected_status", "expected_attempts"),
    [
        (("甲", "乙"), "under", 2),
        ((("甲" * 3_411),), "over", 1),
        (((CLEAN, "length"),), "within", 1),
    ],
)
def test_length_invalid_is_terminal_and_never_scored(
    store: SqliteStoryGraph,
    book: Seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: tuple[str | tuple[str, str | None], ...],
    expected_status: str,
    expected_attempts: int,
) -> None:
    out = tmp_path / "invalid.jsonl"
    scripted = list(responses)

    def responder(
        messages: list[dict[str, str]], index: int
    ) -> str | tuple[str, str | None]:
        del messages
        return scripted[index]

    client, calls = _client(responder)
    score_calls: list[str] = []
    real_score = runner_mod.score_against

    def score_spy(*args: Any, **kwargs: Any) -> Any:
        text = args[-1]
        score_calls.append(text)
        return real_score(*args, **kwargs)

    monkeypatch.setattr(runner_mod, "score_against", score_spy)

    with pytest.raises(runner_mod.LengthInvalidError) as caught:
        _raw_run_gate(
            store,
            book.pid,
            [KNOWS_TRAP],
            config=_config(),
            plan=_call_plan(_config()),
            out_path=out,
            client=client,
        )

    assert caught.value.trap_id == "K01"
    assert caught.value.arm == "x0"
    assert caught.value.repeat == 0
    assert caught.value.measurement.status == expected_status
    assert len(calls) == expected_attempts
    assert score_calls == [KNOWS_TRAP.reference], "invalid final must not reach scorer"
    records = _records(out)
    assert [record["kind"] for record in records[-expected_attempts - 1 :]] == [
        *("generation_attempt" for _ in range(expected_attempts)),
        "length_invalid",
    ]
    assert not _of_kind(out, "generation")


def test_over_within_tolerance_completes_and_is_flagged(
    store: SqliteStoryGraph,
    book: Seeded,
    tmp_path: Path,
) -> None:
    """修正案 8：3,300 字（≤3,410 宽容带）是有效 cell，不判死、带标记。"""
    out = tmp_path / "tolerated.jsonl"
    long_text = "甲" * 3_300

    def responder(messages: list[dict[str, str]], index: int) -> str:
        del messages, index
        return long_text

    client, calls = _client(responder)
    _raw_run_gate(
        store,
        book.pid,
        [KNOWS_TRAP],
        config=_config(),
        plan=_call_plan(_config()),
        repeats=3,
        out_path=out,
        client=client,
    )
    records = _records(out)
    generations = [r for r in records if r["kind"] == "generation"]
    assert len(generations) == 9  # 3 臂 × 3 次
    assert all(r["length_tolerated"] is True for r in generations)
    assert all(r["length"]["actual_units"] == 3_300 for r in generations)


def test_first_attempt_is_synced_before_a_continuation_transport_failure(
    store: SqliteStoryGraph,
    book: Seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "partial.jsonl"
    client, calls = _client(lambda messages, index: "甲")
    create = client.chat.completions.create

    def fail_second(**kwargs: Any) -> Any:
        if len(calls) == 1:
            raise RuntimeError("second transport exploded")
        return create(**kwargs)

    client.chat.completions.create = fail_second
    syncs: list[int] = []
    real_fsync = runner_mod.os.fsync

    def fsync_spy(fd: int) -> None:
        syncs.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(runner_mod.os, "fsync", fsync_spy)

    with pytest.raises(ProviderError, match="second transport exploded"):
        _raw_run_gate(
            store,
            book.pid,
            [KNOWS_TRAP],
            config=_config(),
            plan=_call_plan(_config()),
            out_path=out,
            client=client,
        )

    assert len(calls) == 1
    assert syncs, "flush without fsync is not durable attempt evidence"
    attempts = _of_kind(out, "generation_attempt")
    assert len(attempts) == 1 and attempts[0]["attempt"] == 1
    assert attempts[0]["output"] == "甲"
    assert not _of_kind(out, "generation")
    assert not _of_kind(out, "length_invalid"), "transport failure is incomplete, not INVALID"


# ══════════════════════════════════════════════════════════════════════════
# ② messages 必须原样落盘（ADR 0010 末尾那条「唯一可发现路径」）
# ══════════════════════════════════════════════════════════════════════════


def test_the_full_messages_land_on_disk(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """每一次生成的**完整 messages** 都在 jsonl 里，且中文是中文不是 `\\uXXXX`。

    「tell 漏进 prompt」是这套仪器最贵的错误（Δ 翻负 → 裁决表读出假 KILL），而
    ADR 0010 明写发现它的唯一办法是**人去读**存下来的 prompt 原文。
    `ensure_ascii=True` 存出来的中文人读不了——那和没存差不多。
    """
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    run_gate(store, book.pid, [KNOWS_TRAP], config=_config(), out_path=out, client=client)

    raw = out.read_text(encoding="utf-8")
    assert "血脉秘密" in raw, "秘密的显示名该在 prompt 里（X1/X2 注入的就是标签）"
    assert "\\u" not in raw, "ensure_ascii=False —— 一份人读不了的审计记录等于没有审计记录"

    for rec in _of_kind(out, "generation_attempt"):
        assert rec["messages"], "空 messages = 这条记录证明不了 prompt 里有没有 tell"
        assert all({"role", "content"} <= set(m) for m in rec["messages"])


def test_the_prompts_are_exactly_what_assemble_produces(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """落盘的 messages == 生产渲染器 `assemble()` 的出参，**逐字节**。

    协议 §2：三臂不是三套 prompt 构造器，是同一个生产渲染器的三个 form。runner 若在
    中间加一句自己的话（哪怕只加给某一臂），gate 测的就不是产品会发的东西——而那**正是**
    PLAN §5.7 那个内建混淆的复发形态。
    """
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    run_gate(store, book.pid, [KNOWS_TRAP], config=_config(), out_path=out, client=client)

    ctx = resolve_constraints(store, book.pid, CHAPTER, CAST)
    expected = {
        arm: assemble(
            ctx,
            form=form,
            goal=KNOWS_TRAP.goal,
            length=M2_LENGTH_SPEC,
            previous_tail=KNOWS_TRAP.prior,
        )
        for arm, form in ARMS
    }
    for rec in _of_kind(out, "generation_attempt"):
        assert rec["messages"] == expected[rec["arm"]], f"{rec['arm']} 的 prompt 不是 assemble 出的"


def test_the_tell_never_reaches_the_prompt(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """**这条是本文件最重要的一条。**

    tell 一旦进了 X1/X2 的 prompt，两臂 100% 命中自己写进去的词，`Δ` 翻负，
    预注册的裁决表逐字读出「KILL 起草线」——把一个本来对的项目砍掉，而全程没有东西会红。
    第 4 道 arch-guard 扫的是符号引用，**它对字符串内容一无所知**；这一条补的正是那一格
    在真实渲染上的那半边。
    """
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    run_gate(store, book.pid, [KNOWS_TRAP], config=_config(), out_path=out, client=client)

    for rec in _of_kind(out, "generation_attempt"):
        prompt = "\n".join(m["content"] for m in rec["messages"])
        assert TELL not in prompt, f"{rec['arm']} 的 prompt 里出现了内容 tell —— 仪器接反了"


def test_the_jsonl_is_ordered_by_trap_then_arm_then_repeat(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """写入顺序是审计记录的一部分（人比对时按它读），也是本文件 `_by_arm` 的前提。"""
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    run_gate(
        store,
        book.pid,
        [KNOWS_TRAP, FUTURE_TRAP],
        config=_config(),
        out_path=out,
        client=client,
    )

    seen = [(r["trap_id"], r["arm"], r["repeat"]) for r in _of_kind(out, "generation")]
    assert seen == [
        (trap, arm, i)
        for trap in ("K01", "F01")
        for arm, _ in ARMS
        for i in range(3)
    ]


# ══════════════════════════════════════════════════════════════════════════
# ③ `leaked` 存的是这条陷阱瞄的那一类
# ══════════════════════════════════════════════════════════════════════════


def test_a_knows_trap_records_knows_violation_only(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """KNOWS 陷阱的草稿里只写了**未来实体**（FUTURE 泄漏）→ `leaked` 必须是 False。

    取错这一格的后果不是「数字差一点」：FUTURE 的 tell 必然出现在 X1/X2 的 prompt 里
    （echo 风险，§5 caveat），把它混进主导裁决的那一列会系统性地冤枉注入臂。
    """
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(FUTURE_LEAK, FUTURE_LEAK, FUTURE_LEAK))
    gi = run_gate(store, book.pid, [KNOWS_TRAP], config=_config(), out_path=out, client=client)

    assert gi.traps[0].x0 == (False, False, False)
    # 而 jsonl 里那一维照记不误 —— 判分器看见了，只是这条陷阱不读它。
    gen = _of_kind(out, "generation")
    assert all(r["leak"]["future_leak"] is True for r in gen)
    assert all(r["leak"]["knows_violation"] is False for r in gen)
    assert all(r["leaked"] is False for r in gen)


def test_a_future_trap_records_future_leak_only(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """镜像：FUTURE 陷阱的草稿里只写了 KNOWS 的 tell → `leaked` 必须是 False。

    没有这条镜像，上面那条也可能是因为「`leaked` 恒取 knows_violation」而绿的。
    """
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(KNOWS_LEAK, KNOWS_LEAK, KNOWS_LEAK))
    gi = run_gate(store, book.pid, [FUTURE_TRAP], config=_config(), out_path=out, client=client)

    assert gi.traps[0].x0 == (False, False, False)
    gen = _of_kind(out, "generation")
    assert all(r["leak"]["knows_violation"] is True for r in gen)
    assert all(r["leaked"] is False for r in gen)


def test_the_arms_are_counted_independently(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """X0 泄漏、X1/X2 干净——这是这个仪器要量的那个形状，所以它必须真的量得出来。"""
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(KNOWS_LEAK, CLEAN, CLEAN))
    gi = run_gate(store, book.pid, [KNOWS_TRAP], config=_config(), out_path=out, client=client)

    trap = gi.traps[0]
    assert trap.x0 == (True, True, True)
    assert trap.x1 == (False, False, False)
    assert trap.x2 == (False, False, False)
    assert trap.kind == "KNOWS" and trap.trap_id == "K01"


# ══════════════════════════════════════════════════════════════════════════
# ④ 天花板门：reference 判一次分
# ══════════════════════════════════════════════════════════════════════════


def test_a_clean_reference_does_not_trip_the_ceiling(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    gi = run_gate(store, book.pid, [KNOWS_TRAP], config=_config(), out_path=out, client=client)

    assert gi.traps[0].reference_leaked is False
    assert len(_of_kind(out, "reference")) == 1


def test_a_leaky_reference_trips_the_ceiling(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """`reference` 是**人工写的、本该干净的**那一份完成。它被判泄漏说明检测器在一份
    干净文本上开了火（或者这条陷阱物理上无法满足）→ §6 第 2 行 INVALID，这一轮不可信。

    它不占模型调用：18 次生成之外只多一次判分。
    """
    out = tmp_path / "a.jsonl"
    client, calls = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    trap = _trap("K02", "KNOWS", reference=KNOWS_LEAK)
    gi = run_gate(store, book.pid, [trap], config=_config(), out_path=out, client=client)

    assert gi.traps[0].reference_leaked is True
    assert len(calls) == 9, "reference 是判分，不是生成——它不该多打一次模型"


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 一条陷阱只算一次约束，两侧共用同一个对象
# ══════════════════════════════════════════════════════════════════════════


def test_scene_view_is_called_once_per_trap(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """9 次生成 + 1 次 reference 判分，`scene_view()` 只许调**一次**。

    多算一次不会报错、数字也多半一样——直到某天图变了、或者 `resolve` 的花名册在两次
    查询之间漂了一格。那时 prompt 里的事实和判分用的禁忌集不再是同一份，
    而 EVAL_PROTOCOL §3 的「禁忌集只有一个来源」要在**对象层**成立，不是靠碰巧。
    """
    seen: list[tuple] = []
    real = runner_mod.scene_view

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(runner_mod, "scene_view", spy)

    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    run_gate(
        store,
        book.pid,
        [KNOWS_TRAP, FUTURE_TRAP],
        config=_config(),
        out_path=out,
        client=client,
    )

    assert len(seen) == 2, f"两条陷阱应各算一次约束，实测调了 {len(seen)} 次"


def test_an_unresolvable_cast_names_the_trap(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """25 条陷阱里坏了一条，报错必须说得出是哪一条。

    `UnresolvedCast` 自己只知道章号，而合成小册子里同一章会有好几条陷阱——
    一条指不到人的报错，作者（这里是维护者）唯一的下一步是把 25 条挨个试。
    """
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    bad = TrapSpec(
        id="K09",
        kind="KNOWS",
        chapter=CHAPTER,
        cast=["查无此人"],
        goal="随便",
        prior="随便",
        reference=CLEAN,
    )
    with pytest.raises(UnresolvedCast, match="K09"):
        run_gate(store, book.pid, [bad], config=_config(), out_path=out, client=client)


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 冻结的 config（ADR 0010 D5）
# ══════════════════════════════════════════════════════════════════════════


def test_every_call_carries_the_same_frozen_config(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """三臂 × 3 次共 9 次调用，参数逐次相同且来自同一份 config。"""
    out = tmp_path / "a.jsonl"
    client, calls = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    cfg = _config()
    run_gate(store, book.pid, [KNOWS_TRAP], config=cfg, out_path=out, client=client)

    assert len(calls) == 9
    for kwargs in calls:
        assert kwargs["model"] == cfg.model
        assert kwargs["max_tokens"] == _call_plan(cfg).request_token_budget
        # 默认模型拒绝非默认采样参数 → temperature 是 None → 这个字段根本不发。
        assert "temperature" not in kwargs


def test_config_none_is_refused(store: SqliteStoryGraph, book: Seeded, tmp_path: Path) -> None:
    """`config=None` 会让每一次 `complete()` 现读环境变量（ADR 0010 D5 点名的那个洞）。

    类型标注写着 `ProviderConfig`，但 Python 不检查它——所以这里必须运行期判死，
    而且要在烧第一个 token 之前判：一轮跑到一半才发现三臂用了不同参数，只能整轮作废。
    """
    out = tmp_path / "a.jsonl"
    client, calls = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    with pytest.raises(ValueError, match="ProviderConfig"):
        run_gate(store, book.pid, [KNOWS_TRAP], config=None, out_path=out, client=client)
    assert not calls and not out.exists(), "该在开跑之前就红，不该留下半份 jsonl"


def test_run_gate_itself_fails_closed_until_amendment_5_is_implemented(
    store: SqliteStoryGraph,
    book: Seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public library callers cannot bypass the CLI's preregistration guard."""
    monkeypatch.setattr(
        runner_mod,
        "PROTOCOL_VERSION",
        "EVAL_PROTOCOL.md@0393088 + 修正案 1/2/3/4 + ADR 0010",
    )
    out = tmp_path / "runs" / "must-not-exist.jsonl"
    client, calls = _client(_by_arm(CLEAN, CLEAN, CLEAN))

    with pytest.raises(ValueError, match="修正案 5"):
        run_gate(
            store,
            book.pid,
            [KNOWS_TRAP],
            config=_config(),
            out_path=out,
            client=client,
        )

    assert calls == [], "协议未实现时不得发出模型请求"
    assert not out.exists(), "协议未实现时不得创建证据文件"
    assert not out.parent.exists(), "协议 guard 必须早于证据目录创建"


def test_run_gate_refuses_to_overwrite_existing_evidence(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`runs/*.jsonl` 是预注册证据：同名文件存在时必须原样保留，且一枚 token 都不烧。"""
    out = tmp_path / "existing.jsonl"
    sentinel = b"SENTINEL_EXISTING_EVIDENCE\n"
    out.write_bytes(sentinel)
    client, calls = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    scene_calls: list[tuple[Any, ...]] = []

    def forbidden_scene_view(*args: Any, **kwargs: Any) -> Any:
        scene_calls.append((*args, kwargs))
        raise AssertionError("重名结果应该在查询场景之前判死")

    monkeypatch.setattr(runner_mod, "scene_view", forbidden_scene_view)

    with pytest.raises(ValueError, match="已存在"):
        run_gate(
            store,
            book.pid,
            [KNOWS_TRAP],
            config=_config(),
            out_path=out,
            client=client,
        )

    assert calls == [], "发现重名必须早于第一次模型调用"
    assert scene_calls == [], "发现重名必须早于 scene_view 查询"
    assert out.read_bytes() == sentinel, "已有实验记录一字节都不许被覆盖"


# ══════════════════════════════════════════════════════════════════════════
# ⑦ repeats 白名单（修正案 3 裁定 3）
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("repeats", [0, 1, 2, 4, 7])
def test_illegal_repeats_are_refused(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path, repeats: int
) -> None:
    """协议只定义 3（§5）和 5（修正案 2）。**别的值一律抛错，不许硬判。**

    `repeats=1` 尤其危险：符号稳定性过滤会退化成恒真（只有一次重复，它必然与自己同号），
    四个 PASS 条件里最难的那个白送，而裁决记录上还写着「符号稳定」。
    """
    out = tmp_path / "a.jsonl"
    client, calls = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    with pytest.raises(ValueError, match="重复"):
        run_gate(
            store,
            book.pid,
            [KNOWS_TRAP],
            config=_config(),
            repeats=repeats,
            out_path=out,
            client=client,
        )
    assert not calls and not out.exists()


def test_five_repeats_is_the_escalated_round(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """5 是缓刑轮（修正案 2），必须跑得起来——否则 INCONCLUSIVE 的预案是一句空话。"""
    out = tmp_path / "a.jsonl"
    client, calls = _client(_by_arm(KNOWS_LEAK, CLEAN, CLEAN, repeats=5))
    gi = run_gate(
        store, book.pid, [KNOWS_TRAP], config=_config(), repeats=5, out_path=out, client=client
    )

    assert len(calls) == 15
    assert gi.traps[0].x0 == (True,) * 5 and gi.traps[0].x1 == (False,) * 5
    assert _records(out)[0]["repeats"] == 5


def test_an_empty_trap_set_is_refused(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """零陷阱跑完会得到一份空 jsonl + 一张漂亮的裁决表。那是 `demo.sh` 警告的那个形态。"""
    with pytest.raises(ValueError, match="陷阱"):
        run_gate(store, book.pid, [], config=_config(), out_path=tmp_path / "a.jsonl")


def test_duplicate_trap_ids_are_refused(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="唯一"):
        run_gate(
            store,
            book.pid,
            [KNOWS_TRAP, KNOWS_TRAP],
            config=_config(),
            out_path=tmp_path / "a.jsonl",
        )


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 出参只由输入决定（时间戳只进文件名）
# ══════════════════════════════════════════════════════════════════════════


def test_the_same_inputs_give_the_very_same_gate_input(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """同一份输入跑两遍（落两个不同的文件），`GateInput` 必须逐字段相等。

    这条钉的是「裁决可复算」：ADR 0009 的结论要能被任何人拿同一份 `runs/*.jsonl`
    用 `decide()` 重算出来。`GateInput` 里只要混进一个时间戳、一个文件名、一个随机 id，
    这条就不再成立，而没有别的东西会红。
    """
    first_client, _ = _client(_by_arm(KNOWS_LEAK, CLEAN, CLEAN))
    second_client, _ = _client(_by_arm(KNOWS_LEAK, CLEAN, CLEAN))
    kwargs = {"config": _config(), "client": None}

    a = run_gate(
        store, book.pid, [KNOWS_TRAP], out_path=tmp_path / "a.jsonl",
        **{**kwargs, "client": first_client}
    )
    b = run_gate(
        store, book.pid, [KNOWS_TRAP], out_path=tmp_path / "b.jsonl",
        **{**kwargs, "client": second_client}
    )
    assert a == b


def test_stamped_path_is_the_only_clock_reader(tmp_path: Path) -> None:
    """时间戳只出现在文件名里。**它不进任何一条记录，更不进 `GateInput`。**"""
    path = stamped_path(tmp_path / "runs")
    assert path.parent == tmp_path / "runs"
    assert re.fullmatch(r"\d{8}T\d{6}Z\.jsonl", path.name), path.name
    # 全模块只有 `stamped_path` 读时钟：源码里 `datetime` / `time` 出现的次数就是证据。
    source = Path(runner_mod.__file__).read_text(encoding="utf-8")
    assert source.count("datetime.now(") == 1
    assert "time.time(" not in source


# ══════════════════════════════════════════════════════════════════════════
# ⑨ ground_truth.json → TrapSpec：runner 不读 tells
# ══════════════════════════════════════════════════════════════════════════

GROUND_TRUTH = {
    "project_id": "project:synth:01J0",
    "chapters": 12,
    "traps": [
        {
            "id": "K01",
            "kind": "KNOWS",
            "chapter": 5,
            "cast": ["苏挽", "萧决"],
            "target": "血脉秘密",
            "goal": "苏挽来还伞。",
            "prior": "上一章末尾别人提过。",
            "reference": "她没有接话。",
            "must_not_reveal": ["血脉秘密"],
            "forbidden": ["血枭盟"],
            "tells": [TELL],
        }
    ],
}


def test_trap_spec_physically_cannot_hold_a_tell() -> None:
    """**判分绝不许读 `tells`。**

    那会是一份独立于 `panel.constraints` 的禁忌集，`checks/base.py` 的
    「判分器 == Validator，同一份代码」当场名存实亡，跑出来的 p 值不说明任何
    关于产品的事。所以这条纪律不写成注释，写成「这个类型里没有那个字段」。
    """
    fields = set(TrapSpec.model_fields)
    assert fields == {"id", "kind", "chapter", "cast", "goal", "prior", "reference"}
    for leaky in ("tells", "must_not_reveal", "forbidden", "target"):
        assert leaky not in fields


def test_load_traps_picks_fields_and_drops_the_rest() -> None:
    traps = load_traps(GROUND_TRUTH)
    assert [t.id for t in traps] == ["K01"]
    assert traps[0].kind == "KNOWS" and traps[0].cast == ["苏挽", "萧决"]
    assert TELL not in traps[0].model_dump_json()


def test_load_traps_refuses_an_empty_file() -> None:
    with pytest.raises(ValueError, match="一条陷阱都没有"):
        load_traps({"project_id": "x", "traps": []})


def test_load_traps_refuses_a_broken_row_instead_of_skipping_it() -> None:
    """**坏行不许静默跳过。** 少跑一条陷阱最后表现为 n 变小、判别对变少 →
    INCONCLUSIVE，而那时没人分得清是数据坏了还是仪器没测到东西。"""
    broken = {"traps": [{**GROUND_TRUTH["traps"][0], "prior": None}]}
    del broken["traps"][0]["reference"]
    with pytest.raises(ValueError, match="形状不对"):
        load_traps(broken)


# ══════════════════════════════════════════════════════════════════════════
# ⑩ 反混淆报告
# ══════════════════════════════════════════════════════════════════════════


def test_a_confound_report_lands_for_every_trap(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """每条陷阱一行 confound 记录，且 `GateInput.confound_ok` 就是它们的合取。

    这里**不断言 ok 是 True**：X1 与 X2 的渲染差异是 `assemble()` 的事，
    小册子长什么样也会影响它。这条钉的是「runner 把这个开关接对了」——
    真报警时 §6 第 3 行只摘掉决策 B，A 照评。
    """
    out = tmp_path / "a.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    gi = run_gate(
        store,
        book.pid,
        [KNOWS_TRAP, FUTURE_TRAP],
        config=_config(),
        out_path=out,
        client=client,
    )

    reports = _of_kind(out, "confound")
    assert [r["trap_id"] for r in reports] == ["K01", "F01"]
    assert all(r["report"] is not None for r in reports), "confound_lint 已落地，不该再走缺席分支"
    assert gi.confound_ok == all(r["report"]["ok"] for r in reports)


def test_the_two_injected_arms_actually_pass_the_confound_lint(
    store: SqliteStoryGraph, book: Seeded, tmp_path: Path
) -> None:
    """**这条断言 ok 是 True，上面那条故意不断言——两条缺一不可。**

    上面钉的是「开关接对了」，这条钉的是「开关的读数是绿的」。少了这条，
    `assemble()` 哪天让 X1 与 X2 拉开距离（多加一行、换个措辞），
    `confound_ok` 会静静地变成 False，§6 第 3 行摘掉决策 B，
    **FORM-PIVOT 从此对每一轮 gate 都失效，而没有任何东西会红**——
    裁决照出，只是少了一个维度，读结果的人根本不会知道它被摘过。

    实测余量（2026-07-30，`test_draft_assemble.py` 的满配 ctx）：
    整条 prompt 411 vs 421 可见字符，比值 1.024，容差 1.15；人名集合差为空。
    余量不小，但「不小」不是「有人在看着」。
    """
    out = tmp_path / "ok.jsonl"
    client, _ = _client(_by_arm(CLEAN, CLEAN, CLEAN))
    gi = run_gate(
        store,
        book.pid,
        [KNOWS_TRAP, FUTURE_TRAP],
        config=_config(),
        out_path=out,
        client=client,
    )

    bad = [r for r in _of_kind(out, "confound") if not r["report"]["ok"]]
    assert not bad, (
        "X1 与 X2 没通过反混淆检查，FORM-PIVOT 会被静默摘掉：\n  "
        + "\n  ".join(f"{r['trap_id']}: {r['report']['reasons']}" for r in bad)
        + "\n多半是 assemble() 的两个 form 拉开了距离——改 assemble()，别放宽 LEN_TOLERANCE"
        + "（那个数是预注册的旋钮，见 EVAL_PROTOCOL §2）。"
    )
    assert gi.confound_ok is True


# ══════════════════════════════════════════════════════════════════════════
# ⑪ runner 住在 eval/ —— 这不是风格问题
# ══════════════════════════════════════════════════════════════════════════


def test_the_runner_lives_inside_the_wall() -> None:
    """`tests/test_draft_boundary.py` 的 `SCORER_DIRS` docstring 逐字写着：runner 落在
    `cli.py` 或顶层 `synth/` 就绕过整堵墙。**它是同时碰两侧的唯一一段代码**，
    在墙外意味着墙的两面都可以被它一个人破掉。

    第 4 道守卫扫的是目录，所以「文件在哪儿」就是「它受不受管」——把它钉住。
    """
    assert Path(runner_mod.__file__).parent.name == "eval"


# ══════════════════════════════════════════════════════════════════════════
# ⑫ nh gate
# ══════════════════════════════════════════════════════════════════════════


def _write_ground_truth(path: Path, pid: str, traps: Sequence[dict]) -> Path:
    path.write_text(
        json.dumps({"project_id": pid, "chapters": 12, "traps": list(traps)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _row(trap_id: str, kind: str) -> dict:
    return {
        "id": trap_id,
        "kind": kind,
        "chapter": CHAPTER,
        "cast": list(CAST),
        "goal": "苏挽来还伞。",
        "prior": "上一章末尾别人提过。",
        "reference": CLEAN,
        "tells": [TELL],
    }


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2026-08-02 起 CLI gate 会按 env 里的 route 从注册表解析能力并冻结 plan；
    # 测试环境用已登记的 DeepSeek V4 路由，plan 构建才走真实路径（complete 仍被桩替换）。
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "not-needed")
    monkeypatch.delenv("NH_LLM_TEMPERATURE", raising=False)
    monkeypatch.delenv("NH_LLM_MAX_TOKENS", raising=False)


@pytest.fixture
def amendment_5_runner_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI gate 自 2026-08-02 起自带 plan 构建，不再注入（保留名字兼容旧测试）。"""
    return None


def _stub_complete(monkeypatch: pytest.MonkeyPatch, texts: Sequence[str]) -> list[dict]:
    """把 `runner.complete` 换成一个按调用序号发稿的桩。

    CLI 没有 `client=` 注入点（那是 runner 的参数），而给一条命令加一个只有测试会用的
    旗标，就是让被测物迁就断言。换掉 runner 里的那个名字更诚实：换掉的是**模型**，
    runner 自己一行没动。
    """
    from novel_harness.draft.provider import CompletionResult

    calls: list[dict] = []

    def fake(
        messages: Any,
        *,
        config: Any = None,
        plan: Any = None,
        client: Any = None,
    ) -> CompletionResult:
        del client
        calls.append({"messages": messages, "config": config, "plan": plan})
        return CompletionResult(text=texts[(len(calls) - 1) % len(texts)], model="fake")

    from novel_harness.draft import generate as generate_module

    monkeypatch.setattr(generate_module, "complete", fake)
    return calls


def test_gate_fails_closed_until_amendment_5_runner_is_implemented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner_mod,
        "PROTOCOL_VERSION",
        "EVAL_PROTOCOL.md@0393088 + 修正案 1/2/3/4 + ADR 0010",
    )
    out = tmp_path / "must-not-exist.jsonl"
    code, captured = _gate_invoke(
        [
            "gate",
            "--db", str(tmp_path / "missing.db"),
            "--project", "project:test",
            "--ground-truth", str(tmp_path / "missing.json"),
            "--out", str(out),
        ],
    )

    assert code != 0
    assert "已暂停" in captured
    assert "1/2/3/4/5/6" in captured
    assert not out.exists()


def test_gate_without_an_endpoint_gives_chinese_instructions(
    book: Seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amendment_5_runner_ready: None,
) -> None:
    """没配 `NH_LLM_BASE_URL` 时给修复指引再退出，**不抛 traceback**。

    敲这条命令的是维护者，但 `provider.py` 那条「不替你猜供应商」的拒绝写给的是作者——
    一份 pydantic 的 4 行 `[type=value_error, input_value=...]` 会让人跳过整段，
    包括唯一告诉他该怎么办的那半句（同 `_reason()` 的理由）。
    """
    monkeypatch.delenv("NH_LLM_BASE_URL", raising=False)
    gt = _write_ground_truth(tmp_path / "gt.json", book.pid, [_row("K01", "KNOWS")])

    code, captured = _gate_invoke(
        ["gate", "--db", str(book.path), "-p", book.pid, "--ground-truth", str(gt)],
    )

    assert code != 0
    assert "NH_LLM_BASE_URL" in captured
    assert "Traceback" not in captured


def test_gate_rejects_legacy_global_token_env_without_a_traceback(
    book: Seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    llm_env: None,
    amendment_5_runner_ready: None,
) -> None:
    monkeypatch.setenv("NH_LLM_MAX_TOKENS", "4096")
    gt = _write_ground_truth(tmp_path / "gt.json", book.pid, [_row("K01", "KNOWS")])

    code, captured = _gate_invoke(
        ["gate", "--db", str(book.path), "-p", book.pid, "--ground-truth", str(gt)],
    )

    assert code != 0
    assert "NH_LLM_MAX_TOKENS" in captured
    assert "call plan" in captured
    assert "Traceback" not in captured


def test_gate_prints_how_many_traps_and_generations(
    book: Seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    llm_env: None,
    amendment_5_runner_ready: None,
) -> None:
    """「跑了几条陷阱、几次生成」是这条命令的成功输出本身（同 `nh check` 的「跑了几条规则」）。

    两条 KNOWS 陷阱、X0 在第一条上泄漏 → `x0_knows_leak = 0.50`，刚好越过地板门，
    于是走到判别对那一档 → INCONCLUSIVE。**那是一次有效实验的合法结论，退出码 0。**
    """
    _stub_complete(monkeypatch, [CLEAN])
    gt = _write_ground_truth(
        tmp_path / "gt.json", book.pid, [_row("K01", "KNOWS"), _row("K02", "KNOWS")]
    )
    out = tmp_path / "runs" / "one.jsonl"

    code, captured = _gate_invoke(
        [
            "gate",
            "--db", str(book.path),
            "-p", book.pid,
            "--ground-truth", str(gt),
            "--out", str(out),
        ],
    )

    assert code == 1, captured  # 全干净 → X0 泄漏 0.00 → 地板 → INVALID
    assert "2 条陷阱 × 3 臂 × 3 次 = 18 次生成" in captured
    assert "INVALID" in captured
    assert out.exists() and len(_records(out)) == 1 + 2 * 2 + 18 * 2


def test_gate_exits_zero_on_a_real_verdict(
    book: Seeded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    llm_env: None,
    amendment_5_runner_ready: None,
) -> None:
    """越过地板门之后的裁决（这里是 INCONCLUSIVE）退出码 0。

    **把 KILL / INCONCLUSIVE 判成「命令失败」等于说「结论不合我意就是出错」**；
    而 INVALID 退 1，因为那一档的含义是「仪器坏了，这一轮没产出任何证据」。
    """
    # 第一条陷阱的 X0 泄漏、其余全干净 → x0_knows_leak = 0.5，落在 [0.50, 0.90] 内。
    texts = [KNOWS_LEAK] * 3 + [CLEAN] * 15
    _stub_complete(monkeypatch, texts)
    gt = _write_ground_truth(
        tmp_path / "gt.json", book.pid, [_row("K01", "KNOWS"), _row("K02", "KNOWS")]
    )
    out = tmp_path / "one.jsonl"

    code, captured = _gate_invoke(
        [
            "gate",
            "--db", str(book.path),
            "-p", book.pid,
            "--ground-truth", str(gt),
            "--out", str(out),
        ],
    )

    assert code == 0, captured
    assert "INCONCLUSIVE" in captured
    assert "判别对" in captured


def test_gate_refuses_a_ground_truth_from_another_project(
    book: Seeded, tmp_path: Path, llm_env: None, amendment_5_runner_ready: None
) -> None:
    """拿 A 书的 ground truth 跑 B 书的库：约束照样算得出来，只是算的不是这些陷阱瞄的那些。

    整轮数字会看起来完全正常，而它们不说明任何事——正是「静默的零 vs 真的零」那一类。
    """
    gt = _write_ground_truth(tmp_path / "gt.json", "project:别的书:01J0", [_row("K01", "KNOWS")])

    code, captured = _gate_invoke(
        ["gate", "--db", str(book.path), "-p", book.pid, "--ground-truth", str(gt)],
    )

    assert code != 0
    assert "别的书" in captured


def test_gate_refuses_an_illegal_repeats(
    book: Seeded, tmp_path: Path, llm_env: None, amendment_5_runner_ready: None
) -> None:
    gt = _write_ground_truth(tmp_path / "gt.json", book.pid, [_row("K01", "KNOWS")])

    code, captured = _gate_invoke(
        [
            "gate",
            "--db", str(book.path),
            "-p", book.pid,
            "--ground-truth", str(gt),
            "--repeats", "4",
        ],
    )

    assert code != 0
    assert "重复" in captured and "Traceback" not in captured


def test_gate_refuses_a_missing_ground_truth(
    book: Seeded, tmp_path: Path, amendment_5_runner_ready: None
) -> None:
    code, captured = _gate_invoke(
        [
            "gate",
            "--db", str(book.path),
            "-p", book.pid,
            "--ground-truth", str(tmp_path / "nope.json"),
        ],
    )

    assert code != 0
    assert "不存在" in captured
