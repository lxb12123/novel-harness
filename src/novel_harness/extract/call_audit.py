"""模型调用的审计原子持久化（抽取 / 滚动总结 / 起草 / 写作助手共用）。"""

from __future__ import annotations

from collections.abc import Callable
import json

from pydantic import BaseModel, ConfigDict

from ..db import Connection
from ..ids import artifact_id
from .control import AnalysisRequest, AuditedCompletion, ExtractionRun, ExtractionRunStateError


class ModelCallReceipt(BaseModel):
    """一次模型调用的**账单原料**：`record_call()` 除 conn / project_id / id 工厂之外的全部入参。

    形状照着下面那个函数的签名长，是为了让持有 conn 的那一层是一次**平移**而不是一次
    翻译——翻译的地方就是能悄悄漏字段的地方，而漏掉的那个字段会让日志页少算一笔钱。
    （`tests/test_chat_api.py` 有一条按字段名对签名的断言钉着这句话。）

    **它住在这儿而不是 `agent/loop.py`**（2026-08-11 搬的）：起草侧
    （`draft/product_draft.py`）也产同一种原料，而 `draft/` 不许 import `agent/`。
    放在 `record_call` 隔壁还有一个好处——改签名的人抬眼就看得见要跟着改的那个模型。

    `capability` / `schema_version` **没有默认值**：搬过来之前它们默认是写作助手那一档，
    于是起草侧少写一个参数就会把一次起草记成一次聊天，而日志页上那两行长得一模一样。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    """`model_call.capability`。**新长出一种花钱的动作就要去
    `activity.py::_CAPABILITY_LABEL` 补一行中文**，那张表认不出的是原样回吐的。"""

    schema_version: str
    model: str
    finish_reason: str | None = None
    prompt_hash: str
    prompt_bytes: bytes
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    """**供应商报的那个数，没报就是 `None`。** 这一层不许替它补一个估算值：
    账上的零和「没报」是两件事（`CostTotals` 把 `NULL` 折成 0 那条已知病就是这么来的）。
    闸门那一侧另算（`agent/loop.py` 的 `charged`），两个消费者两套规矩。"""

    elapsed_ms: int = 0


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
