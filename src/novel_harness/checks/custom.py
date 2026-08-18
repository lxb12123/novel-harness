"""确定性自定义验证规则（023 / Task 13）。

第一期只支持一条模板：`forbidden_literal` —— 作者写一段必须出现在正文里的字，
引擎逐段做**精确子串匹配**（不执行作者代码、不把自然语言交给 LLM、不上正则）。

── `required_literal` 为什么不做 ──────────────────────────────────────────
「这段字**没有**出现」在 `(para_index, quote_text, occurrence_k)` 模型里没有合法
锚：一个不存在的字符串没有「第几段第几次」可指。强行实现会破坏 ADR 0006 的
定位契约。所以只有「出现了才算命中」的模板。
"""

from __future__ import annotations

from typing import Any

from .base import CheckContext, Issue

__all__ = ["forbidden_literal_check", "custom_rule_spec"]


def forbidden_literal_check(literal: str, *, rule_id: str = "custom:forbidden_literal") -> Any:
    """`forbidden_literal` 模板的执行器：`literal` 出现在哪段就报哪段。

    返回一个 `check(ctx) -> list[Issue]` —— 规则 Catalog 的形状（RuleSpec.check），
    贡献者入口 `check(ctx: CheckContext) -> list[Issue]` 不破坏。
    """

    def check(ctx: CheckContext) -> list[Issue]:
        issues: list[Issue] = []
        if ctx.paragraphs is None:
            # 正文没装入 = unavailable，service 在 availability 阶段就拦住了；
            # 这里只是防御性返回空（不该被调用到）。
            return issues
        from ..graph import TextAnchor

        for para_index, para in enumerate(ctx.paragraphs):
            if literal not in para:
                continue
            occurrences = _occurrences(para, literal)
            for occurrence_k in range(len(occurrences)):
                issues.append(
                    Issue(
                        rule=f"custom:{rule_id}",
                        issue_type="CUSTOM_LITERAL_HIT",
                        chapter=ctx.chapter,
                        anchor=TextAnchor(
                            para_index=para_index,
                            quote_text=literal,
                            occurrence_k=occurrence_k,
                        ),
                        message=f"这段正文里出现了「{literal}」。",
                    )
                )
        return issues

    return check


def _occurrences(text: str, needle: str) -> list[int]:
    start = 0
    positions: list[int] = []
    while True:
        idx = text.find(needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def custom_rule_spec(
    *, rule_id: str, title: str, literal: str, blocks_downstream: bool
) -> Any:
    """把一条数据库里的确定性规则升格成 catalog 的 `RuleSpec`（供 service 用）。

    `literal` 来自 `config_json`（作者写的字），**不允许作者塞代码 / 正则 / 语义**。
    """
    from .catalog import RuleAvailability, RuleSpec

    return RuleSpec(
        rule_id=rule_id,
        title=title,
        description=f"正文某一段出现「{literal}」时命中。",
        blocks_downstream=blocks_downstream,
        enabled=True,
        schema_version="v1",
        template="forbidden_literal",
        config={"literal": literal},
        availability=lambda ctx: (
            RuleAvailability.AVAILABLE
            if ctx.paragraphs is not None
            else RuleAvailability.UNAVAILABLE
        ),
        check=forbidden_literal_check(literal, rule_id=rule_id),
    )
