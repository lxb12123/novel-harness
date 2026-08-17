"""正文验证器的薄 HTTP 壳（Task 4）。

`POST …/chapters/{n}/check` 与保存流程共用同一个
`checks.service.validate_snapshot`——手动检查和保存后的自动验证不能是两条实现，
否则「作者按了检查看到的」和「系统保存后自动判的」迟早分岔。

`GET …/validation-rules` 返回规则目录元数据（Task 13 再补作者自定义规则与 CRUD）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .. import importer
from ..checks import catalog
from ..checks.service import (
    SnapshotValidationReport,
    current_ruleset,
    validate_snapshot,
)
from ..db import Connection
from ..graph import ChapterCommitToken
from ..text import paragraphs as split_paragraphs
from .deps import get_conn, get_store, load_project


router = APIRouter()


@router.get("/api/projects/{project_id}/validation-rules")
def validation_rules(
    proj: Any = Depends(load_project),
) -> list[dict[str, Any]]:
    """规则目录元数据：R2/R3 常驻显示（Task 13 在这里并上作者自定义规则）。"""
    return [
        {
            "rule_id": spec.rule_id,
            "title": spec.title,
            "description": spec.description,
            "enabled": spec.enabled,
            "blocks_downstream": spec.blocks_downstream,
            "template": spec.template,
        }
        for spec in catalog.SYSTEM_RULES
    ]


@router.post("/api/projects/{project_id}/chapters/{chapter}/check")
def check(
    chapter: int,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> SnapshotValidationReport:
    """手动检查**当前快照**：和保存流程同一个 service，返回快照绑定报告。

    磁盘是正文真相源（ADR 0007），所以先确保磁盘版已收进库（没同步过就同步这一章），
    再拿 `ChapterCommitToken` 跑规则——报告的 snapshot/generation/hash 与库完全一致。
    """
    root = Path(proj.root_path)
    file = root / importer.chapter_path(chapter)
    if not file.exists():
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})
    text = file.read_text(encoding="utf-8-sig")
    text_sha = importer.text_digest(text)
    db_hash = store.current_chapter_hash(proj.id, chapter)
    if db_hash != text_sha:
        importer.sync_chapter(store, proj.id, root, chapter)

    current = next(
        (ct for ct in store.current_snapshots(proj.id) if ct.number == chapter), None
    )
    if current is None or importer.text_digest(current.text) != text_sha:
        raise HTTPException(422, {"error": "sync_refused", "message": "这一章还没进库"})
    generation = store.current_chapter_generation(proj.id, chapter)
    if generation is None:
        raise HTTPException(422, {"error": "sync_refused", "message": "这一章还没进库"})
    token = ChapterCommitToken(
        project_id=proj.id,
        chapter_id=current.chapter_id,
        chapter_number=chapter,
        source_snapshot_id=current.snapshot_id,
        source_generation=generation,
        text_sha256=text_sha,
        text=text,
        changed=False,
    )
    epoch, ruleset_hash = current_ruleset(conn, proj.id)
    return validate_snapshot(
        conn,
        store,
        token,
        ruleset_epoch=epoch,
        ruleset_hash=ruleset_hash,
        phase="initial",
        paragraphs=split_paragraphs(text),
    )
