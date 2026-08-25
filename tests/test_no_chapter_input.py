"""约束 10 的守卫：**作者永不填章号，一次都不行**（ARCHITECTURE §10 / PLAN §5.9）。

> 问作者「这条关系从第几章开始有效」，他不记得（200 万字写了三年，他连主角哪章突破
> 金丹都要翻）。他会填 1 → 时态模型退化成当前值快照图 → 这个项目全部差异化的地基没了。
> **表单里有章号输入框 = 邀请污染。**

修法是：`valid_from` 只由证据决定。作者确认的是「这条事实在这段原文里出现过」——他看着
原文点，零记忆负担——系统自己反推出那是第几章。血统整条是：

    edge.valid_from_chapter ← evidence.chapter_number ← chapter.number
      ← ChapterSpec.number ← importer ← chapterize 的 index ← 文本顺序

**这条约束之前只有 docstring 里的一段话**，没有任何自动化断言。而它是那种「加一个
字段就能让用户少被拒一次」的约束——那个下午一定会来，来的时候没有任何东西会拦住它，
因为填错章号的产物是一条 `valid_from` 错了的 CANON 边，**而它在面板上长得完全正常**。

> 2026-08-20 裁剪：**命令面（CLI）已删**，作者的输入只剩 Web 工作台的 API。所以这套
> 守卫如今扫的是三层——`declare.py` 的一处 AST（`valid_from_chapter=` 的赋值）、
> 改正层的 Pydantic 入参模型/函数签名、以及路径上唯一那个章号的 `ge=1` 下界。

── 为什么是一个新文件，不塞进 `test_arch_guard.py` ────────────────────────

那份守卫治的是「时态过滤写第二遍」，判据是「谁在碰图表」。约束 10 是另一个问题、另一条
判据（「作者的输入能不能到达 valid_from」）。塞进去会把一份判据清晰的守卫变成杂物间，
而那个文件自己的 docstring 把代价写得很清楚：**误报会让人把守卫关掉，而关掉的守卫等于
没有守卫。** 两条判据混在一个文件里，关掉其中一条的最省事办法就是关掉整个文件。

── 它拦不住什么（诚实说明）──────────────────────────────────────────────

- 它扫的是 `declare.py` 的一处 AST、Pydantic 入参模型的字段、改正层函数的参数名。一个把
  章号藏在 `--quote "第88章:他终于明白…"` 里解析出来的实现，这里一条都扫不到。没有哪道
  守卫是完备的——它拦住的是**顺手写出来的那一种**，而那一种正是会真的发生的那一种。
- PLANNED 边的 `valid_from` **不是这里管的**：它按设计由作者打算（001_init.sql 逐字写了
  「那不是回忆是决定」），走的是另一条写路径，不属于「作者填章号」这一加农。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

DECLARE_PY = Path(__file__).resolve().parents[1] / "src" / "novel_harness" / "declare.py"

BANNED = re.compile(r"chapter|valid_from|valid_to|since|^ch$|^at$", re.IGNORECASE)
"""参数/字段名里撞上它 = 一个章号输入框。

`^ch$` / `^at$` 用锚而不是裸词：`ch` 和 `at` 作为**整个名字**是缩写的章号和 AS OF，
但作为子串它们在 `search` / `match` / `path` 里无处不在——不加锚这道守卫会对着
合法的 `path` 参数开火，然后被关掉。
"""


def valid_from_assignments(source: str, filename: str = "<probe>") -> list[tuple[int, str, str]]:
    """`valid_from_chapter=` 这个**关键字实参**的每一次出现：`(行号, 值的 AST 类型, 值的源码)`。

    扫关键字实参而不是 grep：`EdgeSpec` 是 `extra="forbid"` 的 Pydantic 模型，
    给 `valid_from_chapter` 赋值的唯一形状就是一个关键字实参。docstring 和注释里
    引着它的地方（`declare.py` 有好几处）不是赋值，AST 天然不会把它们算进来。
    """
    tree = ast.parse(source, filename=filename)
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "valid_from_chapter":
            out.append((node.lineno, type(node.value).__name__, ast.unparse(node.value)))
    return sorted(out)


# ══════════════════════════════════════════════════════════════════════════
# 守卫
# ══════════════════════════════════════════════════════════════════════════


def test_valid_from_is_assigned_exactly_once_in_declare() -> None:
    """`declare.py` 里 `valid_from_chapter=` **只许出现一次**，且值必须是 `ev.chapter_number`。

    第二次赋值一定意味着有第二条路给它填数——那正是「退化成快照图」的形态。而值必须是
    一个 `ast.Attribute`（从证据上取的属性）而不是常量或参数名：`valid_from_chapter=1`
    和 `valid_from_chapter=chapter` 都能编译、都能跑、都能在面板上画得很正常。

    与 `queries.TEMPORAL_WHERE` 只写一次是同一个手法：**把「只有一处」从一件 review
    事项变成一件 CI 事项。** review 会累，CI 不会。
    """
    found = valid_from_assignments(DECLARE_PY.read_text(encoding="utf-8"), "declare.py")

    assert len(found) == 1, (
        f"declare.py 里给 valid_from_chapter 赋值了 {len(found)} 次：{found}。\n"
        "它只许有一处（§5.9 / 约束 10）——第二处一定是第二条让章号进来的路。"
    )
    lineno, kind, source = found[0]
    assert kind == "Attribute", (
        f"declare.py:{lineno} 把 valid_from_chapter 赋成了 {kind}（`{source}`）。\n"
        "它只能是证据上的一个属性（`ev.chapter_number`）：常量和参数名都能跑、都能在\n"
        "面板上画得很正常，而它们的产物是一条时间错了的 CANON 边。"
    )
    assert source.endswith(".chapter_number"), (
        f"declare.py:{lineno} 的 valid_from_chapter = `{source}`，它必须取自证据的章号。"
    )


CONSTANT_PROBE = """
def declare_edge(ev, spec_cls):
    return spec_cls(src="a", dst="b", valid_from_chapter=88)
"""

PARAM_PROBE = """
def declare_edge(ev, chapter, spec_cls):
    return spec_cls(src="a", dst="b", valid_from_chapter=chapter)
"""

TWICE_PROBE = """
def declare_edge(ev, spec_cls):
    if ev.planned:
        return spec_cls(valid_from_chapter=ev.planned_chapter)
    return spec_cls(valid_from_chapter=ev.chapter_number)
"""

DOCSTRING_PROBE = '''
def declare_edge(ev, spec_cls):
    """按 §5.9，这里写 valid_from_chapter=88 是不行的。"""
    return spec_cls(valid_from_chapter=ev.chapter_number)
'''


def test_the_ast_guard_can_see_a_hand_typed_chapter() -> None:
    """三种真实的坏形态各喂一次。都能编译、都能跑、都能在面板上画得很正常。"""
    assert valid_from_assignments(CONSTANT_PROBE) == [(3, "Constant", "88")]
    assert valid_from_assignments(PARAM_PROBE) == [(3, "Name", "chapter")]

    twice = valid_from_assignments(TWICE_PROBE)
    assert len(twice) == 2, "两条路给 valid_from 填数，扫描器必须都看见"

    # docstring 里引着它的地方不是赋值。误报会让人把守卫关掉，而 declare.py 的
    # docstring 到处在讲 valid_from。
    assert valid_from_assignments(DOCSTRING_PROBE) == [(4, "Attribute", "ev.chapter_number")]


# ══════════════════════════════════════════════════════════════════════════
# 第四道：**改**一条已生效事实的入参里也没有章号
# ══════════════════════════════════════════════════════════════════════════
#
# 前三道盯的是「新增」那条路（`declare.py`）。作者推翻「事前逐条确认」之后多出来一条路：
# 抽取直接生效，作者事后**改**（`corrections.py` + `/canon/…`）。那条路上「改一条 CANON
# 事实」离「顺手也让他改一下这条事实从第几章开始成立」只有一个字段的距离，而那个字段
# 一旦有了，作者填的就是他不记得的那个数（§5.9）。
#
# 判据：扫 **Pydantic 入参模型的字段名** 和 **改正层函数的参数名**。出参不扫——
# `KnowledgeCorrection.since_chapter` 是一个**产物**，它必须在（面板要渲染「✓ 知道
# (ch88)」），扫它等于要求这个产品别告诉作者章号。


def model_chapter_fields(model: Any) -> list[str]:
    """一个 Pydantic 模型上撞了章号的字段名。"""
    return [name for name in model.model_fields if BANNED.search(name)]


def callable_chapter_params(func: Any) -> list[str]:
    import inspect

    return [name for name in inspect.signature(func).parameters if BANNED.search(name)]


def test_fact_edit_request_schemas_have_no_chapter_field() -> None:
    """三个改正入参模型（`/canon/events/{id}/cast` + 提案 `edit`）里一个章号字段都没有。

    （2026-08-14 起这张单子上还有 `KnowledgeAddRequest`，它那条路由的路径上就有一个
    `{chapter}`，是最容易长出章号的一个。它随秘密下线一起删了，ADR 0039——
    **那条理由仍然是这张单子的判据**：路径上已经有了 ≠ 请求体里可以顺手也收一个。）
    """
    from novel_harness.api.review import (
        EventCastEditRequest,
        ProposalEditRequest,
    )
    from novel_harness.extract.proposal_models import ProposalReview

    offenders = {
        model.__name__: model_chapter_fields(model)
        for model in (
            EventCastEditRequest,
            ProposalEditRequest,
            ProposalReview,
        )
        if model_chapter_fields(model)
    }
    assert not offenders, (
        f"改正入参里出现了章号字段：{offenders}。\n"
        "改一条事实**说错了**和改它**从第几章开始成立**是两件事：后者只由证据决定，\n"
        "新事实的 valid_from 只能从被改的那条上继承（约束 10 / ADR 0006）。"
    )


def test_the_correction_layer_takes_no_chapter_argument() -> None:
    """**改**那几条一个章号参数都没有：它们的 `valid_from` 从被改的那条事实上继承。

    （`add_knowledge` 曾经是**逐个点名**的唯一例外，由下面那条单独钉着；
    它随秘密下线一起删了，ADR 0039，那条守卫跟着走——**这张单子因此重新是全称的**）
    ——理由和形式写在那儿（同 `test_canon_edit_boundary.NAMED_CHAPTER_BOXES` 的做法：
    例外写清楚为什么正当，**不是**在这条守卫上开一个「名字里带 chapter 就放过」的口子）。
    """
    from novel_harness.corrections import correct_event_cast
    from novel_harness.graph.sqlite_events import SqliteEventStore

    offenders = {
        func.__qualname__: callable_chapter_params(func)
        for func in (correct_event_cast, SqliteEventStore.edit_cast)
        if callable_chapter_params(func)
    }
    assert not offenders, f"改正层长出了章号参数：{offenders}"
def test_the_schema_guard_can_see_a_chapter_field() -> None:
    """**守卫的自守卫**：喂一个真带章号的模型/函数进去，它必须红。

    没有这一条，上面两条在 `model_fields` 换形状（pydantic 升级）的那天会安静地全绿。
    """
    from pydantic import BaseModel

    class _Probe(BaseModel):
        character_id: str
        since_chapter: int

    def _probe(who: str, valid_from: int) -> None: ...

    assert model_chapter_fields(_Probe) == ["since_chapter"]
    assert callable_chapter_params(_probe) == ["valid_from"]

    class _Clean(BaseModel):
        character_id: str
        believed_value: str

    assert model_chapter_fields(_Clean) == [], "干净的模型不许被咬——假红会让人关掉守卫"
