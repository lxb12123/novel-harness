"""M2「防泄漏」考试的独立入口：`python -m novel_harness.gate`。

这场考试干的事：拿 `synth/build.py` 造出来的那本带陷阱的假书（里面谁该知道秘密、
谁不该知道，都是提前埋好的），让引擎去续写，看它会不会"说漏嘴"——把不该知道的
秘密写进不该出现的章节。跑完按 `docs/EVAL_PROTOCOL.md` §6（+ 九份修正案）里
**提前定死、考完不许改**的判分规矩，出 PASS / KILL / INCONCLUSIVE。

维护者要记住的几点：
- **会真调模型、真花钱**（一轮约 225 个 final cell，成功轮因长度续写还会翻到两倍）。
  它只服务这场考试，小说作者一辈子不碰，别手贱连着跑。
- 规矩在 `docs/EVAL_PROTOCOL.md`，**跑之前就冻结**。本入口不解释结果、不挑分支，
  只把 `decide()` 判出来的结论打印出来。
- 退出码：PASS / KILL / INCONCLUSIVE 都是一次**有效**实验的正常结束，退出码 0
  （"KILL" 也是合法结论，别把"结果不合我意"当成"命令出错"）；只有 INVALID 才是
  仪器坏了——陷阱造错 / 判分链断了，这轮等于白跑，没产出任何证据，退出码非 0。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .db import connect
from .graph.sqlite_store import SqliteStoryGraph
from .project import get as get_project

if TYPE_CHECKING:
    from .eval.score import GateDecision

# ── 展示用的盒子和表格 ─────────────────────────────────────────────────────
# 从 cli.py 搬来的（那里同名助手是面板/检查的命令行渲染）；删掉命令行面后，这里就是
# 唯一宿主。`_width` 必须按显示列宽算：`len("萧决") == 2` 但它占 4 列，拿 len() 对齐会歪。


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, columns: int) -> str:
    return text + " " * max(0, columns - _width(text))


def _box(title: str, lines: Sequence[str]) -> str:
    """把几行字画进 §3.2 / README 的那个框里。"""
    inner = max([_width(line) for line in lines] + [_width(title) + 1])
    out = ["┌─ " + title + " " + "─" * (inner - _width(title) - 1) + "┐"]
    out += ["│ " + _pad(line, inner) + " │" for line in lines]
    out.append("└" + "─" * (inner + 2) + "┘")
    return "\n".join(out)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """按列宽对齐的表格，行 0 是表头，表头下面有一条分隔线。"""
    widths = [max(_width(row[i]) for row in [header, *rows]) for i in range(len(header))]
    out = ["  ".join(_pad(cell, w) for cell, w in zip(header, widths, strict=True)).rstrip()]
    out.append("─" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        out.append("  ".join(_pad(cell, w) for cell, w in zip(row, widths, strict=True)).rstrip())
    return out


def _reason(exc: Exception) -> str:
    """把异常变成人话：pydantic 的 str 是给开发者的，掏它包着的那半句给作者/维护者。"""
    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            return "；".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
    except ImportError:  # 理论不可达：pydantic 是硬依赖，只在极小的出错窗口里兜底
        pass
    return str(exc)


class GateError(RuntimeError):
    """跑不通考试（输入错了 / 库/项目对不上）。`run()` 会把它转成退出码 1 的消息。"""


def _open_store(db: Path, project: str) -> SqliteStoryGraph:
    """开库 + 确认这个项目真的有东西。

    存在性检查走 **`project` 表**（`project.get`），而不是 `store.resolve()`——后者是
    算「谁知道什么」的入口，判分层碰它 = 自己摆了第二份禁忌集（EVAL_PROTOCOL §3 /
    `test_draft_boundary.py::test_neither_side_resolves_on_its_own`）。只查项目存在，
    不查花名册、不算约束视图。**不替你建库**：空库上闭世界推导会给出一张「谁都不知道」
    的假矩阵，长得像正确答案，其实什么都不是。失败抛 `GateError`（不 sys.exit——库函数
    不该把退出决定做掉）。
    """
    if not db.exists():
        raise GateError(f"库不存在：{db}（不替你建，见 EVAL_PROTOCOL/ADR 0012）")
    conn = connect(db)
    if get_project(conn, project) is None:
        conn.close()
        raise GateError(
            f"项目 {project} 在 {db} 里没有项目行：\n"
            "要么 project_id 打错了，要么这个库是别本书的。"
        )
    return SqliteStoryGraph(conn)


def _gate_lines(decision: GateDecision, repeats: int) -> list[str]:
    """裁决的全部依据，**逐条印出来**。

    ADR 0009 直接抄这几行，所以这里不许只印一个 verdict：一份说不出自己怎么来的裁决，
    读者没法复算，而「任何人拿同一份 runs/*.jsonl 都能重算出同一个结论」正是
    `decide()` 被写成纯函数的理由。
    """
    out = [
        f"命中规则：{decision.rule}",
        f"动作：{decision.action}",
        "",
        f"n(KNOWS)={decision.n_knows}  n(FUTURE)={decision.n_future}  重复={repeats}",
        f"X0 的 KNOWS 泄漏率：{decision.x0_knows_leak:.2f}"
        "（地板 0.50 / 天花板 0.90，落在区间外判 INVALID）",
        "",
    ]
    out += _table(
        ["比较", "n", "leak(a)", "leak(b)", "Δ", "b_only", "c_only", "p", "Holm p", "符号稳定"],
        [
            [
                c.name,
                str(c.n),
                f"{c.leak_a:.2f}",
                f"{c.leak_b:.2f}",
                f"{c.delta:+.2f}",
                str(c.b_only),
                str(c.c_only),
                f"{c.p_exact:.4f}",
                f"{decision.holm_p.get(c.name, float('nan')):.4f}",
                "✓" if decision.sign_stable.get(c.name) else "✗",
            ]
            for c in decision.comparisons
        ],
    )
    if decision.eligible_arms:
        out += ["", f"「该臂」（取到 max Δ）：{'、'.join(decision.eligible_arms)}"]
    for arm, checks in decision.pass_checks.items():
        flags = "  ".join(f"{k}={'✓' if v else '✗'}" for k, v in checks.items())
        out.append(f"  {arm}：{flags}")
    if decision.future_floor:
        floor = "  ".join(f"{k}={v:.2f}" for k, v in decision.future_floor.items())
        # FUTURE 只作**描述性地板**（§3 / 修正案 4 裁定 C 的 echo 探针），不参与裁决。
        # 不写这句话，下一个读者会拿它当第二个 kill-gate。
        out += ["", f"FUTURE 泄漏率（描述性，不参与裁决）：{floor}"]
    if decision.form_pivot:
        out += ["", "FORM 重要：X2 显著优于 X1 → 生产默认翻成 NH_DRAFT_FORM=X2，重跑确认。"]
    for note in decision.notes:
        out.append(f"⚠ {note}")
    return out


def run(
    *,
    db: Path,
    project: str,
    ground_truth: Path,
    repeats: int,
    out: Path | None,
) -> int:
    """跑一轮考试。返回退出码（0 = 有效实验结束；非 0 = INVALID 或仪器/输入错误）。"""
    from .draft.provider import ProviderConfig, ProviderError
    from .eval.runner import PROTOCOL_VERSION, load_traps, run_gate, stamped_path
    from .eval.score import Verdict, decide

    if "修正案 1/2/3/4/5/6/7/8" not in PROTOCOL_VERSION:
        print(
            "✗ gate 已暂停：当前 runner 的 PROTOCOL_VERSION 不含修正案 1/2/3/4/5/6/7/8。\n"
            "  旧协议跑出来的 JSONL 不能用于 ADR 0009，还白花钱；"
            "等 PROTOCOL_VERSION 原子升级后再放行。",
            file=sys.stderr,
        )
        return 1

    if not ground_truth.exists():
        print(
            f"ground truth 不存在：{ground_truth}\n"
            "它是 synth/build.py 的生成物（不入库），先造小册子再跑 gate。",
            file=sys.stderr,
        )
        return 1

    try:
        store = _open_store(db, project)
    except GateError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    try:
        data = json.loads(ground_truth.read_text(encoding="utf-8"))
        stated = data.get("project_id")
        if stated and stated != project:
            # 拿 A 书的 ground truth 去跑 B 书的库，约束照样算得出，只是算的不是这些陷阱
            # 瞄的那些——数字看起来完全正常，其实不说明任何事。
            print(
                f"ground truth 是给项目 {stated} 造的，而 --project 是 {project}。\n"
                "对不上就不跑：陷阱瞄的那些边界在另一个项目里不存在。",
                file=sys.stderr,
            )
            return 1
        traps = load_traps(data)
    except (ValueError, OSError) as exc:
        print(f"✗ 读不了 {ground_truth}：{_reason(exc)}", file=sys.stderr)
        return 1

    try:
        config = ProviderConfig.from_env()
    except Exception as exc:
        print(
            f"✗ 模型没配好：{_reason(exc)}\n"
            "  gate 要真的调模型。三个环境变量：\n"
            "    export NH_LLM_BASE_URL=https://api.deepseek.com    # DeepSeek V4\n"
            "    export NH_LLM_MODEL=deepseek-v4-flash              # flash / pro\n"
            "    export NH_LLM_API_KEY=...                          # 只从环境注入，不进 profile/JSONL\n"
            "  base_url 和 model 必须是**匹配的一对**——端点上没有这个模型名，发出去就是 404。",
            file=sys.stderr,
        )
        return 1

    try:
        from .draft.capabilities import (
            CapabilityError,
            ReasoningEffort,
            plan_call,
            resolve_capabilities,
        )
        from .draft.length import M2_LENGTH_SPEC

        capability = resolve_capabilities(config.base_url, config.model)
        plan = plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, capability)
    except CapabilityError as exc:
        print(
            f"✗ M2 的能力计划配不起来：{_reason(exc)}\n"
            "  M2 固定请求 high reasoning，而这对 route 没有审计过的能力声明（或 high 不受支持）。\n"
            "  已冻结的 profile：deepseek-v4-flash @ https://api.deepseek.com"
            "（docs/M2_ENDPOINT_PROFILE.md，2026-08-02，不含 key）。\n"
            "  换模型就换 NH_LLM_BASE_URL / NH_LLM_MODEL 为一对**已登记**的 route；"
            "未知 route 预检失败，不静默降级。",
            file=sys.stderr,
        )
        return 1

    out_path = out if out is not None else stamped_path(Path("runs"))
    try:
        gate_input = run_gate(
            store,
            project,
            traps,
            config=config,
            plan=plan,
            repeats=repeats,
            out_path=out_path,
        )
        decision = decide(gate_input)
    except ProviderError as exc:
        print(
            f"✗ 模型调用失败，这一轮没跑完：{exc}\n  已经烧掉的那些生成在 {out_path} 里。",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"✗ {_reason(exc)}", file=sys.stderr)
        return 1

    # 「跑了几条陷阱、几次生成」是**成功输出本身**，不是装饰：一份零陷阱的 run 会打印
    # 一张漂亮的裁决表 + exit 0，数字印出来，人一眼看得见。
    generations = len(gate_input.traps) * len(("x0", "x1", "x2")) * repeats
    lines_on_disk = sum(1 for _ in out_path.open(encoding="utf-8"))
    print(
        f"✓ 跑完：{len(gate_input.traps)} 条陷阱 × 3 臂 × {repeats} 次 = {generations} 次生成"
        f"（另 {len(gate_input.traps)} 次 reference 判分）。\n"
        f"  落盘 {lines_on_disk} 行：{out_path}\n"
        f"  每一次生成的**完整 prompt** 都在里面——「tell 漏进 prompt」这个最贵的错误，"
        "唯一的发现办法是人去读它（ADR 0010）。"
    )
    print(_box(f"kill-gate 裁决 · {decision.verdict.value}", _gate_lines(decision, repeats)))
    if decision.verdict is Verdict.INVALID:
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m novel_harness.gate",
        description="跑一轮 M2「防泄漏」考试并出裁决（会调模型、会花钱，维护者专用）。",
    )
    parser.add_argument("--db", required=True, help="SQLite 库（合成小册子那本）")
    parser.add_argument("--project", "-p", required=True, help="project_id")
    parser.add_argument(
        "--ground-truth", required=True, type=Path, help="synth/build.py 生成的 ground_truth.json"
    )
    parser.add_argument("--repeats", type=int, default=3, help="每条陷阱每臂跑几次。只接受 3 或 5")
    parser.add_argument("--out", type=Path, help="jsonl 落盘路径。默认 runs/<时间戳>.jsonl；已有文件拒绝覆盖")
    args = parser.parse_args(argv)

    if args.repeats not in (3, 5):
        print(f"重复次数只接受 3 或 5（§5 首轮 3，修正案 8 升级 5），实得 {args.repeats}。", file=sys.stderr)
        return 1

    return run(
        db=Path(args.db),
        project=args.project,
        ground_truth=args.ground_truth,
        repeats=args.repeats,
        out=args.out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
