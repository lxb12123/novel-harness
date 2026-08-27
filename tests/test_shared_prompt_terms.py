"""国际化第三批：`prompt_terms.py`（顶层，不是 `agent/` 或 `draft/` 那两份同名文件）
的双语消息表——同一把尺，穷举，漏译当场红。

判据和 `agent/prompt_terms.py`（见 `tests/test_agent_tools.py`）、
`draft/prompt_terms.py`（见 `tests/test_prompt_terms.py`）完全一致：每一个键
两侧都必须非空，英文一侧不许有中文字符。这份表单独开一个文件是因为**它没有
唯一主消费者**——system_notifications.py / advisory_review.py /
panel/constraints.py / draft/context.py / draft/rolling_summary.py /
draft/windows.py / chapter_refresh.py / extract/runner.py / api/app.py
都在用它，放进其中任何一个的测试文件都会显得像是那个文件专属的。
"""

from __future__ import annotations

import re

import pytest

from novel_harness.draft.length import DraftLanguage
from novel_harness.prompt_terms import _MESSAGES, message

_CJK = re.compile("[一-鿿　-〿＀-￯]")


def test_every_message_has_both_languages_and_the_english_side_is_clean() -> None:
    for key, by_language in _MESSAGES.items():
        for language in (DraftLanguage.ZH, DraftLanguage.EN):
            template = by_language.get(language)
            assert template, f"_MESSAGES[{key!r}] 缺 {language!r} 这一侧，或者是空串"
        english = by_language[DraftLanguage.EN]
        assert _CJK.search(english) is None, (
            f"_MESSAGES[{key!r}] 的英文模板里还有中文字符：{english!r}"
        )


def test_looking_up_an_unregistered_language_fails_loudly() -> None:
    """`message()` 缺一侧翻译时必须 `KeyError`，不许静默退回另一种语言——
    那种退回会把「漏译」伪装成「这本书看起来是中文」。"""
    with pytest.raises(KeyError):
        message("chapter_number_at_least_one", "fr")  # type: ignore[arg-type]


def test_a_real_draftlanguage_instance_still_looks_up_correctly() -> None:
    """`_MESSAGES` 的键故意是字面量 `"zh"`/`"en"`（避免模块顶层导入
    `draft.length` 成环，见模块 docstring）。这条测试钉住它没有和真正的
    `DraftLanguage` 脱节——真的传一个 `DraftLanguage` 实例也查得到。"""
    assert message("chapter_number_at_least_one", DraftLanguage.ZH) == "章号至少是 1"
    assert (
        message("chapter_number_at_least_one", DraftLanguage.EN)
        == "Chapter number must be at least 1"
    )
