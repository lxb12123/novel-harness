"""抽取运行的模型调用审计原子持久化。"""

from __future__ import annotations

from collections.abc import Callable
import json

from ..db import Connection
from ..ids import artifact_id
from .control import AnalysisRequest, AuditedCompletion, ExtractionRun, ExtractionRunStateError


def record_model_call(
    conn: Connection,
    run: ExtractionRun,
    request: AnalysisRequest,
    completion: AuditedCompletion,
    *,
    elapsed_ms: int,
    call_id_factory: Callable[[str], str],
) -> str:
    call_id = call_id_factory(run.project_id)
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("call id factory must return a non-empty string")
    call_id.encode("utf-8")
    params_json = json.dumps(
        {
            "finish_reason": completion.finish_reason,
            "schema_version": run.schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    out_bytes = completion.text.encode("utf-8")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO model_call (
                id, project_id, capability, model, params_json, prompt_hash,
                in_artifact, out_artifact, tokens_in, tokens_out, ms
            ) VALUES (?, ?, 'extractor', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                run.project_id,
                completion.model or "unknown",
                params_json,
                request.prompt_hash,
                artifact_id(request.prompt_bytes),
                artifact_id(out_bytes),
                completion.prompt_tokens,
                completion.completion_tokens,
                elapsed_ms,
            ),
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
