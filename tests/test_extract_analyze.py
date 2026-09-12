from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from novel_harness.extract.analyze import (
    AnalysisFormatError,
    ResolvedAnalysis,
    ResolutionContractError,
    SurfaceResolution,
    resolve_surfaces,
    parse_analysis,
)
from novel_harness.extract.models import RawChapterAnalysis
from novel_harness.extract.prompt import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    build_analysis_messages,
)
from novel_harness.graph import AliasHit, AliasKind, Node, NodeLabel, NodeRef, Resolution


def _hit(
    node_id: str,
    name: str,
    *,
    usable: bool = True,
    project_id: str = "project-1",
) -> AliasHit:
    return AliasHit(
        node=Node(
            id=node_id,
            project_id=project_id,
            label=NodeLabel.CHARACTER,
            name=name,
        ),
        kind=AliasKind.ALIAS,
        usable_for_rules=usable,
    )


class FakeStoryGraph:
    def __init__(self, by_surface: dict[str, list[AliasHit]]) -> None:
        self.by_surface = by_surface
        self.calls: list[tuple[str, tuple[str, ...], bool]] = []

    def resolve(
        self,
        project_id: str,
        surfaces: list[str] | tuple[str, ...] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        assert surfaces is not None
        self.calls.append((project_id, tuple(surfaces), rules_only))
        return [
            Resolution(surface=surface, hits=self.by_surface.get(surface, []))
            for surface in surfaces
        ]


class FixedResolutionGraph:
    def __init__(self, resolutions: list[Resolution]) -> None:
        self.resolutions = resolutions

    def resolve(
        self,
        project_id: str,
        surfaces: list[str] | tuple[str, ...] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        return self.resolutions


def _valid_json() -> str:
    return json.dumps(
        {
            "events": [
                {
                    "summary": "顾清音交出密信",
                    "quote": "顾清音从袖中取出密信，轻轻放在案上。",
                    "participants": ["顾清音"],
                    "knowers": ["顾清音", "萧决"],
                    "confidence": 0.95,
                }
            ],
            "state_updates": [],
            "character_profiles": [],
        },
        ensure_ascii=False,
    )


def test_a_markdown_fence_is_stripped_not_a_reason_to_throw_away_the_chapter() -> None:
    """```json 围栏剥掉，里面那份 JSON 照常解析。

    ⚠️ **这一档 2026-09-06 从「必须抛」翻成了「必须过」。** 它原来和「not json」
    「后面跟一句话」并列在 `never_repairs_or_strips` 那张参数表里。

    翻过来的理由是实测：真书上 73 次抽取失败里有 23 次是 `analysis_format`，
    而 `advisory_review._payload` 面对同一个模型**从一开始就剥围栏**（「模型爱加它」）。
    同一种毛病两个答案，严的那一侧整章作废——而围栏是**包装**，里面那份 JSON
    一个字节没变，剥它和「猜模型想说什么」是两回事。

    **别把这条读成「解析变宽容了」**：`not json` / 前后带散文 / 缺字段 / 多字段
    照旧全抛（上面那张表），一次修复、一次重试都没有。
    """
    parsed = parse_analysis("```json\n" + _valid_json() + "\n```")
    assert parsed.events[0].summary == "顾清音交出密信"
    # 光秃秃的 ``` 也是围栏（模型不总是写语言名）。
    assert parse_analysis("```\n" + _valid_json() + "\n```").events[0].summary == (
        "顾清音交出密信"
    )


def test_parse_analysis_validates_json_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    original = RawChapterAnalysis.model_validate_json
    calls: list[str] = []

    def tracked(cls: type[RawChapterAnalysis], text: str) -> RawChapterAnalysis:
        calls.append(text)
        return original(text)

    monkeypatch.setattr(RawChapterAnalysis, "model_validate_json", classmethod(tracked))

    parsed = parse_analysis(_valid_json())

    assert parsed.events[0].summary == "顾清音交出密信"
    assert calls == [_valid_json()], "没有围栏时 `unfenced` 不该改动一个字节"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        _valid_json() + "\nanalysis complete",
        '{"events": [}',
        '{"events": [], "chapter": 12}',
    ],
)
def test_parse_analysis_never_repairs_invalid_output(text: str) -> None:
    with pytest.raises(AnalysisFormatError) as exc_info:
        parse_analysis(text)

    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_analysis_prompt_is_versioned_deterministic_and_includes_exact_text() -> None:
    chapter = "第一段。\n\n第二段含有 {JSON-looking} 原文。"

    first = build_analysis_messages(chapter)
    second = build_analysis_messages(chapter)

    assert ANALYSIS_SCHEMA_VERSION
    assert ANALYSIS_PROMPT_VERSION
    assert first == second
    assert first[-1] == {"role": "user", "content": chapter}


def test_analysis_prompt_pins_safety_and_shape_rules() -> None:
    system = build_analysis_messages("正文")[0]["content"]

    assert "JSON only" in system
    assert "story-beat" in system
    assert "1-12" in system
    assert "verbatim" in system
    assert "120" in system
    assert "30%" in system
    assert "minimum quote length" in system
    assert "location" in system and "object" in system
    assert "state" in system and "dimension" in system and "value" in system
    assert "relationship" in system
    # 2026-08-04 真模型首跑回归：state update 的判别字段和 confidence 必须点名，
    # 否则模型会自创 "shape" 或漏掉 kind，整章被严格解析拒收。
    assert '"kind"' in system
    assert '"kind": "location"' in system
    assert "confidence" in system
    for forbidden in ("IDs", "chapter", "scope", "status"):
        assert forbidden in system
    assert "surface names" in system
    # 2026-09-04 v8：`state` 的四条口径。每一条丢掉都会**静默退回 v7 的行为**，
    # 而那个行为在屏幕上的形状是「角色卡只剩所在地 + 装备两行」/「同一件事两个维度
    # 并排打架」/「中英混排的维度名」——都不会让任何别的测试红。
    assert "language of the supplied chapter" in system
    assert "one stable dimension name per attribute" in system
    assert "cultivation level or power rank" in system
    assert "traits that do not change" in system


def test_resolve_surfaces_preserves_order_candidates_and_unknowns() -> None:
    unique = _hit("character-1", "顾清音")
    ambiguous = [_hit("character-2", "萧决"), _hit("character-3", "林渡")]
    graph = FakeStoryGraph({"顾姑娘": [unique], "师兄": ambiguous})

    result = resolve_surfaces(
        graph,  # type: ignore[arg-type]
        "project-1",
        ["顾姑娘", "师兄", "陌生人", "顾姑娘", "师兄"],
    )

    assert isinstance(result, ResolvedAnalysis)
    assert isinstance(result.resolutions, tuple)
    assert [item.surface for item in result.resolutions] == [
        "顾姑娘",
        "师兄",
        "陌生人",
    ]
    assert result.resolutions[0].unique_id == "character-1"
    assert [candidate.id for candidate in result.resolutions[0].candidates] == ["character-1"]
    assert result.resolutions[0].ambiguous is False
    assert result.resolutions[0].unknown is False

    assert result.resolutions[1].unique_id is None
    assert [candidate.id for candidate in result.resolutions[1].candidates] == [
        "character-2",
        "character-3",
    ]
    assert result.resolutions[1].ambiguous is True
    assert result.resolutions[1].unknown is False

    assert result.resolutions[2].unique_id is None
    assert result.resolutions[2].candidates == ()
    assert result.resolutions[2].ambiguous is False
    assert result.resolutions[2].unknown is True
    assert graph.calls == [("project-1", ("顾姑娘", "师兄", "陌生人"), False)]

    assert isinstance(result.resolutions[1].candidates, tuple)
    with pytest.raises(AttributeError):
        result.resolutions.append(result.resolutions[0])  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        result.resolutions[1].candidates.append(  # type: ignore[attr-defined]
            result.resolutions[1].candidates[0]
        )


def test_resolve_surfaces_uses_unique_unusable_alias_without_rules_filtering() -> None:
    graph = FakeStoryGraph({"小顾": [_hit("character-1", "顾清音", usable=False)]})

    result = resolve_surfaces(graph, "project-1", ["小顾"])  # type: ignore[arg-type]

    assert result.resolutions[0].unique_id == "character-1"
    assert result.resolutions[0].candidates[0].model_dump() == {
        "id": "character-1",
        "label": NodeLabel.CHARACTER,
        "name": "顾清音",
    }


@pytest.mark.parametrize(
    ("candidates", "unique_id"),
    [
        ((), "character-1"),
        ((NodeRef(id="character-1", label=NodeLabel.CHARACTER, name="顾清音"),), None),
        ((NodeRef(id="character-1", label=NodeLabel.CHARACTER, name="顾清音"),), "wrong"),
        (
            (
                NodeRef(id="character-1", label=NodeLabel.CHARACTER, name="顾清音"),
                NodeRef(id="character-2", label=NodeLabel.CHARACTER, name="萧决"),
            ),
            "character-1",
        ),
    ],
)
def test_surface_resolution_rejects_illegal_unique_id_states(
    candidates: tuple[NodeRef, ...], unique_id: str | None
) -> None:
    with pytest.raises(ValidationError, match="unique_id"):
        SurfaceResolution(
            surface="称呼",
            candidates=candidates,
            unique_id=unique_id,
        )


@pytest.mark.parametrize(
    "returned_surfaces",
    [
        ("顾姑娘",),
        ("顾姑娘", "师兄", "额外"),
        ("顾姑娘", "顾姑娘"),
        ("师兄", "顾姑娘"),
    ],
)
def test_resolve_surfaces_rejects_misaligned_graph_results(
    returned_surfaces: tuple[str, ...],
) -> None:
    graph = FixedResolutionGraph(
        [Resolution(surface=surface, hits=[]) for surface in returned_surfaces]
    )

    with pytest.raises(ResolutionContractError, match="StoryGraph.resolve"):
        resolve_surfaces(
            graph,  # type: ignore[arg-type]
            "project-1",
            ["顾姑娘", "师兄", "顾姑娘"],
        )


def test_resolve_surfaces_rejects_foreign_project_candidates() -> None:
    graph = FakeStoryGraph({"顾姑娘": [_hit("character-1", "顾清音", project_id="project-2")]})

    with pytest.raises(ResolutionContractError, match="project"):
        resolve_surfaces(graph, "project-1", ["顾姑娘"])  # type: ignore[arg-type]


def test_resolve_surfaces_rejects_duplicate_candidate_node_ids() -> None:
    duplicate = _hit("character-1", "顾清音")
    graph = FakeStoryGraph({"顾姑娘": [duplicate, duplicate]})

    with pytest.raises(ResolutionContractError, match="duplicate"):
        resolve_surfaces(graph, "project-1", ["顾姑娘"])  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════
# v9：把这本书已有的字段名喂回去（作者 2026-09-06 的裁定：不给固定字段表）
# ══════════════════════════════════════════════════════════════════════════


def test_known_dimension_names_go_into_the_messages_but_not_into_the_run_identity() -> None:
    """**这一条是 v9 的全部风险所在，别删。**

    「已有字段名」那一段随图变化（别的章一抽完，这本书就多几个名字）。它进消息是
    对的——那是收敛字段名的唯一机制。**但它绝不能进 `prompt_hash`**，因为那个哈希是
    运行身份，三处都建在它上面：

      · `extraction_run` 的唯一键（同一章同一个问题只跑一条 run）；
      · `runner.enqueue` 判「这条 run 还算不算数」；
      · `runner.run` 的 `PROMPT_DRIFT` 检查（排队时和执行时的 prompt 必须一致）。

    进去了的话三条同时发作：每加一个字段名就让同一章重新付一次钱；而排队到执行之间
    图一定会变（别的章在并行抽），于是**每一条 run 都死在 PROMPT_DRIFT**。
    """
    from novel_harness.extract.control import AnalysisRequest, identity_prompt_bytes
    from novel_harness.extract.prompt import KNOWN_DIMENSIONS_HEADER
    from novel_harness.graph import ChapterText

    chapter = ChapterText(
        chapter_id="chapter:x", project_id="project:x", number=7,
        snapshot_id="snapshot:x", text="第七章\n\n他到了神枢营。\n",
    )
    bare = AnalysisRequest(chapter)
    with_names = AnalysisRequest(chapter, known_dimensions=("职务", "修为", "装备"))

    # ① 身份一模一样 —— 这是本条测试的要点。
    assert with_names.prompt_hash == bare.prompt_hash
    assert bare.prompt_hash == __import__("hashlib").sha256(
        identity_prompt_bytes(chapter.text)
    ).hexdigest()

    # ② 实际发出去的字节**不**一样，而且名字真的在里面（否则模型看不见，白改）。
    assert with_names.prompt_bytes != bare.prompt_bytes
    sent = "".join(m.content for m in with_names.messages)
    assert KNOWN_DIMENSIONS_HEADER in sent
    for name in ("职务", "修为", "装备"):
        assert name in sent
    # 顺序保留：调用方按用量降序给，用得多的在前才是这本书的骨架。
    assert sent.index("职务") < sent.index("修为") < sent.index("装备")

    # ③ 一个名字都没有时（第一章那次）**一个字节都不多发**。
    assert bare.prompt_bytes == identity_prompt_bytes(chapter.text)
    assert KNOWN_DIMENSIONS_HEADER not in "".join(m.content for m in bare.messages)
