"""进程入口：`NH_DB=<库> uv run python -m novel_harness.api`。

不开连接（只 uvicorn.run 一个 import 字符串），所以不进 CONNECTION_OPENERS——
真正开库的是 deps.py。
"""

from __future__ import annotations

import os

import uvicorn


def main() -> int:
    host = os.environ.get("NH_HOST", "127.0.0.1")
    port = int(os.environ.get("NH_PORT", "8756"))
    uvicorn.run("novel_harness.api.app:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
