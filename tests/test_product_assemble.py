"""Product prompts add Canon memory without changing the frozen kill-gate assembler."""

from __future__ import annotations

import pytest

from novel_harness.draft.assemble import (
    GATE_TAIL_CODE_POINTS,
    WRITE_RULE_FORBIDDEN_HINTS,
    assemble,
)
from novel_harness.draft.context import ResolvedConstraints
from novel_harness.draft.length import DraftLanguage, LengthSpec
from novel_harness.events import CharacterProfileView, EventView, StoryEvent
from novel_harness.graph import (
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
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
        characters=[ALICE, BOB],
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
        goal="两人在渡口商量下一步。",
        length=LENGTH,
    )
    plain = assemble(
        _constraints(),
        goal="两人在渡口商量下一步。",
        length=LENGTH,
    )

    # `[文风][记忆][用户]`（ADR 0019 边界六）：跨章不变的那块在最前面，逐章变的插在它后面。
    assert [product[0], *product[2:]] == plain
    assert product[0] == plain[0], "文风段必须是逐字节的第一条 —— 它是唯一能被前缀缓存的那块"
    assert product[1]["role"] == "system"
    memory_text = product[1]["content"]
    assert memory_text.startswith("已生效的故事记忆")
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
        goal="两人在渡口商量下一步。",
        length=LENGTH,
    )
    memory_text = product[1]["content"]
    assert "【更早章节滚动总结】" in memory_text
    assert "第三章：顾清音救下萧决。" in memory_text
    assert "第四章：两人结盟北上。" in memory_text


def _two_scenes_sharing_one_backlog() -> tuple[str, str]:
    """同一本书的两场戏：滚动总结那一大块完全相同，在场和近期事件各不相同。

    这就是「模型一轮里连起五稿」的形状——五章共用同一批更早的总结，各自的在场名单不同。
    """
    from novel_harness.draft.product_assemble import render_product_memory
    from novel_harness.draft.product_context import ResolvedProductContext, RollingSummaryView

    # 长度比照真书：单章总结上限 120 字，几十章加起来是这段 prompt 里最大的一块。
    backlog = tuple(
        RollingSummaryView(
            chapter_number=n,
            summary=f"第 {n} 章：两人在北境查案，线索指向同一枚旧印。" * 3,
        )
        for n in range(3, 43)
    )

    def scene(profiles: tuple[CharacterProfileView, ...], recent: tuple[EventView, ...]) -> str:
        return render_product_memory(
            ResolvedProductContext(
                recent_from_chapter=43,
                cast=(ALICE, BOB),
                profiles=profiles,
                recent_events=recent,
                background_events=(),
                rolling_summaries=backlog,
            )
        )

    first = scene(
        (CharacterProfileView(character=ALICE, personality="外冷内热"),),
        (_event("event:private-a", 44, "顾清音独自守夜。"),),
    )
    second = scene(
        (
            CharacterProfileView(character=BOB, character_notes="右手有旧伤"),
            CharacterProfileView(character=ALICE, personality="外冷内热"),
        ),
        (_event("event:private-b", 45, "萧决换了佩刀。"),),
    )
    return first, second


def test_the_memory_preamble_puts_the_cross_chapter_stable_block_first() -> None:
    """一轮里连起五稿时，**共同前缀要盖住整块滚动总结**（ADR 0019 边界六）。

    这条不钉措辞，钉的是那个会花钱的性质：前缀缓存只认前缀，所以「每场都变的那几十个字」
    排在「跨章逐字不变的那几千字」前面 = 后面全废。2026-08-13 在作者 722 章真书上量到的
    就是这个形态——相邻两章共同前缀 4.9%，起草那一档五次调用缓存命中全是 0，而同一轮里
    对话那一档（追加式消息表）命中 97.5%。

    **不许拿「都在 prompt 里」搪塞**：摆得下和摆对了是两件事，这一条量的是后者。
    """
    first, second = _two_scenes_sharing_one_backlog()
    assert first != second, "两场戏渲染出同一段字，这条测试就量不到任何东西了"

    shared = 0
    while shared < min(len(first), len(second)) and first[shared] == second[shared]:
        shared += 1

    assert "第 42 章：" in first[:shared], (
        "共同前缀没盖住最后一条滚动总结 —— 有每场都变的东西排到它前面去了。"
        f"（共同前缀 {shared} / 全长 {len(first)}）"
    )
    assert shared / len(first) > 0.8, (
        f"共同前缀只有 {shared / len(first):.1%}，缓存基本用不上。"
        "块序的判据只有一条：这个东西换一场戏会不会变，会变就往后排。"
    )


def test_the_bare_assembler_never_receives_product_memory() -> None:
    """裸 `assemble()` 一个字的记忆前言都不带 —— 那是 `assemble_product()` 外面那一层的事。

    （2026-08-25 之前这条是 `for form in PromptForm` 三臂各跑一遍。三臂删了，
    留下的这一条量的是同一件事：两层的职责不许糊在一起。）
    """
    ctx = _constraints()
    sentinels = ("外冷内热", "曾守过北境", "右手有旧伤", "两人共同烧毁密信")

    rendered = "\n".join(
        message["content"] for message in assemble(ctx, goal="继续交谈。", length=LENGTH)
    )
    assert "已生效的故事记忆" not in rendered
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
        goal="继续交谈。",
        length=LENGTH,
        previous_tail="上文",
        write_rule="自定义文风",
    )

    assert observed == [
        (
            ctx,
            {
                "goal": "继续交谈。",
                "length": LENGTH,
                "previous_tail": "上文",
                # 透传，且默认值仍是那个短的地板值——放大它的决定在 `/draft`，不在这一层。
                "previous_tail_limit": GATE_TAIL_CODE_POINTS,
                "write_rule": "自定义文风",
            },
        )
    ]
    # 记忆插在前导 system 段之后：文风原样在第 0 格，用户消息原样在最后。
    assert [result[0], result[2]] == base_messages
    assert result[1]["content"].startswith("已生效的故事记忆")


def test_the_gate_never_reaches_this_module() -> None:
    """**边界六那次换序的证据，而不是它的说明文字。**

    ADR 0019 边界六写着「纯换顺序，`assemble()` 一个字不动，三臂不受影响」。
    那句话和代码一样会过期，所以这里量的是代码：

    1. **`eval/` 里 `product_assemble` 三个字不出现** —— kill-gate 的三臂
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

    # 判分链那一半（`eval/` 不许 import 产品装配器）随秘密下线一起删了（ADR 0039）：
    # 没有秘密就没有三臂，也就没有「记忆前言进了臂里 = 改考卷」这件事可防。
    # **留下的这一半跟考卷无关**：它问的是「换序那次的安全性今天还押在几个调用方上」。
    callers = {
        path.relative_to(src).as_posix()
        for path in src.rglob("*.py")
        if "assemble_product" in imported_names(path)
    }
    assert callers == {"draft/product_draft.py"}, (
        f"`assemble_product` 的调用方变了：{sorted(callers)}\n"
        "多一个调用方就要重新回答一次「三臂受不受影响」——上面那条换序的全部安全性押在这儿。"
    )



# ══════════════════════════════════════════════════════════════════════════
# 文风禁词网 —— 2026-08-26 从 `tests/test_draft_api.py` 搬过来
#
# 它们从前打的是 `POST /draft`；`write_rule` 那天从那个请求体上删了（只有整章那一支
# 读它，而整章的 HTTP 入口零调用方）。**网本身没删，也不能删**：作者的文风今天挂在
# 对话上（`agent/store.py::start_conversation`），由 `agent/drafting.py` 递进
# `ChapterDraftRequest`，起草工具每写一章都过一次这张网。
#
# ⚠️ **为什么测的是 `check_request` 不是 `assemble`。** 拒绝不在 `assemble` 里——
# 那个常量自己的 docstring 写着：「入口用它拒绝自定义写作规则；`assemble()` 本身
# **保持宽松**（测试要用自己的写作提示），中性由入口守」。往 `assemble` 上测这两条，
# 测出来的会是「它不拦」，那是它的设计不是它的 bug。
#
# 搬之前这两条是这张网全仓**唯一**的覆盖：`check_request` 一处直接测试都没有。
# ══════════════════════════════════════════════════════════════════════════


def _draft_request(write_rule: str) -> object:
    from novel_harness.draft.product_draft import ChapterDraftRequest

    return ChapterDraftRequest(goal="萧决看剑。", length=LENGTH, write_rule=write_rule)


@pytest.mark.parametrize("word", WRITE_RULE_FORBIDDEN_HINTS)
def test_a_write_rule_naming_an_engine_managed_word_is_refused(word: str) -> None:
    """这六个词是**引擎自己在管的事**，写进文风里只会和它打架。

    这是一张**关键词网**不是语义检查（ADR 0005）：抓得住顺手写出来的那一种，
    抓不住换个说法的那一种。参数直接取 `WRITE_RULE_FORBIDDEN_HINTS`——
    往那个元组里加词而不加覆盖，在这儿是不可能的。
    """
    from novel_harness.draft.product_draft import DraftRefused, check_request

    with pytest.raises(DraftRefused) as caught:
        check_request(_draft_request(f"写的时候不要{word}任何情节。"))
    assert word in str(caught.value)
    assert "引擎自己在管的事" in str(caught.value)


def test_an_ordinary_write_rule_passes() -> None:
    """**反面那一半**：网只拦那六个词，不拦文风本身。

    少了这一条，「把 `check_request` 改成一律拒绝」也能让上面那条绿。
    """
    from novel_harness.draft.product_draft import check_request

    check_request(_draft_request("文白夹杂，多用短句，对白简洁。"))  # 不抛就是通过
    check_request(_draft_request(""))
