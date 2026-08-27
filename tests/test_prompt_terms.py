"""国际化第二批：`prompt_terms.py` 的双语词表——穷举，漏译当场红。

判据只有一条：`PromptTerm` 的每一个成员，`term()` 在 `DraftLanguage` 的每一个值上
都必须给出一个非空字符串。新增一个词条却忘了给某一侧翻译，`term()` 在那一侧
直接 `KeyError`——这条测试逐一遍历 `PromptTerm`，红在这份表上，不是红在某个
作者的英文书里。
"""

from __future__ import annotations

import pytest

from novel_harness.draft.length import DraftLanguage
from novel_harness.draft.prompt_terms import PromptTerm, term


@pytest.mark.parametrize("key", list(PromptTerm))
def test_every_term_has_a_non_empty_value_in_both_languages(key: PromptTerm) -> None:
    for language in DraftLanguage:
        assert term(key, language).strip(), (key, language)


@pytest.mark.parametrize("key", list(PromptTerm))
def test_zh_and_en_are_never_the_same_literal(key: PromptTerm) -> None:
    """两侧撞成同一个字面量大概率是漏翻译（复制粘贴漏改），不是巧合。"""
    assert term(key, DraftLanguage.ZH) != term(key, DraftLanguage.EN), key


def test_looking_up_an_unregistered_key_fails_loudly() -> None:
    """`term()` 缺一侧翻译时**必须报错**，不许静默退回另一种语言——那种退回会把
    「漏译」伪装成「这本书看起来是中文」。"""
    with pytest.raises(KeyError):
        term(PromptTerm.PRIOR_TEXT, "fr")  # type: ignore[arg-type]
