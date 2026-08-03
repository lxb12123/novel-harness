"""Pure Python/SQLite shared contract for durable JSON and text values."""

from __future__ import annotations

import json
import math
from typing import Any, Final

_JSON_INT_MIN: Final = -(2**63)
_JSON_INT_MAX: Final = 2**63 - 1


def _bounded_json_int(raw: str) -> int:
    value = int(raw)
    if not _JSON_INT_MIN <= value <= _JSON_INT_MAX:
        raise ValueError("JSON integer exceeds SQLite's exact range")
    return value


def _reject_json_constant(raw: str) -> None:
    raise ValueError(f"non-finite JSON number: {raw}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _validate_json_value(value: Any) -> None:
    if isinstance(value, str):
        value.encode("utf-8")
    elif value is None or isinstance(value, bool):
        return
    elif type(value) is int:
        if not _JSON_INT_MIN <= value <= _JSON_INT_MAX:
            raise ValueError("JSON integer exceeds SQLite's exact range")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
    elif isinstance(value, list):
        for item in value:
            _validate_json_value(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            key.encode("utf-8")
            _validate_json_value(item)
    else:
        raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def strict_json_dumps(value: Any) -> str:
    """Serialize the finite, int64, strict-UTF-8 JSON domain SQLite can audit."""
    _validate_json_value(value)
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    canonical.encode("utf-8")
    return canonical


def strict_sql_text(raw: object) -> str | None:
    """Decode SQL TEXT passed as BLOB without letting sqlite3 decode invalid UTF-8 first."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
    return None


def canonical_json_text(raw: object) -> str | None:
    """Return one duplicate-free Python/SQLite JSON representation, or ``None``."""
    decoded = strict_sql_text(raw)
    if decoded is None:
        return None
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_int=_bounded_json_int,
            parse_constant=_reject_json_constant,
        )
        return strict_json_dumps(value)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return None
