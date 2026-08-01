"""反混淆闸门 —— X1 与 X2 之间除了「清单 vs 散文」还有没有第二处差异（EVAL_PROTOCOL §2）。

协议 §2 的**反混淆铁律**：X1 与 X2 必须从**同一个** `knowledge_matrix` 对象渲染，
除「清单 vs 散文」这一个变量外不许有第二处差异——它修的是 PLAN §5.7 那个内建混淆
（原设计里 X2 比 X1 多带一条行为指令，于是「X2 更好」永远分不清是叙事化的功劳
还是那条指令的功劳）。本模块是那条铁律**能被机器判的那一角**，不是那条铁律本身。

── 它的输出只驱动一个开关，而那个开关的两个位置代价不对称 ────────────────

`ok=False` **不是裁决**。协议 §6 第 3 行规定的动作是：本轮**禁用 FORM-PIVOT**
（决策 B 摘掉），**A 仍可评**；补救是「重渲染对齐后重跑 B」。消费点是
`eval/score.py::GateInput.confound_ok`（它那段 docstring 写了为什么 B 不能硬读）。

- **假警报**（该 ok 却报警）：丢掉决策 B 这一轮。B 是**次要**决策，且 §6 自带补救，
  重渲染重跑就回来了。便宜。
- **假放行**（该报警却 ok）：一份被污染的 B 被当真，生产默认可能因为一个错误的理由
  翻成 `NH_DRAFT_FORM=X2`，而没有任何东西会红。

**下面每一处需要取舍的地方都往「宁可报警」那边倒**，唯一的理由就是这个不对称。

── 一、专名是集合判断，不是分词 ──────────────────────────────────────────

判据是 `{n for n in known_names if n in x1}`：拿调用方给的**白名单**去正文里做子串命中。
**没有分词器、没有 NER、没有任何「找出这段文字里的专名」的逻辑**——那要回答
「这串字符是不是一个名字」，是语义判断，ADR 0005 的铁律在 v1 里禁止本仓库长出这种能力
（顺带：它还会给 `pyproject.toml` 引一个新的重依赖，而那份文件刚清掉一个没人用的死依赖）。

**代价**：白名单之外的名字对它**不存在**。X2 多点了一个 `known_names` 里没有的名字，
它一个字都看不见。调用方给的那份名单就是这道闸门的全部视野——名单来自
`ResolvedConstraints`（`cast` + `secret_labels` + `forbidden_names`），
名单漏了谁，闸门就对谁瞎。

── 二、字数口径：**非空白字符数** ────────────────────────────────────────

中文没有空格，`len(s.split())` 这类按词计数的口径在中文上直接失效，所以只能数字符。
数的是**非空白**字符（`str.isspace()`，含全角空格 `　` 与各种换行）：

X1 是清单、X2 是散文，清单每条一行，同样多的内容天然带更多换行。把空白算进去，
量到的就是**排版**，而排版正是这里唯一**允许**存在的那个差异——那样产生的报警全是假警报，
而**一道天天误报的守卫会被人关掉，关掉的守卫等于没有**（同 `test_draft_boundary.py`
里「守卫误报会被人关掉」那条）。

标点算数。剔标点要维护一张中英文标点表，那是个会漂移的本地化判断，
而它的量级远小于 15% 的容差——为它引入一张表，得不偿失。

── 三、±15% 取的是哪个比：`max/min ≤ 1.15`，且必须**对称** ────────────────

协议只写了「字数 ±15% 内」，**没写分母**。三种读法给出三条不同的线：
`|a−b|/max`、`|a−b|/min`、`|a−b|/x1`。这是一个必须做的判断，做完要说清它往哪边错。

1. **先排除 `|a−b|/x1`（以 X1 为基准）**：那样 `confound_lint(x1, x2)` 与
   `confound_lint(x2, x1)` 会给出不同裁决——**结论由「哪一列写在前面」决定，那不是裁决**。
   `score.decide()` 的 `max(Δ1, Δ2)` 平票 bug 是同一个病（修正案 3 的见证数据里，
   同一份数字只把 X1、X2 两列对调，裁决就从 KILL 翻成 PASS）。同样的坑不踩第二次。
2. 两个对称读法里取**更严**的 `|a−b|/min`，等价于 `max/min ≤ 1 + LEN_TOLERANCE`。
   另一个读法 `|a−b|/max` 要到 `max/min > 1.176` 才报警。取严的理由就是上面那条代价不对称。

**它往哪边错**：往**多报警**那边。一对真实相差 15.5% 的渲染（按 `/max` 算只有 13.4%）
会被拦下来，于是本轮的 B 白丢一次。这是本模块**有意**选的那一边，不是没想到。

边界**含等号**：`max/min == 1.15` 判 ok。阈值本身是预注册的一部分，
`tests/test_confound_lint.py::test_the_tolerance_is_the_preregistered_one` 钉着它——
它红了不是「测试过时了」，是有人动了协议 §2 的那个数。

── 四、空的东西一律报警：空集相等不是一致性证据 ──────────────────────────

三种「什么都没量到」的形状，全部判 `ok=False`：

- **一臂渲染出来是空的** → 这一轮的 B 根本没有被判者。
- **两臂都是空串** → 长度相等、专名集合相等，两条子检查逐字全过，而它们过得毫无信息。
- **两臂都不含任何已知专名**（含 `known_names` 本身为空）→ 集合确实相等，
  但那是两个空集相等。

理由是同一条：**这道闸门唯一的输出是一个「关掉就更保守」的开关，没有信息时它应该报那个
保守值。** 反过来设计（没看见东西所以全绿）就是这个仓库反复警告的
「一张漂亮的空表 + exit 0」——`demo.sh` 自己也在警告同一件事。

**代价说清楚**：调用方漏传 `known_names` 会当场收到报警，而不是静默通过。那是有意的。

── 五、它罩不住什么（这一节比上面四节都重要）────────────────────────────

- **行为指令，它完全看不见。** X2 多一句「注意不要写出秘密」：不含任何已知专名、
  长度增量远小于 15% → 四条子检查逐条全过、报 ok。而那**正是** PLAN §5.7 的内建混淆，
  正是协议 §2 要修掉的那一件事。守住它的**不是本模块**，是
  [ADR 0010](../../../docs/adr/0010-writer-boundary.md) D4「三臂共用同一个 `_base`，
  X1/X2 只在它之后追加图谱段」+ review。
  **别把本模块的绿灯读成「反混淆铁律成立」**——它只覆盖那条铁律的一角。
- 语气、句序、强调程度、隐含指令，一律看不见。
- **次数看不见**（集合不是多重集）：X1 提一次 `萧决`、X2 提十二次，专名集合相同。
  长度那一维**可能**兜住一点，但那是碰巧，不是设计。
- **它只比 X1 vs X2，从不看三臂共用的那个 base。** 有人把 tell 拼进 `goal`，
  三臂**同时**被污染，Δ 反而看不出异常，本模块也一个字看不见——ADR 0010 已经点名说过
  这一格（「`confound_lint` 也抓不到它，它比的是 X1 vs X2，不是 base」）。
  那一格归 `synth/leak_selfcheck.py`（修正案 4 裁定 B：`goal` 一律不许含 tell）。
- **互为子串的名字会连坐**：`known_names` 里同时有 `血枭` 和 `血枭盟` 时，
  写了后者的那一臂两个都算命中。方向上只会多报警，不会漏报警。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

LEN_TOLERANCE = 0.15
"""协议 §2「字数 ±15% 内」的那个 15%。**它是预注册的一部分，改它等于改卷子。**

分母的选择不在协议里，是本模块做的判断（模块 docstring 第三节）：
`max/min ≤ 1 + LEN_TOLERANCE`，对称、且是两个对称读法里更严的那个。
"""


def visible_len(text: str) -> int:
    """字数 = **非空白字符数**（模块 docstring 第二节）。

    公开而不是私有，是因为它编码的是一个**口径**而不是一个实现细节：
    「清单 vs 散文」这个唯一允许的差异主要体现在换行上，把换行算进字数会让闸门
    量到排版而不是内容。这个口径由 `test_visible_len_ignores_layout` 直接钉住。
    """
    return sum(1 for ch in text if not ch.isspace())


class ConfoundReport(BaseModel):
    """一次反混淆比对的结果。**`ok` 是「`reasons` 为空」的同义词**，不是第三个独立判断。

    每一条子检查**都跑完**、都往 `reasons` 里写，不短路。跟 `score.py::_pass_checks`
    是同一个道理：落空时要能说出**哪一条**落空了。这段话会原样进 ADR 0009，
    而一份说错了自己失败原因的记录比没有记录更糟——它会让下一轮去改一个没问题的地方。
    """

    model_config = ConfigDict(frozen=True)

    ok: bool
    """`not reasons`。False = 本轮禁用 FORM-PIVOT（§6 第 3 行），**A 仍可评**，不是裁决。"""

    only_in_x1: list[str] = Field(default_factory=list)
    """只在 X1 里命中的已知专名，字典序。

    **报警必须说得出是谁**：动作是「重渲染对齐后重跑 B」，而没有名字的报警没法照着改。
    """

    only_in_x2: list[str] = Field(default_factory=list)
    """只在 X2 里命中的已知专名，字典序。"""

    len_x1: int
    """X1 的非空白字符数。"""

    len_x2: int
    """X2 的非空白字符数。"""

    len_ratio: float
    """`max/min`，恒 ≥ 1.0（两臂等长 = 1.0）。判据是 `≤ 1 + LEN_TOLERANCE`。

    **一臂为空、另一臂非空时是 `inf`**——那是数学上诚实的值，且那一轮无论如何都已经报警了。
    注意 pydantic 的 JSON 序列化默认把 `inf` 写成 `null`：runner 往 `runs/*.jsonl` 落盘时
    读 `reasons`，别只读这个数。
    """

    reasons: list[str] = Field(default_factory=list)
    """报警理由，人话，可能不止一条。空 ⟺ `ok`。"""


def confound_lint(x1: str, x2: str, *, known_names: Sequence[str]) -> ConfoundReport:
    """断言 X1 与 X2 只差「清单 vs 散文」：专名集合相同、字数在 ±15% 内。

    Args:
        x1: X1 臂渲染出来的 prompt 文本（清单形态）。
        x2: X2 臂渲染出来的 prompt 文本（散文形态）。**必须与 `x1` 出自同一个
            `ResolvedConstraints`**——那是 ADR 0010 D4 的事，本函数验不了。
        known_names: 白名单，通常是 `ctx.cast + ctx.secret_labels + ctx.forbidden_names`。
            空串 / 纯空白的名字会被丢掉（`"" in text` 恒真，留着它会让每一臂都「命中」
            一个不存在的名字）——同 `eval/leak.py::_hits` 里那个 `if t` 的道理。

    Returns:
        `ConfoundReport`。`ok=False` 时 `reasons` 逐条说明落空的是哪一项。

    Notes:
        **两个参数不是对称的名字，但判据必须是对称的**：把 `x1`、`x2` 对调，`ok` 不变、
        两个 `only_in_*` 互换（`test_the_verdict_does_not_depend_on_argument_order` 钉这条）。
        不对称的判据意味着裁决由「哪一列写在前面」决定——见模块 docstring 第三节。
    """
    names = {n for n in known_names if n.strip()}
    in_x1 = {n for n in names if n in x1}
    in_x2 = {n for n in names if n in x2}
    only_x1 = sorted(in_x1 - in_x2)
    only_x2 = sorted(in_x2 - in_x1)

    len_x1 = visible_len(x1)
    len_x2 = visible_len(x2)
    lo, hi = min(len_x1, len_x2), max(len_x1, len_x2)
    # 两臂都空时「等长」是真的（比值 1.0），空臂那条报警由下面单独一条负责——
    # 别拿 inf 去表达「都是空的」，那会把两件不同的事挤进同一个数字。
    ratio = 1.0 if hi == 0 else (float("inf") if lo == 0 else hi / lo)

    reasons: list[str] = []

    # ① 空臂：这一轮的 B 没有被判者。排在最前面，因为它一旦成立，后面两条量到的都是噪声。
    if lo == 0:
        which = "两臂都" if hi == 0 else ("X1" if len_x1 == 0 else "X2")
        reasons.append(
            f"{which}渲染出来是空的（非空白字符 X1={len_x1}、X2={len_x2}）"
            "——这一轮的决策 B 没有被判者"
        )

    # ② 专名集合不同 = 反混淆铁律被破了一处，且是**看得见名字**的那一处。
    if only_x1 or only_x2:
        reasons.append(
            f"专名集合不同：只在 X1 里的 {only_x1}、只在 X2 里的 {only_x2}"
            "（两臂必须从同一个矩阵渲染，协议 §2）"
        )

    # ③ 字数。空臂那一档已经报过，不再用 inf 重复报一次。
    if lo > 0 and ratio > 1 + LEN_TOLERANCE:
        reasons.append(
            f"字数比 {ratio:.3f} > {1 + LEN_TOLERANCE:.2f}"
            f"（非空白字符 X1={len_x1}、X2={len_x2}，口径见模块 docstring 第二节）"
        )

    # ④ 空集相等不是一致性证据，是没有证据（模块 docstring 第四节）。
    if not in_x1 and not in_x2:
        reasons.append(
            f"两臂都不含任何已知专名（known_names 去空后 {len(names)} 个）"
            "——专名这一维的比较是空的，没量到东西不等于对齐了"
        )

    return ConfoundReport(
        ok=not reasons,
        only_in_x1=only_x1,
        only_in_x2=only_x2,
        len_x1=len_x1,
        len_x2=len_x2,
        len_ratio=ratio,
        reasons=reasons,
    )
