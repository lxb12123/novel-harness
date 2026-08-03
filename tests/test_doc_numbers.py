"""文档里的数字 == 代码里的真值，而且全仓只许有一份。

2026-07-30 的审计发现的病不是「某个数字写错了」，是**同一个数字在六七份 `.md` 里各躺一份**：
「27 条路由」躺了七份、「620 个 pytest」躺了三份，而审计当天的真值是 32 和 681。改代码的人没有
义务记得去改七个地方——所以这个病不能靠纪律治，只能靠「只有一处 + 那一处会红」治。
（那个 681 在**本文件被加进来的同一天**就变成了 690——加 9 个测试就够了。
这正好是下面「罩不住什么」第一条的活证据：pytest 那个数至今只能靠人手改。）

这里两道守卫各管一半，缺一半都不成立：

1. `test_architecture_numbers_match_the_code` —— ARCHITECTURE.md「当前状态」里的数字
   必须等于**运行时从代码数出来的值**（不是另一份硬编码常量——那就成了第二份会漂的拷贝）。
2. `test_the_numbers_live_in_exactly_one_place` —— 这些数字**只许**出现在 ARCHITECTURE.md。
   别处写指针。第 1 道只能保证被它盯着的那一份是对的；没有第 2 道，第八份拷贝照样能长出来。

── 它罩不住什么（诚实交代）────────────────────────────────────────────────

- **「N 个 pytest」不验值。** 在 pytest 里数 pytest 要么递归要么另起进程，代价不值。
  它只受第 2 道管（只许有一份），值错了得靠人看。前端那几个数（`28 个手写源文件` /
  `2857 行`）同理，连模式都没进来——它们随每次改前端就漂，钉住等于天天假红。
- **只查「当前状态」那一节。** ARCHITECTURE.md 别处（§7 里程碑对未来的估算、§10 约束里
  举的例子）不查，也不该查——「M4 再加 8 条路由」不是一个关于今天的断言。
- **「」里的数字一律放过。** 那是引用一个旧值（当前状态那节自己就引着「27 条路由」讲
  这段病史），不是断言当前值。**代价是把数字包进「」就能绕过这道守卫**——但那么写读起来
  就是在引用历史，review 看得出来；而不放过的代价是这份文档没法讲自己的病史。
- **模式是白名单，窄于「所有数字」**，而且第 2 道比第 1 道还窄一档（`Fact.unique`）：
  501 stub 那个 5 是 `UI_ARCHITECTURE.md` §48 **提出的要求**，ARCHITECTURE 只是回答兑现了没有，
  两份文档各说各的份内事，不是拷贝。宁可窄一点漏掉几处，也不要假红——
  **守卫一旦假红就会被关掉，而关掉的守卫等于没有。**
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer
from fastapi.routing import APIRoute

from novel_harness.api.app import app as api_app
from novel_harness.checks import ALL_CHECKS
from novel_harness.cli import app as cli_app

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = REPO_ROOT / "docs" / "ARCHITECTURE.md"
MIGRATIONS = REPO_ROOT / "src" / "novel_harness" / "migrations"
FIXTURE = REPO_ROOT / "frontend" / "src" / "__fixtures__" / "api.json"

SECTION = "当前状态"


# ══════════════════════════════════════════════════════════════════════════
# 真值：每一条都在**运行时**数出来
# ══════════════════════════════════════════════════════════════════════════


def _routes() -> list[APIRoute]:
    """本仓自建的路由。

    过滤 `APIRoute` 就把 FastAPI 白送的 `/openapi.json` / `/docs` / `/redoc`
    （它们是 `starlette.routing.Route`）挡在外面了——数它们等于让「换个 FastAPI 版本」
    也能把文档判红。

    新版 FastAPI 把 `include_router` 挂载成 `_IncludedRouter` 占位而不是展开成
    `APIRoute`；不穿透它，`api/extraction.py` / `api/review.py` 的子路由就永远不在
    守卫视野里——「删掉一个真实路由」都不会红。
    """
    routes: list[APIRoute] = []
    for route in api_app.routes:
        if isinstance(route, APIRoute):
            routes.append(route)
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            routes.extend(r for r in original.routes if isinstance(r, APIRoute))
    return routes


def route_count() -> int:
    return len(_routes())


def api_route_count() -> int:
    return len([r for r in _routes() if r.path.startswith("/api")])


def stub_501_count() -> int:
    """声明 501 的 stub 路由（UI_ARCHITECTURE §48：让前端灰置按钮而不是藏起来）。"""
    return len([r for r in _routes() if getattr(r, "status_code", None) == 501])


def error_mapping_count() -> int:
    """我们自己注册的异常处理器。

    **不是** `len(app.exception_handlers)`：那里面还有 FastAPI 自带的三个默认处理器
    （`HTTPException` / `RequestValidationError` / `WebSocketRequestValidationError`），
    数量随 FastAPI 版本变——把它们算进来，升一次依赖就假红一次。
    也**不是**在源码里数 `@app.exception_handler`：那会被换行、注释、被注释掉的装饰器骗。
    按「处理器函数是不是我们写的」筛，两种骗法都躲不掉。
    """
    ours = "novel_harness.api.app"
    return len(
        [h for h in api_app.exception_handlers.values() if getattr(h, "__module__", "") == ours]
    )


def cli_leaf_count(group: typer.Typer | None = None) -> int:
    """`nh` 的**叶子**子命令数（`declare character` 算一条，`declare` 本身不算）。

    作者敲得出来的是叶子，中间那层分组敲了只会打印帮助。
    """
    group = cli_app if group is None else group
    return len(group.registered_commands) + sum(
        cli_leaf_count(g.typer_instance) for g in group.registered_groups
    )


def table_count() -> int:
    """迁移里建了几张表。扫整个 `migrations/`，不写死 `001_init.sql`——加了 002 得跟着变。"""
    return sum(
        len(re.findall(r"(?im)^\s*CREATE\s+TABLE\b", p.read_text(encoding="utf-8")))
        for p in sorted(MIGRATIONS.glob("*.sql"))
    )


def fixture_endpoint_count() -> int:
    """契约 fixture 冻了几个端点。这份 json 是 `test_frontend_contract.py` 从真 app dump 的。"""
    return len(json.loads(FIXTURE.read_text(encoding="utf-8")))


# ══════════════════════════════════════════════════════════════════════════
# 文档侧：把「会漂的数字断言」抓出来
# ══════════════════════════════════════════════════════════════════════════

_CORNER_QUOTED = re.compile(r"「[^「」]*」")


def strip_quoted(text: str) -> str:
    """去掉「」里的内容。

    这个仓库的写法里，「」包着的是**被引用的一段字**（旧值、别人的原话、测试名），
    不是在断言当前事实。当前状态那节自己就写着「620 个 pytest」在三份文档里各躺一份——
    不放过它，这份文档就没法讲自己的病史。
    """
    return _CORNER_QUOTED.sub("", text)


@dataclass(frozen=True)
class Fact:
    key: str
    """出错信息里用的人话名字。"""

    pattern: str
    """抓数字的正则，必须**只有一个**捕获组，且组里是那个数字。

    宽容度是刻意的：它要认得出作者会自然写出的那几种中文说法
    （「32 条自建路由」「31 条 /api」「其中 5 条是 501 stub」），
    但不能宽到把「27 条 issue」也算进来。
    """

    actual: Callable[[], int] | None
    """真值从哪儿来。`None` = 只受「唯一副本」那道管，不验值（见模块 docstring）。"""

    unique: bool = True
    """这条数字是不是「只许有一份」。"""


FACTS: tuple[Fact, ...] = (
    Fact("自建路由数", r"(\d+)\s*条(?:自建)?路由", route_count),
    Fact("/api 路由数", r"(\d+)\s*条\s*/api", api_route_count),
    # 501 的份数**不归 ARCHITECTURE.md 独占**：那个 5 是 UI_ARCHITECTURE §48 提出的要求
    # （「这 5 个按钮要灰着，不许藏」），ARCHITECTURE 只是回答「兑现了没有」。
    # 提要求的那份文档说不出自己要几条，就没法提要求了。所以只验值，不管唯一性。
    Fact("501 stub 数", r"(\d+)\s*条(?:是)?\s*501", stub_501_count, unique=False),
    Fact("错误映射数", r"(\d+)\s*个错误映射", error_mapping_count),
    Fact("CLI 叶子子命令数", r"(\d+)\s*个子命令", cli_leaf_count),
    Fact("migrations 建表数", r"(\d+)\s*张表", table_count),
    Fact("契约 fixture 端点数", r"(\d+)\s*个端点", fixture_endpoint_count),
    Fact("pytest 数", r"(\d+)\s*个 ?pytest", None),
    Fact("vitest 数", r"(\d+)\s*个 ?vitest", None),
)


def doc_numbers(text: str) -> dict[str, list[int]]:
    """一段 markdown 里，每条事实被声称成了几。抓不到的键不出现（**不是**空列表）。"""
    clean = strip_quoted(text)
    found: dict[str, list[int]] = {}
    for fact in FACTS:
        hits = [int(m.group(1)) for m in re.finditer(fact.pattern, clean)]
        if hits:
            found[fact.key] = hits
    return found


def mismatches(text: str) -> list[str]:
    """文档这段话和代码对不上的地方。空列表 = 全对。

    **抓不到也算错。** 静默 skip 的守卫比没有守卫更糟——它还额外提供一份安全感。
    """
    found = doc_numbers(text)
    bad: list[str] = []
    for fact in FACTS:
        if fact.actual is None:
            continue
        real = fact.actual()
        hits = found.get(fact.key)
        if not hits:
            bad.append(f"{fact.key}：文档里一句都没写（真值 {real}）。模式 {fact.pattern}")
        elif set(hits) != {real}:
            bad.append(f"{fact.key}：文档说 {sorted(set(hits))}，代码是 {real}")
    return bad


def section_of(text: str, heading: str) -> str:
    """截出某个 `##` 小节（到下一个 `##` 为止）。截不到返回空串，由调用方判死。"""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"## {heading}"), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


# ══════════════════════════════════════════════════════════════════════════
# 第 1 道：文档说的 == 代码是的
# ══════════════════════════════════════════════════════════════════════════


def test_architecture_numbers_match_the_code() -> None:
    """ARCHITECTURE.md「当前状态」里的每个数字都得是今天的真值。

    这一节被 CLAUDE.md / README.md 指认为**权威**，所以它错的时候没有第二处能兜底。
    """
    body = section_of(ARCHITECTURE.read_text(encoding="utf-8"), SECTION)
    assert body, f"ARCHITECTURE.md 里找不到 `## {SECTION}` —— 那一节是全仓数字的唯一副本，不能改名"

    bad = mismatches(body)
    assert not bad, (
        "ARCHITECTURE.md「当前状态」和代码对不上：\n  " + "\n  ".join(bad) + "\n"
        "改的是文档不是代码——那些数字全是运行时数出来的。"
        "如果是某条模式没认出作者的新写法，改 FACTS 里的正则，别把断言删掉。"
    )


def test_all_checks_wording_stays_honest() -> None:
    """`ALL_CHECKS` 的「有几条」这件事是**措辞**不是数字，所以单独钉。

    文档说「只有一条」当且仅当代码里真只有一条；R2/R3 落地后文档已改成
    「R2/R3/R4 在跑」，M3 那段论证也同步从「会假绿」改成了「代码就绪、等真书」。
    """
    body = section_of(ARCHITECTURE.read_text(encoding="utf-8"), SECTION)
    says_only_one = re.search(r"`ALL_CHECKS`[^\n]{0,8}只有", body) is not None
    assert says_only_one == (len(ALL_CHECKS) == 1), (
        f"文档称 ALL_CHECKS 只有一条 = {says_only_one}，实际 len(ALL_CHECKS) = {len(ALL_CHECKS)}。\n"
        "规则表长出第二条时，「M3 今天不可测」那段论证也得跟着改——它靠的就是这个前提。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 第 2 道：这些数字只许有一份
# ══════════════════════════════════════════════════════════════════════════

EXEMPT_DIRS = ("docs_dev/", "docs/adr/bench/", "frontend/node_modules/", "node_modules/", ".venv/")
"""扫不到的目录，各有各的理由，别顺手往里加。

- `docs_dev/` —— 维护者的私记，按定义是**当天的快照**，不是权威，本来就该会过时。
- `docs/adr/bench/` —— ADR 0001 的实测证据，改写它 = 改写论证（同 ruff 的排除名单）。
- `node_modules/` / `.venv/` —— 别人的文件。
"""

EXEMPT_FILES = ("docs/PLAN.md",)
"""`PLAN.md` 是 92KB 的原始实施计划，写的是**当初打算做什么**，是历史文书。
拿今天的真值去判它，等于要求一份计划书随代码一起改——那它就不是计划书了。
`docs/EVAL_PROTOCOL*` 同理且更硬：它是**预注册**，冻在 `0393088` 那条 commit 上，
按定义**不许**因为后来的代码变动而改（改了就是改卷子）。
"""

HOME = "docs/ARCHITECTURE.md"


def markdown_files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(REPO_ROOT.rglob("*.md")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(EXEMPT_DIRS) or rel in EXEMPT_FILES:
            continue
        if rel.startswith("docs/EVAL_PROTOCOL"):
            continue
        out.append(path)
    return out


def test_the_numbers_live_in_exactly_one_place() -> None:
    """会漂的数字断言只许出现在 ARCHITECTURE.md，别处写指针。

    第 1 道守卫只盯着一份文档。没有这一道，第八份拷贝还是能长出来，而且长出来那天
    它是对的——**拷贝不是在写下的那一刻骗人，是在下一次改代码的时候**。
    """
    offenders: list[str] = []
    for path in markdown_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == HOME:
            continue
        owned = doc_numbers(path.read_text(encoding="utf-8"))
        for fact in FACTS:
            if fact.unique and fact.key in owned:
                offenders.append(f"{rel}：{fact.key} = {owned[fact.key]}")

    assert not offenders, (
        "这些文档抄了一份 ARCHITECTURE.md「当前状态」的数字：\n  " + "\n  ".join(offenders) + "\n"
        f"删掉数字，改成指到 `{HOME}#当前状态` 的指针。理由不是洁癖：改代码的人没有义务"
        "记得去改七个地方，所以第二份拷贝的寿命就是「到下次改代码为止」。\n"
        "真的要引用一个历史值（讲病史），把它包进「」——那读起来就是引用，不是断言。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 守卫自己的守卫
# ══════════════════════════════════════════════════════════════════════════
#
# 一道**永远绿**的数字守卫是最坏的结果：数字照漂，还多一份「有测试盯着」的安全感。
# 所以下面既证明它抓得到错的，也证明它放得过对的（假红会被关掉，关掉的守卫等于没有）。

QUOTED_PROBE = "2026-07-30 的审计发现「27 条路由」躺了七份，而真值早已是 32。"

STALE_ROUTES = 27
"""审计前 CLAUDE.md / README.md 里躺着的那个路由数。"""


def _fresh_probe() -> str:
    """用当下真值现拼一段，形状照抄 ARCHITECTURE.md 那个代码块。

    **probe 里一个数字都不许写死。** 写死就是又造一份会漂的拷贝——
    正是这份守卫要治的病，犯在守卫自己身上尤其难看。
    """
    return (
        f"## {SECTION}\n"
        f"migrations/001_init.sql（{table_count()} 张表）\n"
        f"cli.py ← nh 的 {cli_leaf_count()} 个子命令\n"
        f"api/ ← {route_count()} 条自建路由 + {error_mapping_count()} 个错误映射\n"
        f"（{api_route_count()} 条 /api + 1 条 `GET /`；其中 {stub_501_count()} 条是 501 stub）\n"
        f"api.json ← {fixture_endpoint_count()} 个端点\n"
    )


def _stale_probe() -> str:
    """真值那段里只把路由数改回 27 —— **单个数字漂掉才是真实的故障形态**，
    没有人会一次改错七个。"""
    return _fresh_probe().replace(f"{route_count()} 条自建路由", f"{STALE_ROUTES} 条自建路由")


def test_the_guard_can_see_a_single_stale_number() -> None:
    """**这条是这个文件存在的理由。** 一个数字漂掉，必须红，且必须指名道姓是哪个。"""
    # probe 用的错值要是哪天变成真值，`replace` 就什么都没改，这条会静默变成空转。
    assert route_count() != STALE_ROUTES, "路由数回到 27 了 —— 换一个错值，否则这个 probe 不证明任何事"
    bad = mismatches(_stale_probe())
    assert len(bad) == 1, f"该只红一条（路由数），实际 {bad}"
    assert "自建路由数" in bad[0] and str(STALE_ROUTES) in bad[0]


def test_the_guard_does_not_cry_wolf_on_the_truth() -> None:
    # 反过来也得成立：拿真值拼出来的一段必须全绿，否则上面那条红得没有意义。
    assert mismatches(_fresh_probe()) == []


def test_a_missing_number_is_a_failure_not_a_skip() -> None:
    """文档把数字整段删了 = 守卫红，**不是**悄悄通过。

    这是本仓库最贵的一条教训（`demo.sh` 注释里那句「一张漂亮的空表 + exit 0」）：
    匹配不到时最自然的写法是 `if not hits: continue`，而那正好让「删掉数字」成为
    绕过守卫的最省事办法。
    """
    bad = mismatches(f"## {SECTION}\n这一节今天什么数字都不写了。\n")
    assert len(bad) == len([f for f in FACTS if f.actual is not None])
    assert all("一句都没写" in line for line in bad)


def test_the_scanner_reads_the_real_section() -> None:
    # 小节截取要是把标题拼错（或者 ARCHITECTURE.md 改了标题），上面全部会静默变成空串。
    body = section_of(ARCHITECTURE.read_text(encoding="utf-8"), SECTION)
    lines = body.splitlines()
    assert lines[0] == f"## {SECTION}"
    # `### ` 是本节内部的小标题，不算越界——所以判据是「整行以 `## ` 开头」而不是子串。
    assert not any(ln.startswith("## ") for ln in lines[1:]), "截过头了，吃进了下一个 ## 小节"
    assert doc_numbers(body), "真文档里一个数字都没抓到 —— 模式全瞎了"


def test_quoting_an_old_value_is_not_an_assertion() -> None:
    """「」里的旧值不算断言——否则这份文档没法讲自己的病史（它正引着「27 条路由」）。"""
    assert doc_numbers(QUOTED_PROBE) == {}
    assert doc_numbers(QUOTED_PROBE.replace("「", "").replace("」", "")) == {"自建路由数": [27]}


def test_the_uniqueness_scanner_actually_scans() -> None:
    # 文件清单要是筛空了（比如 rglob 起点找错），第 2 道会永远绿。
    files = {p.relative_to(REPO_ROOT).as_posix() for p in markdown_files()}
    assert HOME in files and "CLAUDE.md" in files and "README.md" in files
    assert not any(f.startswith("docs_dev/") for f in files), "docs_dev/ 是维护者私记，不受这条管"
    assert "docs/PLAN.md" not in files and not any(
        f.startswith("docs/EVAL_PROTOCOL") for f in files
    ), "预注册和历史计划书不许被今天的代码判红"
    assert doc_numbers(ARCHITECTURE.read_text(encoding="utf-8")), "扫描器在权威那份里都抓不到数字"
