"""泄漏检测（集合判断）+ secret_surfaces 的单测。

核心不变式：**内容 tell（「玄血蛊」）算泄漏，显示名标签（「血脉秘密」）不算**——后者是
进 prompt 的合法标签，把它算成泄漏就是 echo 假阳性，会让整个 KNOWS 维度不可信。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from novel_harness.eval.leak import score_against, score_draft
from novel_harness.graph import (
    AliasHit,
    AliasKind,
    Node,
    NodeLabel,
    NodeProps,
    NodeRef,
    Resolution,
)
from novel_harness.panel.constraints import (
    ForbiddenEntity,
    SceneConstraints,
    UnresolvedCast,
    secret_surfaces,
)

PID = "project:demo:01J0"


def _node(node_id: str, label: NodeLabel, name: str, **props: object) -> Node:
    return Node(id=node_id, project_id=PID, label=label, name=name, props=NodeProps(**props))


BLOODLINE = _node("secret:demo:01S1", NodeLabel.SECRET, "血脉秘密")
XUANTIE = _node("secret:demo:01S2", NodeLabel.SECRET, "玄铁令下落")
YOUQUAN = _node("location:demo:01L1", NodeLabel.LOCATION, "幽泉窟", first_appears_chapter=10)


class _Store:
    """只实现 `resolve` 的最小 fake：secret_surfaces / score_against 只碰这一个方法。

    存别名时**保留 kind**（真实 store 才分得清 canonical 显示名和内容 tell）——
    test_knowledge 的 FakeGraph 把一切 hit 都写成 CANONICAL，测不了这条排除逻辑。
    """

    def __init__(self, aliases: list[tuple[str, Node, AliasKind, bool]]) -> None:
        self._aliases = aliases

    def resolve(
        self,
        project_id: str,
        surfaces: Sequence[str] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        del project_id
        by_surface: dict[str, list[AliasHit]] = {}
        for surface, node, kind, usable in self._aliases:
            by_surface.setdefault(surface, []).append(
                AliasHit(node=node, kind=kind, usable_for_rules=usable)
            )
        if surfaces is None:
            items = sorted(by_surface.items(), key=lambda kv: len(kv[0]), reverse=True)
            res = [Resolution(surface=s, hits=h) for s, h in items]
        else:
            res = [Resolution(surface=s, hits=by_surface.get(s, [])) for s in surfaces]
        return [r for r in res if r.usable_for_rules] if rules_only else res


ALIASES: list[tuple[str, Node, AliasKind, bool]] = [
    ("血脉秘密", BLOODLINE, AliasKind.CANONICAL, True),
    ("玄血蛊", BLOODLINE, AliasKind.ALIAS, True),
    ("玄铁令下落", XUANTIE, AliasKind.CANONICAL, True),
    ("沉舟渡", XUANTIE, AliasKind.ALIAS, True),
    ("幽泉窟", YOUQUAN, AliasKind.CANONICAL, True),
]


def _con(
    chapter: int,
    must_not_reveal: list[NodeRef],
    forbidden: list[ForbiddenEntity],
) -> SceneConstraints:
    return SceneConstraints(
        chapter=chapter,
        unresolved_cast=[],
        must_not_reveal=must_not_reveal,
        forbidden_entities=forbidden,
    )


# ── secret_surfaces：只收内容 tell，排除显示名 ──────────────────────────────


def test_secret_surfaces_excludes_the_display_label() -> None:
    out = secret_surfaces(_Store(ALIASES), PID, [NodeRef.of(BLOODLINE), NodeRef.of(XUANTIE)])
    assert out == {BLOODLINE.id: ["玄血蛊"], XUANTIE.id: ["沉舟渡"]}


def test_secret_surfaces_empty_without_secrets() -> None:
    assert secret_surfaces(_Store(ALIASES), PID, []) == {}


def test_secret_surfaces_skips_unusable_tell() -> None:
    """1 字别名（usable_for_rules=False）不能拿去匹配正文——满篇误报（ADR 0004）。"""
    aliases = [*ALIASES, ("蛊", BLOODLINE, AliasKind.ALIAS, False)]
    assert secret_surfaces(_Store(aliases), PID, [NodeRef.of(BLOODLINE)]) == {
        BLOODLINE.id: ["玄血蛊"]
    }


# ── leak：内容 tell 算泄漏，标签不算 ────────────────────────────────────────


def test_knows_tell_in_draft_is_a_leak() -> None:
    res = score_against(_Store(ALIASES), PID, _con(5, [NodeRef.of(BLOODLINE)], []), "他压低声音提起了玄血蛊的来历。")
    assert res.knows_violation is True
    assert res.knows_tokens == ["玄血蛊"]
    assert res.future_leak is False


def test_display_label_in_draft_is_not_a_leak() -> None:
    """prompt 里合法出现的标签「血脉秘密」不算泄漏——否则就是 echo 假阳性。"""
    res = score_against(_Store(ALIASES), PID, _con(5, [NodeRef.of(BLOODLINE)], []), "她隐约觉得这血脉秘密没那么简单。")
    assert res.knows_violation is False
    assert res.knows_tokens == []


def test_future_entity_name_is_a_floor_leak() -> None:
    forbidden = [ForbiddenEntity(node=NodeRef.of(YOUQUAN), first_appears_chapter=10, surfaces=["幽泉窟"])]
    res = score_against(_Store(ALIASES), PID, _con(5, [], forbidden), "风声里似乎藏着幽泉窟的方向。")
    assert res.future_leak is True
    assert res.future_tokens == ["幽泉窟"]
    assert res.knows_violation is False


def test_clean_draft_leaks_nothing() -> None:
    forbidden = [ForbiddenEntity(node=NodeRef.of(YOUQUAN), first_appears_chapter=10, surfaces=["幽泉窟"])]
    res = score_against(_Store(ALIASES), PID, _con(5, [NodeRef.of(BLOODLINE)], forbidden), "两人只是寻常寒暄，谁也没多说一句。")
    assert res.knows_violation is False
    assert res.future_leak is False
    assert res.knows_tokens == [] and res.future_tokens == []


def test_multiple_knows_tells_all_reported() -> None:
    con = _con(5, [NodeRef.of(BLOODLINE), NodeRef.of(XUANTIE)], [])
    res = score_against(_Store(ALIASES), PID, con, "他既知玄血蛊，又晓得沉舟渡的方位。")
    assert res.knows_violation is True
    assert res.knows_tokens == ["沉舟渡", "玄血蛊"]  # sorted()


def test_empty_draft_leaks_nothing() -> None:
    res = score_against(_Store(ALIASES), PID, _con(5, [NodeRef.of(BLOODLINE)], []), "")
    assert res.knows_violation is False and res.future_leak is False


def test_scoring_never_calls_semantic_paths() -> None:
    """健全性：一段**用别的话**说破秘密、但不含 tell 的草稿，检测器**看不出来**——
    这不是 bug，是设计边界。集合判断够不着改写（ADR 0005），所以合成小册子才要用唯一
    专名 tell 把「说破」变成一次可判定的集合命中。这条 pin 住那个诚实的假阴性。"""
    res = score_against(_Store(ALIASES), PID, _con(5, [NodeRef.of(BLOODLINE)], []), "他其实是魔尊之子，这事没人知道。")
    assert res.knows_violation is False  # 没写 tell「玄血蛊」→ 集合判断判它安全


def test_score_draft_rejects_an_empty_cast() -> None:
    """空 cast 会安静穿过 `require_resolved_cast()`——判分侧必须自己拦。

    `ResolvedCast.complete` 是 `not unresolved and bool(ids)`，也就是说退化有两条路径，
    而那道断言只读 `unresolved_cast`、只看得见第一条。第二条（作者没写 `cast=`，
    `Scene.cast` 默认 `[]`）产出的约束是「全部秘密」，拿它判泄漏等于给这一臂换了一份
    更严的卷子，kill-gate 的臂间比较当场失效。

    起草侧同一个洞由 `draft/context.py` 的 `ResolvedConstraints` 堵（`cast` 的
    `min_length=1`）。**两侧必须同时堵**：只堵一侧，判分器和起草器就会对同一个场景
    算出不同的禁忌集，而那正是「判分器 == Validator」要防的事。
    """
    with pytest.raises(UnresolvedCast, match="没有声明在场角色"):
        score_draft(_Store({}), "p", 152, [], "随便一段草稿")
