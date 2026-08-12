"""Product prompts add Canon memory without changing the frozen kill-gate assembler."""

from __future__ import annotations

from novel_harness.draft.assemble import GATE_TAIL_CODE_POINTS, PromptForm, assemble
from novel_harness.draft.context import ResolvedConstraints
from novel_harness.draft.length import DraftLanguage, LengthSpec
from novel_harness.events import CharacterProfileView, EventView, StoryEvent
from novel_harness.graph import (
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    GraphVersion,
    InformationScope,
    KnowledgeMatrix,
    NodeLabel,
    NodeRef,
)


PID = "project:memory"
ALICE = NodeRef(id="character:private-alice", label=NodeLabel.CHARACTER, name="顾清音")
BOB = NodeRef(id="character:private-bob", label=NodeLabel.CHARACTER, name="萧决")
LENGTH = LengthSpec(
    language=DraftLanguage.ZH,
    min_units=100,
    target_units=120,
    max_units=150,
)


def _constraints() -> ResolvedConstraints:
    return ResolvedConstraints(
        chapter=12,
        cast=[ALICE.name, BOB.name],
        matrix=KnowledgeMatrix(
            project_id=PID,
            chapter=12,
            scope=InformationScope.CANON,
            version=GraphVersion(),
            characters=[ALICE, BOB],
            secrets=[],
            cells=[],
        ),
    )


def _event(event_id: str, chapter: int, summary: str) -> EventView:
    return EventView(
        event=StoryEvent(
            id=event_id,
            project_id=PID,
            chapter_number=chapter,
            summary=summary,
            information_scope=InformationScope.CANON,
            status=EdgeStatus.ACTIVE,
            confidence=0.9,
            source=EdgeSource.EXTRACTOR,
            evidence_id="evidence:private-anchor",
            evidence_status=EvidenceStatus.FRESH,
        ),
        participants=[ALICE],
        knowers=[ALICE, BOB],
    )


def _memory():
    from novel_harness.draft.product_context import ResolvedProductContext

    return ResolvedProductContext(
        recent_from_chapter=1,
        cast=(ALICE, BOB),
        profiles=(
            CharacterProfileView(
                character=ALICE,
                personality="外冷内热",
                background="曾守过北境",
            ),
            CharacterProfileView(character=BOB, character_notes="右手有旧伤"),
        ),
        recent_events=(_event("event:private-recent", 11, "两人共同烧毁密信。"),),
        background_events=(_event("event:private-old", 2, "顾清音曾救过萧决。"),),
    )


def test_product_assembler_prepends_narrow_canon_memory() -> None:
    from novel_harness.draft.product_assemble import assemble_product

    memory = _memory()
    product = assemble_product(
        _constraints(),
        memory,
        form=PromptForm.X1,
        goal="两人在渡口商量下一步。",
        length=LENGTH,
    )
    plain = assemble(
        _constraints(),
        form=PromptForm.X1,
        goal="两人在渡口商量下一步。",
        length=LENGTH,
    )

    # `[文风][记忆][用户]`（ADR 0019 边界六）：跨章不变的那块在最前面，逐章变的插在它后面。
    assert [product[0], *product[2:]] == plain
    assert product[0] == plain[0], "文风段必须是逐字节的第一条 —— 它是唯一能被前缀缓存的那块"
    assert product[1]["role"] == "system"
    memory_text = product[1]["content"]
    assert memory_text.startswith("已确认的故事记忆")
    for expected in ("顾清音", "外冷内热", "曾守过北境", "右手有旧伤", "两人共同烧毁密信", "顾清音曾救过萧决"):
        assert expected in memory_text
    for forbidden in ("character:private", "event:private", "evidence:private", "PROVISIONAL"):
        assert forbidden not in memory_text


def test_product_assembler_renders_rolling_summaries_as_background_only() -> None:
    from novel_harness.draft.product_assemble import assemble_product
    from novel_harness.draft.product_context import ResolvedProductContext, RollingSummaryView

    memory = ResolvedProductContext(
        recent_from_chapter=1,
        cast=(ALICE, BOB),
        profiles=(),
        recent_events=(),
        background_events=(),
        rolling_summaries=(
            RollingSummaryView(chapter_number=3, summary="第三章：顾清音救下萧决。"),
            RollingSummaryView(chapter_number=4, summary="第四章：两人结盟北上。"),
        ),
    )
    product = assemble_product(
        _constraints(),
        memory,
        form=PromptForm.X1,
        goal="两人在渡口商量下一步。",
        length=LENGTH,
    )
    memory_text = product[1]["content"]
    assert "【更早章节滚动总结】" in memory_text
    assert "第三章：顾清音救下萧决。" in memory_text
    assert "第四章：两人结盟北上。" in memory_text
    # 机器摘要必须自报「未经作者确认」，不能伪装成已确认事实。
    assert "未经作者确认" in memory_text


def test_kill_gate_forms_never_receive_product_memory() -> None:
    ctx = _constraints()
    sentinels = ("外冷内热", "曾守过北境", "右手有旧伤", "两人共同烧毁密信")

    for form in PromptForm:
        rendered = "\n".join(
            message["content"]
            for message in assemble(ctx, form=form, goal="继续交谈。", length=LENGTH)
        )
        assert "已确认的故事记忆" not in rendered
        assert all(sentinel not in rendered for sentinel in sentinels)


def test_product_assembler_calls_the_existing_assembler_unchanged(monkeypatch) -> None:
    import novel_harness.draft.product_assemble as product_module

    observed: list[tuple[object, dict[str, object]]] = []
    # 形状照 `assemble()` 的真出参（一条 system + 一条 user）：假实现比真实现窄的时候，
    # 「记忆插在哪一格」这条断言就量不到东西了（`test_agent_index.py::FakeEvents` 栽过这个）。
    base_messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "user"},
    ]

    def fake_assemble(ctx, **kwargs):
        observed.append((ctx, kwargs))
        return base_messages

    monkeypatch.setattr(product_module, "assemble", fake_assemble)
    ctx = _constraints()
    result = product_module.assemble_product(
        ctx,
        _memory(),
        form=PromptForm.X2,
        goal="继续交谈。",
        length=LENGTH,
        previous_tail="上文",
        house_style="自定义文风",
    )

    assert observed == [
        (
            ctx,
            {
                "form": PromptForm.X2,
                "goal": "继续交谈。",
                "length": LENGTH,
                "previous_tail": "上文",
                # 透传，且默认值仍是三臂那个冻结值——放大它的决定在 `/draft`，不在这一层。
                "previous_tail_limit": GATE_TAIL_CODE_POINTS,
                "house_style": "自定义文风",
            },
        )
    ]
    # 记忆插在前导 system 段之后：文风原样在第 0 格，用户消息原样在最后。
    assert [result[0], result[2]] == base_messages
    assert result[1]["content"].startswith("已确认的故事记忆")


def test_the_gate_never_reaches_this_module() -> None:
    """**边界六那次换序的证据，而不是它的说明文字。**

    ADR 0019 边界六写着「纯换顺序，`assemble()` 一个字不动，三臂不受影响」。
    那句话和代码一样会过期，所以这里量的是代码：

    1. **`eval/` 和 `cli.py` 里 `product_assemble` 三个字都不出现** —— kill-gate 的三臂
       （`eval/runner.py`）和它的离线重算器（`eval/evidence.py`）直接 import `assemble`；
    2. **整个 `src/` 里 `assemble_product` 只有一个调用方** —— `draft/product_draft.py`
       里那个「章号 → 一稿正文」的函数，而它只在 `PRODUCT` 那一支走这条路
       （点名 X0/X1/X2 的请求原样走 `assemble()`）。
       **2026-08-11 之前那个调用方是 `api/app.py` 的 `/draft` 路由体**；那 120 行被提到
       `draft/` 里，是因为 agent 的起草工具要调同一个函数（3.6 / ADR 0021）——
       **提出来之后调用方仍然只有一个，这条断言的全部重量就在这个「一个」上**。

    判据是 AST 的 import 图不是 grep：那几个名字在注释和 docstring 里也有
    （本文件自己就写了好几遍）。
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

    def imported_names(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                found.add(node.module or "")
                found |= {alias.name for alias in node.names}
            elif isinstance(node, ast.Import):
                found |= {alias.name for alias in node.names}
        return found

    gate_sources = sorted((src / "eval").glob("*.py")) + [src / "cli.py"]
    assert len(gate_sources) >= 5, "扫到的判分链文件太少 —— glob 坏了，这条会永远绿"
    polluted = {
        path.name: sorted(n for n in imported_names(path) if "product_assemble" in n)
        for path in gate_sources
        if any("product_assemble" in n for n in imported_names(path))
    }
    assert not polluted, (
        f"判分链 import 了产品装配器：{polluted}\n"
        "三臂必须走 `assemble()`——记忆前言进了臂里就是改考卷（EVAL_PROTOCOL §2 冻结）。"
    )

    callers = {
        path.relative_to(src).as_posix()
        for path in src.rglob("*.py")
        if "assemble_product" in imported_names(path)
    }
    assert callers == {"draft/product_draft.py"}, (
        f"`assemble_product` 的调用方变了：{sorted(callers)}\n"
        "多一个调用方就要重新回答一次「三臂受不受影响」——上面那条换序的全部安全性押在这儿。"
    )
