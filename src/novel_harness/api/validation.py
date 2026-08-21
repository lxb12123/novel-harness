"""正文验证器的薄 HTTP 壳（Task 4）。

`POST …/chapters/{n}/check` 与保存流程共用同一个
`checks.service.validate_snapshot`——手动检查和保存后的自动验证不能是两条实现，
否则「作者按了检查看到的」和「系统保存后自动判的」迟早分岔。

`GET …/validation-rules` 返回规则目录元数据（Task 13 再补作者自定义规则与 CRUD）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import importer
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
    conn: Annotated[Connection, Depends(get_conn)] = None,
) -> list[dict[str, Any]]:

    """规则目录元数据：R2/R3 常驻显示 + 作者自定义规则（024 / Task 13）。"""
    from .. import checks
    from ..checks.service import load_custom_rules

    specs = (*checks.catalog.SYSTEM_RULES, *load_custom_rules(conn, proj.id))
    return [
        {
            "rule_id": spec.rule_id,
            "title": spec.title,
            "description": spec.description,
            "enabled": spec.enabled,
            "blocks_downstream": spec.blocks_downstream,
            "template": spec.template,
        }
        for spec in specs
    ]


@router.post("/api/projects/{project_id}/validation-rules")
def add_validation_rule(
    body: ValidationRuleCreate,
    conn: Annotated[Connection, Depends(get_conn)],
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """添加一条确定性命中规则（024 / Task 13）。

    `literal` 必须非空；语义变化在同一事务 `epoch += 1` 并重算 ruleset hash——
    否则旧报告/旧 attempt 会拿旧 ruleset 冒充新集。
    """
    from ..ids import EntityType, new_id

    config = {"literal": body.literal}
    rule_id = new_id(EntityType.VALIDATION_RULE, proj.id)
    conn.execute(
        """
        INSERT INTO validation_rule (
            id, project_id, title, template, enabled, blocks_downstream, config_json
        ) VALUES (?, ?, ?, 'forbidden_literal', 1, ?, ?)
        """,
        (rule_id, proj.id, body.title or body.literal, body.blocks_downstream,
         json.dumps(config, ensure_ascii=False)),
    )
    _bump_ruleset(conn, proj.id)
    conn.commit()
    return {
        "rule_id": rule_id,
        "title": body.title or body.literal,
        "template": "forbidden_literal",
        "enabled": True,
        "blocks_downstream": body.blocks_downstream,
        "config": config,
    }


@router.patch("/api/projects/{project_id}/validation-rules/{rule_id}")
def update_validation_rule(
    rule_id: str,
    body: ValidationRulePatch,
    conn: Annotated[Connection, Depends(get_conn)],
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """启停 / 改字 / 改阻断（024）。语义变化同事务 bump ruleset epoch。"""
    row = conn.execute(
        "SELECT id FROM validation_rule WHERE project_id = ? AND id = ?",
        (proj.id, rule_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, {"error": "rule_not_found", "rule_id": rule_id})
    setters: list[str] = []
    params: list[object] = []
    if body.enabled is not None:
        setters.append("enabled = ?")
        params.append(int(body.enabled))
    if body.blocks_downstream is not None:
        setters.append("blocks_downstream = ?")
        params.append(int(body.blocks_downstream))
    if body.literal is not None:
        config = json.dumps({"literal": body.literal}, ensure_ascii=False)
        setters.append("config_json = ?")
        params.append(config)
    if setters:
        setters.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        params.extend((rule_id, proj.id))
        conn.execute(
            f"UPDATE validation_rule SET {', '.join(setters)} "
            "WHERE id = ? AND project_id = ?",
            params,
        )
        _bump_ruleset(conn, proj.id)
        conn.commit()
    return {"rule_id": rule_id, "updated": bool(setters)}


@router.delete("/api/projects/{project_id}/validation-rules/{rule_id}")
def delete_validation_rule(
    rule_id: str,
    conn: Annotated[Connection, Depends(get_conn)],
    proj: Any = Depends(load_project),
) -> dict[str, str]:
    """删除一条作者规则（删除也是语义变化：epoch + 1）。"""
    changed = conn.execute(
        "DELETE FROM validation_rule WHERE id = ? AND project_id = ?",
        (rule_id, proj.id),
    ).rowcount
    if changed != 1:
        raise HTTPException(404, {"error": "rule_not_found", "rule_id": rule_id})
    _bump_ruleset(conn, proj.id)
    conn.commit()
    return {"rule_id": rule_id, "deleted": "true"}


def _bump_ruleset(conn: Connection, project_id: str) -> None:
    """规则集语义变化：epoch + 1 并重算 hash（同事务，与规则写在一笔里提交）。"""
    from ..checks.service import ruleset_semantic_hash

    new_hash = ruleset_semantic_hash(conn, project_id)
    conn.execute(
        "UPDATE validation_ruleset_state SET epoch = epoch + 1, ruleset_hash = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE project_id = ?",
        (new_hash, project_id),
    )


class ValidationRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    literal: str = Field(min_length=1)
    blocks_downstream: bool = True


class ValidationRulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    blocks_downstream: bool | None = None
    literal: str | None = None


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
