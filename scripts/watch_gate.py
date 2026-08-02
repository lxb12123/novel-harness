#!/usr/bin/env python3
"""Watch a running `nh gate` round and notify on completion.

Waits until no ``nh gate`` process is alive, then inspects the newest
``runs/*.jsonl`` with the strict offline evidence reconstructor, computes the
preregistered verdict, prints it, writes ``runs/WATCHER_STATUS.txt``, and posts
a macOS notification.

Usage:
    python scripts/watch_gate.py --project project:... [--db synth/gate.db]
        [--ground-truth synth/ground_truth.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _gate_pids() -> list[str]:
    out = subprocess.run(
        ["pgrep", "-f", "nh gate"], capture_output=True, text=True
    )
    return [pid for pid in out.stdout.split() if pid]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="synth/gate.db")
    parser.add_argument("--project", required=True)
    parser.add_argument("--ground-truth", default="synth/ground_truth.json")
    args = parser.parse_args()

    pids = _gate_pids()
    print(f"[watch] waiting for nh gate to finish (pids: {pids or 'none'})", flush=True)
    while _gate_pids():
        time.sleep(30)

    runs_dir = ROOT / "runs"
    candidates = sorted(runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        print("[watch] no runs/*.jsonl found", flush=True)
        return
    path = candidates[-1]
    print(f"[watch] run finished: {path}", flush=True)

    from novel_harness import db
    from novel_harness.eval.evidence import EvidenceError, inspect_run
    from novel_harness.eval.runner import load_traps
    from novel_harness.eval.score import decide
    from novel_harness.graph.sqlite_store import SqliteStoryGraph

    data = json.loads(Path(args.ground_truth).read_text(encoding="utf-8"))
    store = SqliteStoryGraph(db.connect(ROOT / args.db))
    traps = load_traps(data)
    try:
        inspection = inspect_run(
            path, store=store, project_id=args.project, traps=traps
        )
    except EvidenceError as exc:
        verdict = "INVALID"
        summary = f"证据校验失败：{exc}"
    else:
        decision = decide(inspection.gate_input)
        verdict = decision.verdict.value
        summary = (
            f"裁决：{verdict}（规则 {decision.rule}） · "
            f"{len(inspection.gate_input.traps)} 陷阱 × 3 臂 × {inspection.repeats} 次"
        )

    print(f"[watch] {summary}", flush=True)
    (runs_dir / "WATCHER_STATUS.txt").write_text(
        f"{path.name}\n{verdict}\n{summary}\n", encoding="utf-8"
    )
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                "display notification "
                f'"{summary}" with title "nh gate 跑完了 · {verdict}"',
            ],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    main()
