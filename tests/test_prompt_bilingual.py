"""国际化第二批：`assemble.py`/`product_assemble.py` 的框双语化，端到端验一遍。

三件事，每一件坏掉的样子都不一样：

1. **英文书拿到的 prompt 里一个中文字都没有。** 框翻了一半 = 半句中文夹在一份
   英文 prompt 里，模型大概率会被那半句带跑，而且没有任何东西会报错。
2. **中文书拿到的 prompt 跟改之前逐字节相同。** 这是这次改动最重要的回归线——
   `test_draft_assemble.py` / `test_draft_continuation.py` / `test_product_assemble.py`
   里那些钉死的精确字符串已经在护着这条，这里再从头到尾走一遍**整块**记忆前言
   （四个人物资料字段 + 带参与者的事件都填满），把 `_profile_line`/`_event_line`
   两处峰的分支也钉住——那两处不在上面几份文件的精确匹配范围内。
3. **真跑一次英文续写的 HTTP 路径**：项目语言选 en 之后，发给模型的 messages
   全程英文，不是只在 `assemble_continuation()` 这一层单测。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from test_summary_schedule import _wheel

from novel_harness.draft.assemble import continuation_goal
from novel_harness.draft.context import ResolvedConstraints
from novel_harness.draft.length import DraftLanguage, LengthSpec
from novel_harness.draft.product_assemble import (
    assemble_continuation,
    assemble_product,
    render_product_memory,
)
from novel_harness.draft.product_context import ResolvedProductContext, RollingSummaryView
from novel_harness.events import CharacterProfileView, EventView, StoryEvent
from novel_harness.graph import (
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeLabel,
    NodeRef,
)

_CJK = re.compile(
    "["
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\u3000-\u303f"  # CJK 标点（【】、。之类）
    "\uff00-\uffef"  # 全角形式（：；，！？之类）
    "]"
)
"""按码位写区间而不是抄字面量字符：抄错一个边界字符会让这条正则要么编译报错
（区间反了），要么静默漏掉一批字——都不是「测试红了就知道」的那种错。"""


def _no_cjk(text: str) -> bool:
    return _CJK.search(text) is None


ALICE = NodeRef(id="character:alice", label=NodeLabel.CHARACTER, name="顾清音")
BOB = NodeRef(id="character:bob", label=NodeLabel.CHARACTER, name="萧决")


def _event(chapter: int, summary: str) -> EventView:
    return EventView(
        event=StoryEvent(
            id=f"event:{chapter}",
            project_id="p",
            chapter_number=chapter,
            summary=summary,
            information_scope=InformationScope.CANON,
            status=EdgeStatus.ACTIVE,
            confidence=0.9,
            source=EdgeSource.EXTRACTOR,
            evidence_id="evidence:1",
            evidence_status=EvidenceStatus.FRESH,
        ),
        participants=[ALICE, BOB],
        knowers=[ALICE, BOB],
    )


def _full_memory() -> ResolvedProductContext:
    """人物资料四个字段全填、事件带参与者——逼 `_profile_line`/`_event_line` 走满每条分支。"""
    profile = CharacterProfileView(
        character=ALICE,
        gender="女",
        personality="外冷内热",
        background="曾守过北境",
        character_notes="随身带着一把短刀",
    )
    return ResolvedProductContext(
        recent_from_chapter=1,
        cast=(ALICE, BOB),
        profiles=(profile,),
        recent_events=(_event(11, "两人共同烧毁密信。"),),
        background_events=(),
        rolling_summaries=(RollingSummaryView(chapter_number=3, summary="顾清音救下萧决。"),),
    )


# ══════════════════════════════════════════════════════════════════════════
# 一、中文：整块记忆前言逐字节钉死（回归线）
# ══════════════════════════════════════════════════════════════════════════


def test_the_chinese_product_memory_is_unchanged_byte_for_byte() -> None:
    """这是国际化第二批最重要的那条：`_profile_line`/`_event_line` 走满四个字段和
    「涉及」分支之后，中文渲染必须和改表之前的字面量逐字节相同。"""
    text = render_product_memory(_full_memory(), DraftLanguage.ZH)

    assert text == (
        "已生效的故事记忆\n"
        "以下人物资料与事件是当前已生效的记忆（系统自动整理的部分只当线索，"
        "作者亲自确认过的才当既定事实）。\n"
        "\n"
        "【更早章节滚动总结】\n"
        "- 第 3 章：顾清音救下萧决。\n"
        "\n"
        "【更早的相关事件】\n"
        "- 暂无\n"
        "\n"
        "【近八章事件】\n"
        "- 第 11 章：两人共同烧毁密信。（涉及：顾清音、萧决）\n"
        "\n"
        "【在场人物资料】\n"
        "- 顾清音：性别：女；性格：外冷内热；背景：曾守过北境；备注：随身带着一把短刀"
    )


def test_the_chinese_continuation_prompt_is_unchanged_byte_for_byte() -> None:
    ctx = ResolvedConstraints(chapter=3, cast=["顾清音", "萧决"])
    spec = LengthSpec(language=DraftLanguage.ZH, min_units=80, target_units=150, max_units=300)
    summaries = (RollingSummaryView(chapter_number=1, summary="萧决推开门。"),)

    messages = assemble_continuation(
        ctx,
        summaries,
        goal=continuation_goal(DraftLanguage.ZH),
        length=spec,
        previous_tail="夜色沉下来。",
        following_text="他没有回头。",
    )

    assert messages[1]["content"] == "【更早章节滚动总结】\n- 第 1 章：萧决推开门。"
    assert messages[-1]["content"] == (
        "【上文】\n夜色沉下来。\n\n【在场】\n顾清音、萧决\n\n"
        "【这一场要写】\n顺着上文往下写，接住作者已经起的头，"
        "不要另起一段新情节。\n\n【下文】\n"
        "以下是这一章接下来已经写好的正文。不要重写它、不要改动它，"
        "你写的这一段要能自然接上它的开头。\n他没有回头。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 二、英文：一个中文字都不许出现
# ══════════════════════════════════════════════════════════════════════════


EN_SPEC = LengthSpec(language=DraftLanguage.EN, min_units=80, target_units=150, max_units=300)

# **英文书的人物名字本身也是英文**——`language` 只换标题/分隔符/标签这些框，不翻译
# 故事数据（名字、资料原文、事件原文）。混用中文人名去测「英文 prompt 里没有中文」
# 是在测一件语言参数管不到的事，会得到一个假红：分开定义两组人物是这条界线本身。
EN_ALICE = NodeRef(id="character:alice-en", label=NodeLabel.CHARACTER, name="Alice")
EN_BOB = NodeRef(id="character:bob-en", label=NodeLabel.CHARACTER, name="Bob")


def _en_event(chapter: int, summary: str) -> EventView:
    return EventView(
        event=StoryEvent(
            id=f"event:en-{chapter}",
            project_id="p",
            chapter_number=chapter,
            summary=summary,
            information_scope=InformationScope.CANON,
            status=EdgeStatus.ACTIVE,
            confidence=0.9,
            source=EdgeSource.EXTRACTOR,
            evidence_id="evidence:en-1",
            evidence_status=EvidenceStatus.FRESH,
        ),
        participants=[EN_ALICE, EN_BOB],
        knowers=[EN_ALICE, EN_BOB],
    )


def test_an_english_continuation_prompt_has_no_chinese_characters() -> None:
    ctx = ResolvedConstraints(chapter=3, cast=["Alice", "Bob"])
    summaries = (RollingSummaryView(chapter_number=1, summary="Alice opened the door."),)

    messages = assemble_continuation(
        ctx,
        summaries,
        goal=continuation_goal(DraftLanguage.EN),
        length=EN_SPEC,
        previous_tail="Night had fallen.",
        following_text="He did not look back.",
    )
    full_text = "\n".join(m["content"] for m in messages)

    assert _no_cjk(full_text), full_text
    assert "[Prior text]" in full_text
    assert "[Present]" in full_text
    assert "[This scene]" in full_text
    assert "[Following text]" in full_text
    assert "[Earlier chapter summaries]" in full_text


def test_an_english_product_memory_has_no_chinese_characters() -> None:
    profile = CharacterProfileView(
        character=EN_ALICE,
        gender="Female",
        personality="Cold outside, warm inside",
        background="Once guarded the northern frontier",
        character_notes="Carries a short blade",
    )
    memory = ResolvedProductContext(
        recent_from_chapter=1,
        cast=(EN_ALICE, EN_BOB),
        profiles=(profile,),
        recent_events=(_en_event(11, "They burned the secret letters together."),),
        background_events=(),
        rolling_summaries=(RollingSummaryView(chapter_number=3, summary="Alice saved Bob."),),
    )

    text = render_product_memory(memory, DraftLanguage.EN)

    assert _no_cjk(text), text
    assert "[Active story memory]" in text
    assert "[Character profiles]" in text
    assert "Gender: Female" in text
    assert "(involving Alice, Bob)" in text


def test_an_english_full_chapter_prompt_has_no_chinese_characters() -> None:
    ctx = ResolvedConstraints(chapter=12, cast=["Alice", "Bob"])
    profile = CharacterProfileView(character=EN_ALICE, personality="Cold outside, warm inside")
    memory = ResolvedProductContext(
        recent_from_chapter=1,
        cast=(EN_ALICE, EN_BOB),
        profiles=(profile,),
        recent_events=(_en_event(11, "They burned the secret letters together."),),
        background_events=(),
        rolling_summaries=(RollingSummaryView(chapter_number=3, summary="Alice saved Bob."),),
    )
    messages = assemble_product(
        ctx,
        memory,
        goal="They talk at the ferry crossing about their next move.",
        length=EN_SPEC,
    )
    full_text = "\n".join(m["content"] for m in messages)
    assert _no_cjk(full_text), full_text


# ══════════════════════════════════════════════════════════════════════════
# 三、真跑一次英文续写：项目语言选 en，HTTP 路径全程英文
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def wheel(tmp_path):
    return _wheel(tmp_path, 3)


@pytest.fixture
def client(wheel, tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NH_DB", wheel["db"])
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def test_an_actual_english_continuation_run_sends_an_all_english_prompt(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """项目语言真的切成 en 之后，`/draft`（续写）发出去的 messages 全程英文。

    这不是在测 `assemble_continuation()` 本身——上面两条已经测过——是测「书选了
    en」这件事真的从 `PATCH …/language` 一路传到发给模型的那份 prompt，
    不是半路被 `proj.language` 之外的什么东西悄悄扭回中文。
    """
    reply = client.put(
        "/api/settings",
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
            "context_window": 128_000,
        },
    )
    assert reply.status_code == 200, reply.text

    patched = client.patch(
        f"/api/projects/{wheel['pid']}/language", json={"language": "en"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["language"] == "en"

    import novel_harness.draft.generate as generate_mod
    from novel_harness.draft.provider import CompletionResult

    observed: list[list[dict[str, str]]] = []

    def fake(messages, *, config=None, plan=None, client=None) -> CompletionResult:
        observed.append(messages)
        return CompletionResult(
            text="Prose " * 80, model="fake", finish_reason="stop"
        )

    monkeypatch.setattr(generate_mod, "complete", fake)

    reply = client.post(
        f"/api/projects/{wheel['pid']}/chapters/3/draft",
        json={
            "previous_tail": "Night had fallen and no lamp was lit.",
            "length": {"min_units": 80, "target_units": 150, "max_units": 300},
        },
    )
    assert reply.status_code == 200, reply.text

    assert len(observed) == 1
    full_text = "\n".join(m["content"] for m in observed[0])
    assert _no_cjk(full_text), full_text
    assert "English" in full_text
    assert reply.json()["length"]["unit"] == "words"
