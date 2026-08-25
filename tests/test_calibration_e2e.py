"""ADR 0033 端到端验收：空白新章不丢记忆、SceneBrief 进 Writer、UnknownCast 全禁、
摘要新鲜度、RETCON handoff、安全边界（任务文档 §12 的样例）。
"""

from __future__ import annotations

import json
from hashlib import sha256
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from novel_harness import project
from novel_harness.agent.drafting import chapter_drafter
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.tools import dispatch
from novel_harness.calibration.models import (
    AuthorTurnRef,
    CalibrationReport,
    DirectiveCandidate,
    DirectiveKind,
    EpistemicKind,
    FactType,
    Freshness,
    IntendedCastMember,
    SceneProposal,
)
from novel_harness.calibration.store import CalibrationStore
from novel_harness.db import Connection, connect, migrate
from novel_harness.decisions import quote_hash
from novel_harness.declare import Ledger
from novel_harness.draft.capabilities import resolve_capabilities
from novel_harness.draft.provider import CompletionResult, ProviderConfig, ToolCall
from novel_harness.draft.rolling_summary import SummaryStore
from novel_harness.graph import (
    ChapterSpec,
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EdgeType,
    HEALTH_DIM_KEY,
    HEALTH_DIM_NAME,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import chapter_path

TELL = "花瓶里藏着一封旧信，第 40 章才揭晓"
"""秘密的内容 tell。出现在任何 Agent/Writer 面上都是泄漏。"""

ARBITRARY_INJURY = "陈舟右腿伤势反复，走快了就疼"
"""只存任意字符串的伤势说明：Writer 不得因为它是 CANON 就放行。"""

AUTHOR_SAID = (
    "写第十一章：陈舟和林葵进入仓库找钥匙线索，两个人仍然互相怀疑，"
    "暂时不要揭开花瓶秘密。"
)


def _insert_summary(
    conn: Connection,
    pid: str,
    chapter: int,
    summary: str,
    *,
    created_at: str = "",
) -> None:
    # 2026-08-20 合并保存闭环任务后，总结是**版本化**的：读路径走
    # `chapter chapter_summary_head chapter_summary` 三连 JOIN（`_latest_per_chapter`），
    # 所以夹具除了版本行还必须把 head 指针指过来——只插版本行的话 `coverage()`
    # 看不见它，这一章会被当成「没总结」而不是「总结陈旧」。
    # 这里仍然裸写 SQL 而不是走 `RollingSummarizer.ensure()`：**要的就是能指定
    # `created_at`**（新鲜度判据比的正是它和当前快照的先后）。
    created = created_at or "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
    chapter_id = conn.execute(
        "SELECT id FROM chapter WHERE project_id = ? AND number = ?", (pid, chapter)
    ).fetchone()["id"]
    summary_id = f"summary:{pid}:{chapter}"
    conn.execute(
        f"""
        INSERT OR IGNORE INTO chapter_summary
          (id, project_id, chapter_id, chapter_number, summary, summary_sha256,
           schema_version, prompt_hash, created_at, source, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, {created}, 'model', 'ACTIVE')
        """,
        (summary_id, pid, chapter_id, chapter, summary,
         sha256(summary.encode("utf-8")).hexdigest(), "v1", f"hash-{chapter}"),
    )
    conn.execute(
        "UPDATE chapter_summary_head SET current_summary_id = ? WHERE chapter_id = ?",
        (summary_id, chapter_id),
    )
    conn.commit()


def _add_event(
    conn: Connection,
    pid: str,
    chapter: int,
    summary: str,
    participants: list[str],
    knowers: list[str],
) -> None:
    """直接落一条 CANON 事件（测试 fixtures 可以写 SQL，src 不许）。"""
    snap = conn.execute(
        """
        SELECT snapshot.id AS id FROM chapter_snapshot AS snapshot
        JOIN chapter ON chapter.id = snapshot.chapter_id
        WHERE chapter.project_id = ? AND chapter.number = ?
          AND snapshot.text_sha256 = chapter.text_sha256
        """,
        (pid, chapter),
    ).fetchone()["id"]
    chapter_id = conn.execute(
        "SELECT id FROM chapter WHERE project_id = ? AND number = ?", (pid, chapter)
    ).fetchone()["id"]
    evidence_id = f"evid:{pid}:{chapter}"
    conn.execute(
        """
        INSERT INTO evidence
          (id, project_id, chapter_snapshot_id, para_index, quote_text, quote_sha256,
           chapter_id, para_index_hint)
        VALUES (?, ?, ?, 0, ?, ?, ?, 0)
        """,
        (evidence_id, pid, snap, "引语", quote_hash("引语"), chapter_id),
    )
    event_id = f"event:{pid}:{chapter}"
    conn.execute(
        """
        INSERT INTO story_event
          (id, project_id, chapter_number, summary, valid_from_chapter,
           information_scope, status, source, evidence_id, evidence_status)
        VALUES (?, ?, ?, ?, ?, 'CANON', 'ACTIVE', 'extractor', ?, 'FRESH')
        """,
        (event_id, pid, chapter, summary, chapter, evidence_id),
    )
    for cid in participants:
        conn.execute(
            "INSERT INTO event_participant (event_id, project_id, character_id) VALUES (?, ?, ?)",
            (event_id, pid, cid),
        )
    for cid in knowers:
        conn.execute(
            """
            INSERT INTO event_knower
              (event_id, project_id, character_id, valid_from_chapter,
               information_scope, status, evidence_id, evidence_status)
            VALUES (?, ?, ?, ?, 'CANON', 'ACTIVE', ?, 'FRESH')
            """,
            (event_id, pid, cid, chapter, evidence_id),
        )
    conn.commit()


@pytest.fixture
def warehouse(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """陈舟 / 林葵 / 仓库 / 花瓶秘密 + 第 1–10 章 + 第 11 章只有标题。

    - 陈舟有一条**封闭**身体限制（作者 Canon，value_key）+ 一条**任意字符串**伤势说明；
    - 两人有一条 extractor 的 RELATED_TO（value_key）；
    - 第 9 章一条两人都知情的事件；第 9 章新鲜摘要、第 8 章陈旧摘要（先写后改）。
    """
    db = tmp_path / "cal.db"
    root = tmp_path / "book"
    (root / "chapters").mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="仓库", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)

    ids: dict[str, str] = {}
    for name in ("陈舟", "林葵", "周宁"):
        ids[name] = ledger.declare_node(NodeLabel.CHARACTER, name).id
    ids["仓库"] = ledger.declare_node(NodeLabel.LOCATION, "仓库").id
    ids["钥匙线索"] = ledger.declare_node(NodeLabel.OBJECT, "钥匙线索").id
    ids["花瓶秘密"] = store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.FACTION,
            name="花瓶秘密",
            props=NodeProps.model_validate({"twist": TELL, "plot_note": "花瓶的秘密内容"}),
        )
    ).id
    conn.commit()

    for number in range(1, 11):
        heading = f"第{number}章 卷{number}"
        body = f"陈舟与林葵在第 {number} 章碰头。\n" * 3
        if number == 10:
            body += "林葵在第 10 章独自读完了那封信。\n"
        text = f"{heading}\n\n{body}\n"
        (root / chapter_path(number)).write_text(text, encoding="utf-8")
        store.put_chapter(
            ChapterSpec(
                project_id=pid,
                number=number,
                heading=heading,
                path=chapter_path(number),
                text=text,
            )
        )
    (root / chapter_path(11)).write_text("第十一章 仓库\n", encoding="utf-8")
    conn.commit()

    # 陈舟：封闭身体限制（作者 Canon）。
    dim = store.ensure_state_dim(pid, HEALTH_DIM_KEY, HEALTH_DIM_NAME)
    store.upsert_edge(
        EdgeSpec(
            project_id=pid,
            src=ids["陈舟"],
            dst=dim.id,
            type=EdgeType.HAS_STATE,
            valid_from_chapter=10,
            information_scope=InformationScope.CANON,
            props=EdgeProps(value="行动受限", value_key="limited_mobility"),
            source=EdgeSource.AUTHOR,
        )
    )
    # 陈舟：任意字符串伤势说明（挂在自己的维度上，避免被 supersede 掉）。
    note_dim = store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.STATE_DIM,
            name="伤势说明",
            props=NodeProps.model_validate({"dim_key": "injury_note"}),
        )
    )
    store.upsert_edge(
        EdgeSpec(
            project_id=pid,
            src=ids["陈舟"],
            dst=note_dim.id,
            type=EdgeType.HAS_STATE,
            valid_from_chapter=10,
            information_scope=InformationScope.CANON,
            props=EdgeProps(value=ARBITRARY_INJURY),
            source=EdgeSource.AUTHOR,
        )
    )
    # 关系（extractor 自动 Canon，ADR 0020）。
    store.upsert_edge(
        EdgeSpec(
            project_id=pid,
            src=ids["陈舟"],
            dst=ids["林葵"],
            type=EdgeType.RELATED_TO,
            valid_from_chapter=9,
            information_scope=InformationScope.CANON,
            props=EdgeProps(value="互相怀疑", value_key="mutual_suspicion"),
            source=EdgeSource.EXTRACTOR,
        )
    )
    conn.commit()

    _insert_summary(
        conn, pid, 8, "第八章：陈舟腿伤复发。", created_at="'2020-01-01T00:00:00.000Z'"
    )
    _insert_summary(
        conn, pid, 9, "第九章：两人在仓库外碰头。", created_at="'2099-01-01T00:00:00.000Z'"
    )
    # 第 8 章正文改掉并同步 → 它的摘要变成陈旧。
    new_ch8 = "第八章 卷8\n\n陈舟独自在仓库外踱步。\n"
    (root / chapter_path(8)).write_text(new_ch8, encoding="utf-8")
    store.put_chapter(
        ChapterSpec(
            project_id=pid,
            number=8,
            heading="第八章 卷8",
            path=chapter_path(8),
            text=new_ch8,
        )
    )
    conn.commit()

    _add_event(
        conn, pid, 9, "两人同时进入仓库", [ids["陈舟"], ids["林葵"]], [ids["陈舟"], ids["林葵"]]
    )
    yield {"conn": conn, "pid": pid, "root": root, "ids": ids, "store": store}
    conn.close()


def _turn(pid: str, marker: str) -> AuthorTurnRef:
    return AuthorTurnRef(
        turn_id=f"{pid}:turn:{marker}",
        request_sha256=quote_hash(AUTHOR_SAID + marker),
    )


def _proposal() -> SceneProposal:
    return SceneProposal(
        chapter=11,
        intended_cast=(
            IntendedCastMember(surface="陈舟"),
            IntendedCastMember(surface="林葵"),
        ),
        directive_candidates=(
            DirectiveCandidate(
                kind=DirectiveKind.ENTER_LOCATION,
                actor_surface="陈舟",
                location_surface="仓库",
            ),
            DirectiveCandidate(
                kind=DirectiveKind.SEARCH_FOR,
                actor_surface="林葵",
                object_surface="钥匙线索",
            ),
            DirectiveCandidate(kind=DirectiveKind.DEFER_REVEAL, object_surface="花瓶秘密"),
        ),
    )


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


def _context(warehouse: dict[str, Any], *, turn: AuthorTurnRef, **overrides: Any) -> ToolContext:
    base = {
        "store": warehouse["store"],
        "project_id": warehouse["pid"],
        "root_path": str(warehouse["root"]),
        "calibrations": CalibrationStore(warehouse["conn"]),
        "author_turn": turn,
        "summaries": SummaryStore(warehouse["conn"]),
        "events": SqliteEventStore(warehouse["conn"]),
        "working_chapter": 10,
    }
    base.update(overrides)
    return ToolContext(**base)


def _calibrate(warehouse: dict[str, Any], *, turn: AuthorTurnRef) -> CalibrationReport:
    outcome = dispatch(
        _call("calibrate_scene", **_proposal().model_dump()),
        _context(warehouse, turn=turn),
    )
    assert outcome.ok, outcome.content
    return CalibrationReport.model_validate_json(outcome.content)


class CaptureWriter:
    """替掉那一次真的模型调用：把 prompt 原样留下来，交回一段正文。"""

    def __init__(self) -> None:
        self.prompts: list[Any] = []

    def __call__(
        self, messages: Any, *, config: Any, plan: Any, client: Any = None
    ) -> CompletionResult:
        self.prompts.append(messages)
        return CompletionResult(
            text="陈舟推开仓库的门。\n\n林葵跟在后面。",
            model="deepseek-v4-flash",
            finish_reason="stop",
            prompt_tokens=1_000,
            completion_tokens=2_000,
        )


def _desk(warehouse: dict[str, Any]) -> Any:
    return chapter_drafter(
        store=warehouse["store"],
        conn=warehouse["conn"],
        project_id=warehouse["pid"],
        root=warehouse["root"],
        config=ProviderConfig(
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="sk-test",
        ),
        capability=resolve_capabilities("https://api.deepseek.com", "deepseek-v4-flash"),
        events=SqliteEventStore(warehouse["conn"]),
        summaries=SummaryStore(warehouse["conn"]),
    )


def test_blank_chapter_11_calibrates_and_drafts_without_leaking(
    warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """12.1：空白新章用预计人物完成检索与校准；UnknownCast 白名单记忆进 Writer。"""
    import novel_harness.draft.generate as generate_mod

    turn = _turn(warehouse["pid"], "a")
    report = _calibrate(warehouse, turn=turn)

    assert {m.node.name for m in report.normalized_cast} == {"陈舟", "林葵"}
    body = [f for f in report.agent_safe_facts if f.fact_type is FactType.BODY_LIMITATION]
    assert body and body[0].kind is EpistemicKind.AUTHOR_CANON_FACT
    rel = [f for f in report.agent_safe_facts if f.fact_type is FactType.RELATIONSHIP_STAGE]
    assert rel and rel[0].kind is EpistemicKind.EXTRACTED_CURRENT
    events = [f for f in report.agent_safe_facts if f.fact_type is FactType.EVENT]
    assert events, "校准报告没找到已确认事件"
    summaries = [f for f in report.agent_safe_facts if f.fact_type is FactType.CHAPTER_SUMMARY]
    assert any(f.freshness is Freshness.STALE for f in summaries), "第 8 章摘要应标 STALE"
    assert any(f.freshness is Freshness.CURRENT for f in summaries), "第 9 章摘要应 CURRENT"
    assert any(f.fact_type is FactType.FORESHADOW for f in report.unknowns), "12.8：伏笔诚实未知"
    assert not report.deterministic_conflicts, "12.5：校准层不宣判语义冲突"

    sealed_out = dispatch(
        _call("seal_scene_brief", inspection_id=report.id),
        _context(warehouse, turn=turn),
    )
    assert sealed_out.ok, sealed_out.content
    calibration_id = json.loads(sealed_out.content)["calibration_id"]

    writer = CaptureWriter()
    monkeypatch.setattr(generate_mod, "complete", writer)
    desk = _desk(warehouse)
    draft_out = dispatch(
        _call("draft_chapter", chapter=11, calibration_id=calibration_id),
        _context(warehouse, turn=turn, drafter=desk),
    )
    assert draft_out.ok, draft_out.content
    prompt = json.dumps(writer.prompts[0], ensure_ascii=False)
    assert "【本稿执行计划】" in prompt, "SceneBrief 没进 Writer"
    assert "【目标章当前正文】" in prompt and "第十一章 仓库" in prompt, "12.7：目标章没进 Writer"
    assert "【连续性依据】" in prompt
    assert "【机器写作建议】" in prompt or "【Agent 对作者请求的结构化理解" in prompt
    assert "两人同时进入仓库" not in prompt, "UnknownCast 下事件不进 Writer"
    assert "第九章：两人在仓库外碰头" not in prompt, "UnknownCast 下总结不进 Writer"
    assert ARBITRARY_INJURY not in prompt, "任意字符串状态不能只因为 CANON 就放行"
    assert TELL not in prompt
    assert AUTHOR_SAID not in prompt, "作者聊天原文不自动进入 Writer"
    assert "花瓶秘密" in prompt, "安全约束（显示名）仍由后端按章重算"


def test_stale_summary_never_reaches_the_writer(
    warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """12.3：陈旧摘要不进 Writer。"""
    import novel_harness.draft.generate as generate_mod

    turn = _turn(warehouse["pid"], "stale")
    report = _calibrate(warehouse, turn=turn)
    sealed_out = dispatch(
        _call("seal_scene_brief", inspection_id=report.id),
        _context(warehouse, turn=turn),
    )
    calibration_id = json.loads(sealed_out.content)["calibration_id"]
    writer = CaptureWriter()
    monkeypatch.setattr(generate_mod, "complete", writer)
    desk = _desk(warehouse)
    outcome = dispatch(
        _call("draft_chapter", chapter=11, calibration_id=calibration_id),
        _context(warehouse, turn=turn, drafter=desk),
    )
    assert outcome.ok, outcome.content
    prompt = json.dumps(writer.prompts[0], ensure_ascii=False)
    assert "第八章：陈舟腿伤复发" not in prompt, "陈旧摘要进了 Writer"
    assert "第九章：两人在仓库外碰头" not in prompt  # UnknownCast 下新鲜摘要也不进


def test_author_confirmation_binds_the_card_and_the_turn(
    warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """12.1 第四半 + §5.3：作者确认过固定渲染任务卡，指令才进 author_instructions。"""
    import novel_harness.draft.generate as generate_mod

    first = _turn(warehouse["pid"], "ask")
    report = _calibrate(warehouse, turn=first)
    second = _turn(warehouse["pid"], "answer")
    sealed_out = dispatch(
        _call(
            "seal_scene_brief",
            inspection_id=report.id,
            author_choice="STORY_PROGRESSION",
        ),
        _context(warehouse, turn=second),
    )
    assert sealed_out.ok, sealed_out.content
    calibration_id = json.loads(sealed_out.content)["calibration_id"]
    writer = CaptureWriter()
    monkeypatch.setattr(generate_mod, "complete", writer)
    desk = _desk(warehouse)
    outcome = dispatch(
        _call("draft_chapter", chapter=11, calibration_id=calibration_id),
        _context(warehouse, turn=second, drafter=desk),
    )
    assert outcome.ok, outcome.content
    prompt = json.dumps(writer.prompts[0], ensure_ascii=False)
    assert "【作者确认的本稿要求】" in prompt


def test_seal_in_the_same_turn_cannot_fake_confirmation(warehouse: dict[str, Any]) -> None:
    """§5.3：仅有指向模型文字的 answer ID 不算作者确认 —— 同一 turn 封存拒绝确认。"""
    turn = _turn(warehouse["pid"], "same")
    report = _calibrate(warehouse, turn=turn)
    outcome = dispatch(
        _call(
            "seal_scene_brief",
            inspection_id=report.id,
            author_choice="STORY_PROGRESSION",
        ),
        _context(warehouse, turn=turn),
    )
    assert outcome.ok is False
    assert "作者还没有在看过任务卡之后回答" in outcome.content


def test_retcon_non_safety_produces_handoff(
    warehouse: dict[str, Any],
) -> None:
    """12.4 + §9.3：非安全 RETCON 产 handoff；涉及知情的 RETCON 在 Canon 纠错前拒绝。"""
    store = CalibrationStore(warehouse["conn"])
    turn = _turn(warehouse["pid"], "retcon")
    report = _calibrate(warehouse, turn=turn)
    body_item = next(
        f.item_id for f in report.agent_safe_facts if f.fact_type is FactType.BODY_LIMITATION
    )
    sealed_out = dispatch(
        _call(
            "seal_scene_brief",
            inspection_id=report.id,
            author_choice="RETCON_NON_SAFETY",
            retcon_fact_ids=[body_item],
            inferred_tension="作者要陈舟无伤奔跑（机器推演）。",
        ),
        _context(warehouse, turn=_turn(warehouse["pid"], "retcon-answer")),
    )
    assert sealed_out.ok, sealed_out.content
    pending = store.pending_handoffs(warehouse["pid"])
    assert len(pending) == 1 and pending[0][1].author_choice == "RETCON"

    # ⚠️ 这条测试原来还有下半截：给林葵补一条 KNOWS → 报告里出现知情事实 →
    # 引用它做 RETCON_NON_SAFETY → 被拒。**那半随秘密下线一起没了**（ADR 0039）：
    # `SAFETY_FACT_TYPES` 的两个成员就是 KNOWS / BELIEVES，现在它是**空集**，
    # 那道闸不再拦任何东西。挑新成员是维护者的裁定，见 `calibration/seal.py` 上那段。


def test_new_character_cannot_turn_unknown_cast_into_resolved(
    warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """12.6：周宁进检索，但空白章的安全基集仍是 UnknownCast/full-ban。"""
    import novel_harness.draft.generate as generate_mod

    turn = _turn(warehouse["pid"], "zhouning")
    proposal = _proposal().model_copy(
        update={
            "intended_cast": _proposal().intended_cast + (IntendedCastMember(surface="周宁"),)
        }
    )
    outcome = dispatch(
        _call("calibrate_scene", **proposal.model_dump()),
        _context(warehouse, turn=turn),
    )
    assert outcome.ok, outcome.content
    report = CalibrationReport.model_validate_json(outcome.content)
    assert {m.node.name for m in report.normalized_cast} == {"陈舟", "林葵", "周宁"}

    sealed_out = dispatch(
        _call("seal_scene_brief", inspection_id=report.id),
        _context(warehouse, turn=turn),
    )
    calibration_id = json.loads(sealed_out.content)["calibration_id"]
    writer = CaptureWriter()
    monkeypatch.setattr(generate_mod, "complete", writer)
    desk = _desk(warehouse)
    draft_out = dispatch(
        _call("draft_chapter", chapter=11, calibration_id=calibration_id),
        _context(warehouse, turn=turn, drafter=desk),
    )
    assert draft_out.ok, draft_out.content


def test_watermark_change_rejects_the_old_calibration_id(
    warehouse: dict[str, Any],
) -> None:
    """§5.3 / §8.4：水位变化后 draft_chapter 明确拒绝旧 ID。"""
    turn = _turn(warehouse["pid"], "wm")
    report = _calibrate(warehouse, turn=turn)
    sealed_out = dispatch(
        _call("seal_scene_brief", inspection_id=report.id),
        _context(warehouse, turn=turn),
    )
    calibration_id = json.loads(sealed_out.content)["calibration_id"]
    (warehouse["root"] / chapter_path(11)).write_text(
        "第十一章 仓库\n\n陈舟已经进了仓库。\n", encoding="utf-8"
    )
    outcome = dispatch(
        _call("draft_chapter", chapter=11, calibration_id=calibration_id),
        _context(warehouse, turn=turn, drafter=_desk(warehouse)),
    )
    assert outcome.ok is False
    assert "变了" in outcome.content and "重新校准" in outcome.content


def test_planned_scope_never_reaches_the_report(
    warehouse: dict[str, Any],
) -> None:
    """完整 PLANNED 不进 Agent 报告（约束 4 / ADR 0033 §4.3）。"""
    store = warehouse["store"]
    pid = warehouse["pid"]
    ids = warehouse["ids"]
    # 载体 2026-08-24 从 PLANNED 的 KNOWS 换成 PLANNED 的 LOCATED_AT
    # （秘密下线，ADR 0039）——**这条纪律跟秘密无关**：PLANNED 是作者的未来计划，
    # 它进了报告就是把还没发生的剧情递给模型，那正是这个产品声称结构上恒为 0 的东西。
    store.upsert_edge(
        EdgeSpec(
            project_id=pid,
            src=ids["周宁"],
            dst=ids["仓库"],
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=40,
            information_scope=InformationScope.PLANNED,
            props=EdgeProps(),
            source=EdgeSource.AUTHOR,
        )
    )
    warehouse["conn"].commit()
    turn = _turn(pid, "planned")
    report = _calibrate(warehouse, turn=turn)
    planned = [
        f
        for f in report.agent_safe_facts
        if f.fact_type is FactType.LOCATION and "仓库" in f.display_text
    ]
    assert not planned, "PLANNED 的边进了报告（scope 过滤漏了）"
