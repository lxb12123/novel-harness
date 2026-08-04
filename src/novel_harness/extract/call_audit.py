"""模型调用的审计原子持久化（抽取 / 滚动总结共用）。"""

from __future__ import annotations

from collections.abc import Callable
import json

from ..db import Connection
from ..ids import artifact_id
from .control import AnalysisRequest, AuditedCompletion, ExtractionRun, ExtractionRunStateError


def record_call(
    conn: Connection,
    *,
    project_id: str,
    capability: str,
    model: str,
    finish_reason: str | None,
    schema_version: str,
    prompt_hash: str,
    prompt_bytes: bytes,
    text: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    elapsed_ms: int,
    call_id_factory: Callable[[str], str],
) -> str:
    """写入一条 model_call 审计行并返回 call id（调用方负责自己的业务表）。"""
    call_id = call_id_factory(project_id)
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("call id factory must return a non-empty string")
    call_id.encode("utf-8")
    if not capability or not isinstance(capability, str):
        raise ValueError("capability must be a non-empty string")
    params_json = json.dumps(
        {"finish_reason": finish_reason, "schema_version": schema_version},
        sort_keys=True,
        separators=(",", ":"),
    )
    out_bytes = text.encode("utf-8")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO model_call (
            id, project_id, capability, model, params_json, prompt_hash,
            in_artifact, out_artifact, tokens_in, tokens_out, ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            project_id,
            capability,
            model or "unknown",
            params_json,
            prompt_hash,
            artifact_id(prompt_bytes),
            artifact_id(out_bytes),
            prompt_tokens,
            completion_tokens,
            elapsed_ms,
        ),
    )
    return call_id


def record_model_call(
    conn: Connection,
    run: ExtractionRun,
    request: AnalysisRequest,
    completion: AuditedCompletion,
    *,
    elapsed_ms: int,
    call_id_factory: Callable[[str], str],
) -> str:
    try:
        call_id = record_call(
            conn,
            project_id=run.project_id,
            capability="extractor",
            model=completion.model,
            finish_reason=completion.finish_reason,
            schema_version=run.schema_version,
            prompt_hash=request.prompt_hash,
            prompt_bytes=request.prompt_bytes,
            text=completion.text,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            elapsed_ms=elapsed_ms,
            call_id_factory=call_id_factory,
        )
        changed = conn.execute(
            """
            UPDATE extraction_run SET model_call_id = ?
            WHERE id = ? AND status = 'RUNNING' AND model_call_id IS NULL
            """,
            (call_id, run.id),
        )
        if changed.rowcount != 1:
            raise ExtractionRunStateError(
                f"run stopped being RUNNING while recording call: {run.id}"
            )
        conn.commit()
        return call_id
    except BaseException:
        conn.rollback()
        raise
