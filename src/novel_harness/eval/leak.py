"""泄漏检测 —— 一段草稿有没有「说漏嘴」，纯集合判断（ADR 0005 / docs/EVAL_PROTOCOL.md §3）。

一段草稿**泄漏 iff** 它的正文用 `text.anchor.find_all`（精确、非重叠子串）命中了本场景的
某个禁忌 **tell**。没有 LLM、没有 NLP、没有第二份时态过滤——泄漏判定和头牌矩阵一样，
对文本不做任何语义断言，只对「作者声明过的那些禁忌称呼」做集合匹配。

禁忌集只经 `panel.constraints`：
- KNOWS（秘密）走 `secret_surfaces`（内容 tell，**排除**显示名标签）；
- FUTURE（未登场实体）走 `SceneConstraints.forbidden_entities[*].surfaces`。
本模块**不自己调 `store.resolve` / `knowledge_matrix`**——那会绕开闸门另立一份禁忌集，
让「判分器 == Validator」名存实亡。

⚠️ **这条纪律今天只靠 review 守着。** 本该钉死它的第 4 道 arch-guard
（`tests/test_draft_boundary.py`）**还没写**——这行注释此前宣称它「钉死」了，那是假的。
写 runner / `assemble.py` 的人别读到这儿就以为有守卫罩着：按本仓库自己的判据，
**一个不存在的守卫比一个永远绿的守卫更糟**，它还提供安全感。

两类分开记（`LeakResult.knows_violation` vs `future_leak`）：kill-gate 的裁决**由 KNOWS
主导**（paraphrase-hard、echo-immune、产品相关）；FUTURE_LEAK 只作描述性地板——因为
X1/X2 的 prompt 必然点了未来实体的名，存在 echo 风险（EVAL_PROTOCOL.md §5 caveat）。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..graph import StoryGraph
from ..panel.constraints import SceneConstraints, scene_constraints, secret_surfaces
from ..text import anchor


class LeakResult(BaseModel):
    """一段草稿对一个场景约束的打分。**per-kind 分开，这让 KNOWS 主导的裁决免费得到。**"""

    model_config = ConfigDict(frozen=True)

    chapter: int
    knows_violation: bool
    """在场角色此刻不该知道的秘密的**内容 tell** 出现在了草稿里。裁决由它主导。"""
    knows_tokens: list[str] = Field(default_factory=list)
    future_leak: bool
    """还没到首现章的实体名出现在了草稿里。**只作地板，不主导裁决**（echo 风险）。"""
    future_tokens: list[str] = Field(default_factory=list)


def _hits(paras: Sequence[str], tells: set[str]) -> list[str]:
    # 空 tell 没有有意义的「第 k 次出现」，anchor.find_all 会拒——这里先滤掉再匹配。
    return sorted({t for t in tells if t and anchor.find_all(paras, t)})


def score_against(
    store: StoryGraph,
    project_id: str,
    constraints: SceneConstraints,
    draft: str,
) -> LeakResult:
    """对一份**已经算好的**约束打分。kill-gate 的 runner 走这条：约束在 freeze 时定死，
    跑的时候不再重算，避免 `resolve()` 在 freeze 与 run 之间漂移。

    （此处原本引「EVAL_PROTOCOL.md §6 双打分器」——**协议里没有这一节**，§6 是预注册裁决表。
    协议根本没写过 freeze 语义，所以这条约束的落点是还没写的 runner：写它的人得自己守。）
    """
    paras = anchor.paragraphs(draft)
    knows_map = secret_surfaces(store, project_id, constraints.must_not_reveal)
    knows_tells = {t for surfaces in knows_map.values() for t in surfaces}
    future_tells = {s for e in constraints.forbidden_entities for s in e.surfaces}
    knows_hit = _hits(paras, knows_tells)
    future_hit = _hits(paras, future_tells)
    return LeakResult(
        chapter=constraints.chapter,
        knows_violation=bool(knows_hit),
        knows_tokens=knows_hit,
        future_leak=bool(future_hit),
        future_tokens=future_hit,
    )


def score_draft(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    cast: Sequence[str],
    draft: str,
    *,
    secrets: Sequence[str] | None = None,
) -> LeakResult:
    """从场景现算约束再打分（`cast` 是称呼原文，同 `scene_constraints`）。

    **先 `require_resolved_cast()`**：cast 有歧义时 must_not_reveal 退化成全部秘密
    （fail-closed）——拿退化约束去判泄漏会把「全禁」当基线，污染 kill-gate 的臂间比较，
    所以这里把歧义弹给作者，不静默用退化值。
    """
    constraints = scene_constraints(store, project_id, chapter, cast, secrets=secrets)
    constraints.require_resolved_cast()
    return score_against(store, project_id, constraints, draft)
