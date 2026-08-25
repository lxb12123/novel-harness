"""读路径上的那一道 scope 闸 —— **这一层唯一的安全断言**。

── 它为什么有自己的文件（2026-08-24）────────────────────────────────────

它原来住在 `panel/knowledge.py` 里，那份 docstring 写着「住在头牌这个文件里而不是
某个 `panel/_util.py`，是因为它是这一层唯一的安全断言，该被读到」。

**那个理由今天反过来咬了一口**：秘密下线时 `panel/knowledge.py` 整份要删，
而这道闸跟秘密无关——它挡的是 `PLANNED` / `REJECTED` 进读路径，
`panel/state.py`（人物卡）照旧要用。跟着头牌一起删 = **PLANNED 泄漏防线从两层
变一层**，而且没有任何东西会红。

所以给它一个自己的文件：**理由没变（该被读到），只是不再挂在一个会死的东西上。**
"""

from __future__ import annotations

from ..graph import QUERYABLE_SCOPES, InformationScope


def require_queryable_scope(scope: InformationScope) -> None:
    """PLANNED / REJECTED 挡在读路径外（改 7 的「下沉为 filter」）。

    store 侧也有这一条，这里**不是**多余的重复：这个函数是 API 路由的直接入口，
    scope 会从 HTTP 查询参数上来。改 7 的原话是「泄漏在物理上不可能发生，而不是
    大概率不会发生」——一个把硬约束只放在一层的系统，靠的是那一层没 bug。
    PLANNED 泄漏进 Writer prompt 的代价是「未来剧情泄漏」，那正是这个产品声称
    结构上恒为 0 的东西。
    """
    if scope not in QUERYABLE_SCOPES:
        raise ValueError(
            f"scope={scope} 不可读。只有 {sorted(s.value for s in QUERYABLE_SCOPES)} 可查："
            "PLANNED 永不进 Writer prompt，REJECTED 只是防重抽的坟场"
        )
