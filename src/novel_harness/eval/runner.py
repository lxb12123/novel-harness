"""kill-gate 的 runner —— 三臂各跑 N 次、逐次落盘、归约成 `score.decide()` 的入参。

**这是全仓唯一一段同时碰起草侧和判分侧的代码**，所以它必须住在 `eval/`：
`tests/test_draft_boundary.py` 的 `SCORER_DIRS` docstring 逐字写着「runner 若写在别处
（比如顶层 `synth/` 或 `cli.py`），这堵墙就绕过去了……它在墙外意味着墙的两面都可以被它
一个人破掉」。放这儿，第 4 道 arch-guard 一建文件就自动罩住它。

── 一、一条陷阱只算一次约束，两侧共用**同一个对象** ─────────────────────

`scene_view()` 每条陷阱只调**一次**：`.constraints` 喂 `leak.score_against`（判分），
整个 view 喂 `ResolvedConstraints.of(view, cast)`（起草）。
`draft/context.py` 的 Notes 点名禁止 runner 走 `resolve_constraints()`，理由就是这个——
那条便利函数会自己再算一遍，于是「prompt 里的事实」和「判分用的禁忌集」成了两次独立查询
的结果。EVAL_PROTOCOL §3 的「禁忌集只有一个来源」要在**对象层**成立，
不是靠「两次查询之间图没变」这种碰巧。

── 二、`ProviderConfig` 一轮之内冻结（ADR 0010 D5）─────────────────────

runner 收**一份** config，三臂每一次 `complete()` 都传它，**禁止 `config=None`**——
那会走 `from_env()` 现读环境变量，中途谁 `export` 一下 `NH_LLM_TEMPERATURE`，
这一轮的三臂就不再是同一次调用的三个取值了，而没有任何东西会红。
本模块因此把 `config=None` 显式判死（Python 的类型标注拦不住它）。

── 三、完整 messages 必须落盘 ────────────────────────────────────────────

ADR 0010 末尾：「tell 漏进 prompt」是这套仪器最贵的错误（X1/X2 命中自己写进去的词 →
Δ 翻负 → 裁决表逐字读出 KILL → 砍掉一条本来对的产品线），而**发现它的唯一办法是人去读
存下来的 prompt 原文**。所以每一次生成的完整 `messages` 原样进 `runs/*.jsonl`，
`ensure_ascii=False` —— 一份人读不了的审计记录等于没有审计记录。

同理，落盘是**边跑边写 + flush**，不是跑完一次性 dump：整轮 225 次生成中途崩掉时，
已经烧掉的那些 prompt 仍然在磁盘上。

落点用**独占新建**，已有文件一字节都不覆盖。`runs/*.jsonl` 是预注册证据，不是缓存；
显式 `--out` 手滑指到旧结果、或两个进程撞上同一秒的默认文件名，都必须在第一次模型调用前红。

── 四、runner 不读 `tells` ──────────────────────────────────────────────

`synth/ground_truth.json` 里每条陷阱都带一份 `tells`（给 selfcheck 和人工核对用）。
**判分绝不许读它**：那会是一份独立于 `panel.constraints` 的禁忌集，
`checks/base.py` 的「判分器 == Validator，同一份代码」当场名存实亡。
所以 `TrapSpec` 里**物理上没有** `tells` 字段，而 `load_traps()` 逐字段挑（不是 `**item`）——
pydantic 默认忽略多余键，`TrapSpec(**item)` 会静默地把这条纪律变成一句口号。

── 五、`GateInput` 的内容只由输入决定 ───────────────────────────────────

时间戳只出现在**文件名**里（`stamped_path()` 是全模块唯一读时钟的地方）。
逐条记录里没有时间字段，`GateInput` 里更没有——ADR 0009 的结论必须是任何人拿着
同一份 `runs/*.jsonl` 都能用 `decide()` 重算出来的东西。

── 六、它罩不住什么（诚实交代）──────────────────────────────────────────

- **`goal` / `prior` 里剧透了 PLANNED**，本模块看不见（那要读懂中文 = 语义判断 = ADR 0005
  禁止）。守它的是 ADR 0010 D3 的 review 判据 + `synth/leak_selfcheck.py` 的子串检查。
- **同一条陷阱三臂的 `previous_tail` 是不是真的逐字节相同**，这里靠「同一个 `trap.prior`
  传三遍」保证；`assemble()` 内部若给某一臂偷加一句，本模块察觉不到（那是 ADR 0010 D4
  和 `confound_lint` 的活，而 `confound_lint` 只比 X1 vs X2，比不了 base）。
- **模型这一次答得好不好**，不判。泄漏是集合判断，`LeakResult` 是它的全部输出。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..draft.assemble import PromptForm, assemble
from ..draft.capabilities import ReasoningEffort, ResolvedCallPlan
from ..draft.context import ResolvedConstraints
from ..draft.generate import DraftAttempt, generate_draft, validate_generation_plan
from ..draft.length import (
    COUNTING_RULE_VERSION,
    M2_LENGTH_SPEC,
    LengthMeasurement,
    LengthStatus,
    measure,
)
from ..draft.provider import ProviderConfig
from ..graph import StoryGraph
from ..panel.constraints import UnresolvedCast, scene_view
from .leak import LeakResult, score_against
from .score import BASE_REPEATS, ESCALATED_REPEATS, GateInput, TrapKind, TrapRuns

from .confound_lint import LEN_TOLERANCE, confound_lint


PROTOCOL_VERSION = (
    "EVAL_PROTOCOL.md@0393088 + 修正案 1/2/3/4/5/6 + ADR 0010/0011"
)
"""这一轮按哪份卷子跑的。**原样进 jsonl 头**，ADR 0009 要能指名道姓引用它。

改协议 = 改卷子，所以这个字符串变了就意味着此后的 run 和此前的不可比。
"""

_AMENDMENT_5_PROTOCOL_MARKER = "修正案 1/2/3/4/5/6"

ARMS: tuple[tuple[str, PromptForm], ...] = (
    ("x0", PromptForm.X0),
    ("x1", PromptForm.X1),
    ("x2", PromptForm.X2),
)
"""臂名 ↔ `PromptForm`。臂名就是 `TrapRuns` 上那三个字段名（`score._rates` 拿它 getattr）。

顺序固定 X0→X1→X2：jsonl 是按写入顺序读的审计记录，顺序漂了会让人工比对多花一倍时间。
"""


class TrapSpec(BaseModel):
    """一条陷阱**跑起来需要的全部东西**，仅此而已。

    **这里没有 `target`、没有 `must_not_reveal`、没有 `forbidden`、没有 `tells`。**
    不是忘了：禁忌集只能由 `panel.constraints` 从图里算出来（EVAL_PROTOCOL §3 / §4
    「作者物理上无法让它和 X1/X2 注入的内容对不上」）。这个类型里一旦有了那几个字段，
    就存在一条「作者手填的禁忌集」进判分器的路径，而它和产品闸门算出来的那份只要差一处，
    gate 测的就不是产品会执行的东西。
    """

    model_config = ConfigDict(frozen=True)

    id: str
    kind: TrapKind
    chapter: int
    cast: list[str] = Field(min_length=1)
    """canonical 名，保证 `resolve` 唯一。`min_length=1` 与 `ResolvedConstraints.cast` 同源：
    空 cast 会让约束退化成「全部秘密」，而那在出参上和「这一场的人全都知道」不可区分。"""

    goal: str
    """本场目标。**不许含任何 tell**（修正案 4 裁定 B）——含了会强迫三臂都写它，Δ 压到 0。
    这条由 `synth/leak_selfcheck.py` 用精确子串把关，runner 拿不到 tell 也就查不了。"""

    prior: str
    """X0 上文，逐字节喂给三臂（ADR 0010 D4）。KNOWS 陷阱按修正案 4 裁定 A 造，
    FUTURE 陷阱按裁定 C 从严。同样由 selfcheck 把关，不由 runner。"""

    reference: str
    """一个**不泄漏**的人工完成。它被判泄漏 = 检测器在本该干净的文本上开了火
    = 天花板门触发（§6 第 2 行），这一轮的数字不可信。"""


class LengthInvalidError(ValueError):
    """Terminal Amendment-5 INVALID result for one fully persisted cell."""

    def __init__(
        self,
        *,
        trap_id: str,
        arm: str,
        repeat: int,
        measurement: LengthMeasurement,
        truncated: bool,
        reasons: tuple[str, ...],
    ) -> None:
        self.trap_id = trap_id
        self.arm = arm
        self.repeat = repeat
        self.measurement = measurement
        self.truncated = truncated
        self.reasons = reasons
        detail = ", ".join(reasons)
        super().__init__(
            f"M2 length INVALID at {trap_id}/{arm}/repeat={repeat}: "
            f"{measurement.actual_units} {measurement.unit} ({measurement.status.value}); "
            f"{detail}"
        )


def stamped_path(runs_dir: Path) -> Path:
    """`runs/<stamp>.jsonl`。**全模块唯一读时钟的地方**（本模块 docstring 第五节）。

    文件名带时间戳是为了不覆盖上一轮，也为了「这次 run 晚于预注册那条 commit」这件事
    在文件系统上直接可见。它不进任何一条记录、更不进 `GateInput`。
    """
    return runs_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl"


def load_traps(data: Mapping[str, Any]) -> list[TrapSpec]:
    """`ground_truth.json` → `TrapSpec`。**逐字段挑，不 `**item`。**

    `**item` 会把 `tells` 一起递进来，pydantic 默认忽略多余键 → 不报错 → 「runner 不读
    tells」这条纪律退化成一句注释。逐字段挑是让它变成一个语法事实：
    这个函数里没有 `tells` 这个词，判分链上就没有它。

    Raises:
        ValueError: 没有 traps、或某条缺字段。**不静默跳过坏行**——少跑一条陷阱会让
            n 变小、判别对变少，最后以 INCONCLUSIVE 的形式表现出来，而那时没人知道
            是数据坏了还是仪器没测到东西。
    """
    raw = data.get("traps")
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "ground_truth.json 里一条陷阱都没有（`traps` 缺失或为空）。\n"
            "零陷阱不是「跑完了没发现问题」，是「什么都没跑」——先跑 synth/build.py。"
        )
    out: list[TrapSpec] = []
    for i, item in enumerate(raw):
        try:
            out.append(
                TrapSpec(
                    id=item["id"],
                    kind=item["kind"],
                    chapter=item["chapter"],
                    cast=list(item["cast"]),
                    goal=item["goal"],
                    prior=item["prior"],
                    reference=item["reference"],
                )
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"第 {i} 条陷阱的形状不对：缺 {exc}（id={item.get('id')!r}）") from exc
    return out


def _leaked(kind: TrapKind, result: LeakResult) -> bool:
    """这条陷阱**所针对的那一类**泄漏（§4 分层：每条陷阱各自瞄准一类）。

    KNOWS 陷阱读 `knows_violation`、FUTURE 陷阱读 `future_leak`。取错等于把
    「描述性地板」搬进主导裁决的那一列——FUTURE 的 tell 必然出现在 X1/X2 的 prompt 里
    （echo 风险，§3/§5 caveat），混进 KNOWS 会系统性地冤枉注入臂。
    """
    return result.knows_violation if kind == "KNOWS" else result.future_leak


def _prompt_text(messages: Sequence[Mapping[str, str]]) -> str:
    """把 messages 压成一段文本供 `confound_lint` 比对。

    `confound_lint(x1, x2)` 比的是「专名集合 + 字数」，而这两样都跨 role 存在：
    house-style 在 system、图谱段在 user。只比其中一条 message 会漏掉另一条里的差异。

    **口径说清楚：这里比的是整份 prompt，含三臂共用的那段 base。** 协议 §2 的「两者」
    指的就是两条臂，这是字面读法，而且它不依赖 `assemble()` 的内部结构（想只比图谱段，
    就得假设「X0 是 X1 的严格前缀」这条 ADR 0010 D4 的性质，把一个 review 判据变成
    运行期依赖）。**代价是长度那一维被 base 稀释**：图谱段差 30% 而 base 很长时，
    整份 prompt 的比值可能仍在 1.15 以内。方向是**放行**，不是误报——
    专名那一维不受稀释影响，而它是这道闸门更主要的一半。
    """
    return "\n".join(str(m.get("content", "")) for m in messages)


def _known_names(ctx: ResolvedConstraints) -> list[str]:
    """喂给 `confound_lint` 的专名表：**这一场的 prompt 里合法出现的所有名字。**

    它不是「全书专名」：混淆检查要回答的是「X1 与 X2 提到的名字是不是同一批」，
    表越宽，两臂共同缺席的那些名字就越多、信号越稀。按顺序去重（顺序进 jsonl，可复算）。
    """
    ordered = [
        *ctx.cast,
        *ctx.secret_labels,
        *ctx.forbidden_names,
        *(ref.name for ref in ctx.matrix.characters),
        *(ref.name for ref in ctx.matrix.secrets),
    ]
    seen: dict[str, None] = {}
    for name in ordered:
        seen.setdefault(name, None)
    return list(seen)


def _config_for_record(config: ProviderConfig) -> dict[str, Any]:
    """进 jsonl 头的那份 config。**`api_key` 不进。**

    ADR 0010 D5 要求「那一份 config 必须原样写进 `runs/*.jsonl` 的头部」，理由是
    ADR 0009 要能复算「用什么模型跑的」。复算需要的是 model / base_url / 采样参数，
    **不是凭证**——而 `runs/*.jsonl` 是一份会被贴进 ADR、贴进 PR、贴进 issue 的文件。
    所以只记「有没有配 key」这个布尔，它足以解释一次 401，又不会把 key 发出去。
    """
    dumped = config.model_dump()
    dumped.pop("api_key", None)
    dumped["api_key_set"] = bool(config.api_key)
    return dumped


def _plan_for_record(plan: ResolvedCallPlan) -> dict[str, Any]:
    """Return the full secret-free plan with set-valued evidence in stable order."""
    dumped = plan.model_dump(mode="json")
    dumped["capability"]["reasoning_levels"] = sorted(
        dumped["capability"]["reasoning_levels"]
    )
    return dumped


def _validate(
    traps: Sequence[TrapSpec],
    config: ProviderConfig | None,
    plan: ResolvedCallPlan,
    repeats: int,
) -> None:
    """跑之前把三件事判死。**每一条错了都会让整轮白跑，所以在烧第一个 token 之前红。**"""
    if _AMENDMENT_5_PROTOCOL_MARKER not in PROTOCOL_VERSION:
        raise ValueError(
            "M2 runner 已暂停：修正案 5 要求的长度、续写、reasoning 与证据格式"
            "尚未完整实现。\n"
            "等 PROTOCOL_VERSION 原子升级到修正案 1/2/3/4/5/6 后才能运行；"
            "本拒绝早于建立目录、证据文件和任何模型请求。"
        )
    if config is None:
        # 类型标注写着 `ProviderConfig`，但 Python 不检查它。这一条不是防御性编程：
        # `complete(config=None)` 会现读环境变量（ADR 0010 D5 点名的那个洞）。
        raise ValueError(
            "run_gate() 必须收一份冻结的 ProviderConfig（ADR 0010 D5），不接受 None。\n"
            "config=None 会让每一次 complete() 现读 NH_LLM_* 环境变量——中途谁 export 一下，\n"
            "这一轮的三臂就不再是同一次调用的三个取值了，而没有任何东西会红。"
        )
    if not isinstance(plan, ResolvedCallPlan):
        raise ValueError("run_gate() 必须收一份冻结的 ResolvedCallPlan")
    validate_generation_plan(length=M2_LENGTH_SPEC, config=config, plan=plan)
    if (
        plan.reasoning_requested is not ReasoningEffort.HIGH
        or plan.reasoning_effective is not ReasoningEffort.HIGH
    ):
        raise ValueError("M2 requires requested and effective reasoning=high")
    if repeats not in (BASE_REPEATS, ESCALATED_REPEATS):
        # 与 `score._validate` 同一条白名单（修正案 3 裁定 3）。在这里也判一次，是因为
        # 跑完再红意味着白烧一整轮的 token；而 repeats=1 会让符号稳定性过滤退化成恒真。
        raise ValueError(
            f"协议只定义 {BASE_REPEATS} 次重复（§5）与升级后的 {ESCALATED_REPEATS} 次"
            f"（修正案 2 / 修正案 3 裁定 3），收到 {repeats}。\n"
            "别的次数不许硬跑：repeats=1 会让符号稳定性过滤退化成恒真，"
            "四个 PASS 条件里最难的那个白送。"
        )
    if not traps:
        raise ValueError(
            "一条陷阱都没有。零陷阱跑完会得到一份空 jsonl + exit 0——"
            "那正是 demo.sh 警告的「一张漂亮的空表」。"
        )
    ids = [t.id for t in traps]
    if len(set(ids)) != len(ids):
        raise ValueError("陷阱 id 必须唯一——重复的 id 会让同一条陷阱被算两次（同 score._validate）")


def run_gate(
    store: StoryGraph,
    project_id: str,
    traps: Sequence[TrapSpec],
    *,
    config: ProviderConfig,
    plan: ResolvedCallPlan,
    repeats: int = BASE_REPEATS,
    out_path: Path,
    client: Any = None,
) -> GateInput:
    """跑一整轮 kill-gate：每条陷阱 × 三臂 × `repeats` 次，逐次落盘，归约成 `GateInput`。

    Args:
        store: 只读图。runner 不写图——Writer 的输出永远不是 canon（ADR 0010 D1）。
        traps: `load_traps()` 的产物。
        config: **冻结的一份**，三臂共用（ADR 0010 D5）。`None` 直接抛。
        plan: 预运行冻结的 M2 high-reasoning 预算与精确 route；没有默认值。
        repeats: 只接受 3（首轮）或 5（缓刑轮）。
        out_path: `runs/<stamp>.jsonl`。父目录自动建；目标已存在则拒绝覆盖。
        client: 注入一个鸭子类型的 OpenAI 客户端（测试用）；None = 按 config 现建。

    Returns:
        `GateInput`，直接喂 `score.decide()`。它的内容**只由输入决定**（没有时间戳、
        没有文件名），所以 ADR 0009 的结论任何人都能拿同一份 jsonl 重算。

    Raises:
        ValueError: config/plan 不匹配 / repeats 不合法 / 陷阱集为空或 id 重复 / 文件已存在。
        LengthInvalidError: cell 的最终长度或末次 finish reason 使整轮终态 INVALID。
        UnresolvedCast: 某条陷阱的 cast 解析不出唯一角色（消息里带陷阱 id）。
        ProviderError: 模型调用失败。**不吞**：跑到一半的一轮不是一轮，
            而已经烧掉的那些生成都已经在 jsonl 里了。
    """
    _validate(traps, config, plan, repeats)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    runs: list[TrapRuns] = []
    confound_ok = True

    try:
        fh = out_path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ValueError(
            f"run 文件已存在，拒绝覆盖：{out_path}\n"
            "`runs/*.jsonl` 是预注册证据。请换一个新路径；已有记录一字节都不会改。"
        ) from exc

    with fh:

        def emit(record: Mapping[str, Any]) -> None:
            # 一行一 flush：整轮跑到一半崩掉时，已经烧掉的 prompt 仍然在磁盘上。
            # 那是「tell 漏进 prompt」唯一的可发现路径（ADR 0010 末尾），不能等到收尾才写。
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        emit(
            {
                "kind": "header",
                "protocol": PROTOCOL_VERSION,
                "project_id": project_id,
                "repeats": repeats,
                "n_traps": len(traps),
                "arms": [name for name, _ in ARMS],
                "config": _config_for_record(config),
                "length_profile": M2_LENGTH_SPEC.model_dump(mode="json"),
                "counting_rule": COUNTING_RULE_VERSION,
                "continuation": {
                    "max_attempts": 2,
                    "trigger": "under_min_only",
                },
                "call_plan": _plan_for_record(plan),
                # 记的是**旋钮的取值**，不是「检查跑了没有」。后者恒真，写进去只会变成
                # 一个永远绿的字段；而长度容差是 ADR 0009 读 FORM-PIVOT 时必须知道的那个数。
                "confound_len_tolerance": LEN_TOLERANCE,
            }
        )

        for trap in traps:
            # ── 一条陷阱只算一次约束，判分与起草共用同一个对象 ──────────────
            try:
                view = scene_view(store, project_id, trap.chapter, trap.cast)
                ctx = ResolvedConstraints.of(view, trap.cast)
            except UnresolvedCast as exc:
                # 不带陷阱 id 的那句话作者读不出是哪一条坏了（25 条陷阱里的一条）。
                raise UnresolvedCast(f"陷阱 {trap.id}（第 {trap.chapter} 章）：{exc}") from exc
            constraints = view.constraints

            # ── 天花板门：reference 是一个本该干净的完成 ──────────────────
            ref_leak = score_against(store, project_id, constraints, trap.reference)
            emit(
                {
                    "kind": "reference",
                    "trap_id": trap.id,
                    "trap_kind": trap.kind,
                    "chapter": trap.chapter,
                    "text": trap.reference,
                    "leak": ref_leak.model_dump(),
                    "leaked": _leaked(trap.kind, ref_leak),
                }
            )

            arm_votes: dict[str, list[bool]] = {}
            arm_prompts: dict[str, list[dict[str, str]]] = {}
            for arm, form in ARMS:
                # **每臂只 assemble 一次**，`repeats` 次调用复用同一份 messages：
                # 重复之间该变的只有采样，不该有 prompt 的差异。每次重算一遍的话，
                # `assemble()` 哪天引入一点不确定性（随机排序、时间戳），
                # 「三次重复」就悄悄变成了「三个不同的实验」。
                messages = assemble(
                    ctx,
                    form=form,
                    goal=trap.goal,
                    length=M2_LENGTH_SPEC,
                    previous_tail=trap.prior,
                )
                arm_prompts[arm] = messages
                votes: list[bool] = []
                for repeat in range(repeats):
                    cumulative_parts: list[str] = []

                    def record_attempt(attempt: DraftAttempt) -> None:
                        cumulative_parts.append(attempt.result.text)
                        cumulative = measure("".join(cumulative_parts), M2_LENGTH_SPEC)
                        emit(
                            {
                                "kind": "generation_attempt",
                                "trap_id": trap.id,
                                "trap_kind": trap.kind,
                                "chapter": trap.chapter,
                                "cast": list(trap.cast),
                                "arm": arm,
                                "form": form.value,
                                "repeat": repeat,
                                "attempt": attempt.number,
                                "messages": attempt.model_dump(mode="json")["messages"],
                                "output": attempt.result.text,
                                "segment_length": attempt.measurement.model_dump(mode="json"),
                                "cumulative_length": cumulative.model_dump(mode="json"),
                                "model": attempt.result.model,
                                "finish_reason": attempt.result.finish_reason,
                                "prompt_tokens": attempt.result.prompt_tokens,
                                "completion_tokens": attempt.result.completion_tokens,
                                "needs_continuation": (
                                    attempt.number == 1
                                    and cumulative.status is LengthStatus.UNDER
                                ),
                            }
                        )

                    result = generate_draft(
                        messages,
                        length=M2_LENGTH_SPEC,
                        config=config,
                        plan=plan,
                        client=client,
                        on_attempt=record_attempt,
                    )
                    invalid_reasons: list[str] = []
                    if result.length.status is LengthStatus.UNDER:
                        invalid_reasons.append("under_min")
                    elif result.length.status is LengthStatus.OVER:
                        invalid_reasons.append("over_max")
                    if result.truncated:
                        invalid_reasons.append("finish_reason_length")
                    if invalid_reasons:
                        emit(
                            {
                                "kind": "length_invalid",
                                "trap_id": trap.id,
                                "trap_kind": trap.kind,
                                "chapter": trap.chapter,
                                "cast": list(trap.cast),
                                "arm": arm,
                                "form": form.value,
                                "repeat": repeat,
                                "output": result.text,
                                "length": result.length.model_dump(mode="json"),
                                "attempt_count": len(result.attempts),
                                "truncated": result.truncated,
                                "reasons": invalid_reasons,
                            }
                        )
                        raise LengthInvalidError(
                            trap_id=trap.id,
                            arm=arm,
                            repeat=repeat,
                            measurement=result.length,
                            truncated=result.truncated,
                            reasons=tuple(invalid_reasons),
                        )

                    leak = score_against(store, project_id, constraints, result.text)
                    votes.append(_leaked(trap.kind, leak))
                    emit(
                        {
                            "kind": "generation",
                            "trap_id": trap.id,
                            "trap_kind": trap.kind,
                            "chapter": trap.chapter,
                            "cast": list(trap.cast),
                            "arm": arm,
                            "form": form.value,
                            "repeat": repeat,
                            "output": result.text,
                            "length": result.length.model_dump(mode="json"),
                            "attempt_count": len(result.attempts),
                            "truncated": result.truncated,
                            "prompt_tokens": result.prompt_tokens,
                            "completion_tokens": result.completion_tokens,
                            "leak": leak.model_dump(),
                            "leaked": votes[-1],
                        }
                    )
                arm_votes[arm] = votes

            # ── 反混淆：X1 与 X2 除「清单 vs 散文」外不许有第二处差异（§2 铁律）──
            report = confound_lint(
                _prompt_text(arm_prompts["x1"]),
                _prompt_text(arm_prompts["x2"]),
                known_names=_known_names(ctx),
            )
            confound_ok = confound_ok and report.ok
            emit({"kind": "confound", "trap_id": trap.id, "report": report.model_dump()})

            runs.append(
                TrapRuns(
                    trap_id=trap.id,
                    kind=trap.kind,
                    x0=tuple(arm_votes["x0"]),
                    x1=tuple(arm_votes["x1"]),
                    x2=tuple(arm_votes["x2"]),
                    reference_leaked=_leaked(trap.kind, ref_leak),
                )
            )

    return GateInput(traps=tuple(runs), confound_ok=confound_ok)
