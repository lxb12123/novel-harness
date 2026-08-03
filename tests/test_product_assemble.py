"""Product prompts add Canon memory without changing the frozen kill-gate assembler."""

from __future__ import annotations

from novel_harness.draft.assemble import PromptForm, assemble
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

    assert product[1:] == plain
    assert product[0]["role"] == "system"
    memory_text = product[0]["content"]
    assert memory_text.startswith("已确认的故事记忆")
    for expected in ("顾清音", "外冷内热", "曾守过北境", "右手有旧伤", "两人共同烧毁密信", "顾清音曾救过萧决"):
        assert expected in memory_text
    for forbidden in ("character:private", "event:private", "evidence:private", "PROVISIONAL"):
        assert forbidden not in memory_text


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
    base_messages = [{"role": "system", "content": "base"}]

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
                "house_style": "自定义文风",
            },
        )
    ]
    assert result[1:] == base_messages
