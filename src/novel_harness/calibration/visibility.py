"""fact type → agent/writer 可见性白名单（ADR 0033 §4.3）。

这两个派生规则是**后端封闭类型规则**：Agent 和 LLM 都不能填写。UnknownCast 下
的 Writer 白名单按字段判定（enum/key、NodeRef 显示名、布尔/纯数值、章号），
任意字符串 value/备注/关系/状态描述一律 HIDDEN —— 关键词扫描只作附加防御，
不负责把任意字符串变成 Writer-safe。
"""

from __future__ import annotations

from .models import AgentVisibility, FactType, WriterVisibility


def agent_visibility_of(fact_type: FactType) -> AgentVisibility:
    """Agent 报告里这条事实能露出多少。

    （KNOWS/BELIEVES 那两档随秘密下线删了，ADR 0039。）完整 PLANNED、
    Secret props/tell、完整秘密正文、任意 Node props 和不对称知识事件不得进入
    模型可见报告。
    """
    if fact_type is FactType.FORESHADOW:
        return AgentVisibility.SAFE_LABEL_ONLY
    return AgentVisibility.SAFE_FACT


def writer_visibility_of(
    fact_type: FactType,
    *,
    closed_value_key: bool = False,
    public_event: bool = False,
    fresh: bool = False,
) -> WriterVisibility:
    """Writer 简报里这条事实能露出多少。**宁可少给，不伪造「已经安全」。**

    Args:
        closed_value_key: HAS_STATE / RELATED_TO 的 value 是否挂在封闭机器键上。
            只有封闭键才能证明公开；任意字符串 value 即使来自 CANON 行也 HIDDEN。
        public_event: 事件能否对**完整安全 cast** 机械证明公开（见 product_context
            的 `_is_writer_safe` 同一条判据）。
        fresh: 章节总结是否与当前正文快照一致。
    """
    if fact_type is FactType.BODY_LIMITATION:
        return WriterVisibility.SAFE_LABEL_ONLY if closed_value_key else WriterVisibility.HIDDEN
    if fact_type is FactType.RELATIONSHIP_STAGE:
        return WriterVisibility.SAFE_LABEL_ONLY if closed_value_key else WriterVisibility.HIDDEN
    if fact_type is FactType.STATE:
        # 一般状态值/备注是任意字符串，UnknownCast 下默认不进 Writer。
        return WriterVisibility.SAFE_LABEL_ONLY if closed_value_key else WriterVisibility.HIDDEN
    if fact_type is FactType.EVENT:
        return WriterVisibility.SAFE_FACT if public_event else WriterVisibility.HIDDEN
    if fact_type is FactType.CHAPTER_SUMMARY:
        return WriterVisibility.SAFE_FACT if fresh else WriterVisibility.HIDDEN
    if fact_type is FactType.PROFILE:
        return WriterVisibility.HIDDEN
    if fact_type is FactType.FORESHADOW:
        return WriterVisibility.HIDDEN
    return WriterVisibility.SAFE_FACT
