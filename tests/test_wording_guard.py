"""对抗性验证：「屏幕上不许出现研发术语」那张网，到底罩住了什么。

2026-08-11 那一轮把守卫从**词表**换成了**形状**（`frontend/src/test/screenGuard.ts`）。
这份文件是对那次改动的复核，它钉住的三件事都是**当天实测出来的洞**，不是假想：

1. **「换成形状」只换了一条。** 裸 id 那一条确实变成了形状，而**大写枚举那一条仍然是
   一张手抄的 30 个词的表**——实测漏掉 13 个真实枚举值（`DiscardOutcome` 七个、
   `LocateOutcome` 五个、`ProposalStatus.ACCEPTED` / `EDITED`，一个都不在表里）。
   本文件的判据是 **Python 那边的枚举本身**：谁往 `StrEnum` 里加一行、
   `screenGuard.ts` 不跟着收，这里就红。**不许在这儿手抄一份成员清单。**

2. **样本缺了一半，所以判据对不对根本没被验过。** 契约夹具里三条抽取运行全是
   `SUCCEEDED`，而失败那一档的副标题走的是另一条代码路径——它当时把

       provider_failure：chapter analysis provider failed

   摆在小说作者的日志页上（snake_case 机器码 + 一整句英文）。更糟的是
   `tests/test_activity.py::seed_run` 种的失败样本用的是一个**编出来的**码
   （`boom`）配一句**中文** message，于是那条路径在两个运行时的守卫下都是绿的。
   这里种的是 `ExtractionErrorCode` 的真值 + `runner.py` 里真写下的那句英文。

3. **同一句话在两个运行时的判据不一样。** `tests/test_canon_edit_boundary.py::DEV_TERMS`
   的 snake_case 那一段是词表（`valid_from|canon_version|…` 十来个词），
   浏览器那侧是形状——所以 `provider_failure` 在浏览器侧会红、在 Python 侧不会。
   本文件用的是**形状**，扫的是同一批出参。

**判据只拿去扫「真的会上屏的字符串」**：拿它扫源码会对着类型定义和注释开火，
而那种守卫会被人关掉。
"""

from __future__ import annotations

import ast
import enum
import importlib
import inspect
import json
import pkgutil
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_activity import seed_call, seed_run

from novel_harness import activity
from novel_harness.db import connect
from novel_harness.extract.control import ExtractionErrorCode, ExtractionRunStatus
from novel_harness.graph import EdgeType, NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.ids import EntityType, new_id

REPO = Path(__file__).resolve().parents[1]
SCREEN_GUARD = REPO / "frontend" / "src" / "test" / "screenGuard.ts"
API_TYPES = REPO / "frontend" / "src" / "api" / "types.ts"
RUNNER = REPO / "src" / "novel_harness" / "extract" / "runner.py"


# ══════════════════════════════════════════════════════════════════════════
# 判据 —— 形状，四张网，和 `screenGuard.ts` 一一对应
# ══════════════════════════════════════════════════════════════════════════

MACHINE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
"""snake_case：错误码、库字段名、请求体键名。"""

SCREAMING = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
"""SCREAMING_SNAKE：`LOCATED_AT` / `NOT_FOUND` / `SUPERSEDED_IN_ANALYSIS`。

**这一条是形状不是词表**，所以明天新长出来的枚举值也一起收。"""

RAW_ID = re.compile(r"\b[A-Za-z][A-Za-z0-9]*:[A-Za-z0-9]+")
"""`前缀:标识` 形状的内部主键，**包括被截断到认不出前缀的**（`n:ID22`）。"""

LATIN_SENTENCE = re.compile(r"[A-Za-z]+(?:[ ,.'-][A-Za-z]+){3,}")
"""连着四个以上英文单词 —— 一句英文。

中文界面上合法的英文是**名词**（`token` / `ms` / `deepseek-v4` / `API`），
不是句子。`chapter analysis provider failed` 落在这一条上。"""


def _engine_enum_alternatives() -> frozenset[str]:
    """`screenGuard.ts::ENGINE_ENUM` 里**列出来的那些词**，从源文件里读出来。

    **故意不在 Python 这边再抄一份**：两份手抄的表互相验证，正是这条缝原本的病。

    只取 `(?: … | … )` 分组里的完整单词——直接扫整条正则会把字符类里的 `A` / `Z`
    也当成词（那一版实测把 ISO 时间戳末尾的 `Z` 判成了引擎枚举，是一次假红）。
    """
    source = SCREEN_GUARD.read_text(encoding="utf-8")
    match = re.search(r"export const ENGINE_ENUM\s*=\s*\n?\s*/(.+?)/g;", source, re.S)
    assert match, "screenGuard.ts 里找不到 ENGINE_ENUM —— 判据换了名字，这份守卫要跟着改"
    words: set[str] = set()
    for group in re.findall(r"\(\?:([^)]*)\)", match.group(1)):
        words |= {
            token
            for token in group.split("|")
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,}", token)
        }
    assert len(words) >= 10, f"从 screenGuard.ts 里只读出 {len(words)} 个词，解析多半坏了"
    return frozenset(words)


def _bare_upper_words() -> frozenset[str]:
    return frozenset(w for w in _engine_enum_alternatives() if w.isupper())


def _camel_words() -> frozenset[str]:
    return frozenset(w for w in _engine_enum_alternatives() if not w.isupper())


def caught_by_the_browser_net(text: str) -> bool:
    """`screenGuard.ts` 的四张网合起来，会不会咬这段字。

    第四张（整句英文）2026-08-13 补上，**它不改变这里任何一个已有结论**：本函数只被
    拿去问单个枚举值，而那一张要三个连着的小写英文单词才咬。
    """
    if (
        MACHINE.search(text)
        or SCREAMING.search(text)
        or RAW_ID.search(text)
        or LATIN_SENTENCE.search(text)
    ):
        return True
    words = set(re.findall(r"[A-Za-z][A-Za-z0-9]*", text))
    return bool(words & (_bare_upper_words() | _camel_words()))


def dev_shapes(text: str) -> list[str]:
    """这段字里所有「长得像标识符的英文」+ 整句英文。"""
    found: list[str] = []
    for pattern in (MACHINE, SCREAMING, RAW_ID, LATIN_SENTENCE):
        found.extend(m.group(0) for m in pattern.finditer(text))
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", text):
        if word in _bare_upper_words() or word in _camel_words():
            found.append(word)
    return sorted(set(found))


# ══════════════════════════════════════════════════════════════════════════
# 判据的自守卫
# ══════════════════════════════════════════════════════════════════════════

CLEAN_SCREEN = "\n".join(
    [
        "萧决 对「血脉秘密」：知道 → 以为 · 第 3 章起",
        "模型调用 · 抽取 · deepseek-v4 · 入 1200 / 出 400 token · 900 ms",
        "AI 设置 · 服务地址 · 模型名称 · API 密钥",
        "2026/08/11 01:28:40",
        "已整理 3 次 · 花费 未记录 · 有效事件 3 条",
        "这本书导入的是 TXT，打开方式见 README 那一行",
    ]
)
"""**干净的屏幕**：一个字都不许被咬。假红比漏报更危险——它会让下一个人把守卫关掉，
而不是把界面修好。里面故意留了作者界面上真的有的英文（`token` / `ms` / 型号 /
`API` / `TXT`），它们是名词不是标识符。"""


def test_the_shape_judge_sees_every_kind_of_leak_that_really_happened() -> None:
    """**守卫的自守卫。** 每一条都是真的上过这个产品的屏幕（或者当场就会）。"""
    probes = {
        "provider_failure：chapter analysis provider failed": "失败的抽取，副标题原样印错误码 + 英文",
        "边 edge:01J8XK 已撤回": "裸 id",
        "当前：萧决 在 n:ID22": "被截断到认不出前缀的 id",
        "这本书在别处刚被改过 stale_base_version": "只有码没有话的 409",
        "萧决 对「血脉秘密」：KNOWS → BELIEVES": "边类型原样上屏",
        "这一条被丢掉了：SUPERSEDED_IN_ANALYSIS": "词表里一个都没有的丢弃原因",
        "这个称呼没找到：NOT_FOUND": "同上，`LocateOutcome`",
        "别名「决」不能 usable_for_rules（ADR 0004）": "写给维护者的诊断",
    }
    missed = {text: why for text, why in probes.items() if not dev_shapes(text)}
    assert not missed, f"判据看不见真的上过屏的违规：{missed}"

    assert dev_shapes(CLEAN_SCREEN) == [], (
        "干净的界面被咬了 —— 假红会让下一个人把守卫关掉，而不是把界面修好"
    )


def test_the_word_list_is_read_from_the_browser_guard_not_retyped_here() -> None:
    """两个运行时共用**一份**清单：这边是读出来的，不是抄出来的。"""
    upper = _bare_upper_words()
    assert {"CANON", "PROVISIONAL", "KNOWS", "BELIEVES"} <= upper
    assert "Character" in _camel_words()
    # 自守卫：解析真的在读那一行，而不是碰巧命中了别处的大写字母。
    assert "SUPERSEDED" not in upper, "带下划线的值归形状那一条管，不该也躺在词表里"


# ══════════════════════════════════════════════════════════════════════════
# 1. 那张网 vs 引擎自己的枚举 —— **判据是枚举，不是手抄的清单**
# ══════════════════════════════════════════════════════════════════════════


def _engine_enums() -> dict[str, list[str]]:
    """`novel_harness` 里每一个枚举（不含 `agent` / `draft` / `eval`）→ 它的值。

    **遍历包本身**，不是列一张模块名单：加一个模块、加一个枚举，这里自动收进来。
    """
    import novel_harness

    found: dict[str, list[str]] = {}
    skip = {"agent", "draft", "eval", "webui"}
    for info in pkgutil.walk_packages(novel_harness.__path__, "novel_harness."):
        if any(part in skip for part in info.name.split(".")):
            continue
        try:
            module = importlib.import_module(info.name)
        except Exception:  # pragma: no cover - 装不上的可选依赖不该拖垮措辞守卫
            continue
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, enum.Enum)
                and obj.__module__.startswith("novel_harness")
            ):
                found[f"{obj.__module__}.{obj.__qualname__}"] = [str(m.value) for m in obj]
    assert len(found) >= 15, f"只找到 {len(found)} 个枚举 —— 遍历坏了，下面那条是永远绿的"
    return found


def test_the_browser_net_catches_every_uppercase_engine_enum_value() -> None:
    """**这条是本文件的主张**：引擎里每一个大写枚举值，那张网都得咬得住。

    2026-08-11 实测漏掉的（当时全部逃逸）：

    - `DiscardOutcome` 七个（`NOT_FOUND` / `AMBIGUOUS` / `BELOW_THRESHOLD` /
      `UNKNOWN_SURFACE` / `AMBIGUOUS_SURFACE` / `WRONG_LABEL` / `SUPERSEDED_IN_ANALYSIS`）
    - `LocateOutcome` 五个（`EXACT` / `FUZZY` / …）
    - `ProposalStatus.ACCEPTED` / `.EDITED`、`EvidenceStatus.NONE`

    判据是**枚举本身**，所以补上之后这条不会再过期：往任何一个 `StrEnum` 里加一行
    大写值而不动 `screenGuard.ts`，这里当场红。
    """
    escaped: dict[str, list[str]] = {}
    for name, values in _engine_enums().items():
        missed = [
            v
            for v in values
            if re.fullmatch(r"[A-Z][A-Za-z0-9_]*", v) and not caught_by_the_browser_net(v)
        ]
        if missed:
            escaped[name] = missed
    assert not escaped, (
        f"这些引擎枚举值一旦上屏，浏览器那侧的守卫看不见：{escaped}\n"
        "修法：带下划线的归 `screenGuard.ts::ENGINE_ENUM` 第一段（形状，自动收）；\n"
        "没有下划线的裸大写值只能列进第二段——**列完这条就不会再过期**。"
    )


def test_the_lowercase_half_is_a_known_blind_spot_and_says_so() -> None:
    """**诚实地钉住这张网看不见的那一半。**

    HTTP 层有意把一批枚举小写化（`ActivityStatus = "failed"`、`actor = "author"`），
    而一个小写英文单词和界面上合法的英文（`token` / `ms` / 型号）形状上分不开——
    收它就假红。所以这一类只能在**源头**堵，判据文件里必须写着这件事，
    否则下一个人会以为「网是全的」。
    """
    assert not caught_by_the_browser_net("author"), "这条断言在描述现状，不是在批准它"
    assert not caught_by_the_browser_net("failed")
    guard = SCREEN_GUARD.read_text(encoding="utf-8")
    assert "小写" in guard and "假红" in guard, (
        "`screenGuard.ts` 必须自己写着「小写裸枚举值它看不见、以及为什么不收」——\n"
        "一张不说自己有洞的网，会被当成全的。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. 抽取失败那一档 —— 夹具里从来没有过的那半块屏幕
# ══════════════════════════════════════════════════════════════════════════


def _runner_error_literals() -> dict[str, str]:
    """`runner.py` 里每一处 `ExtractionRunError(...)` 的 `(code, message)`。

    判据是 AST 不是 grep：那个类名在注释里也有。
    """
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"), filename="runner.py")
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name != "ExtractionRunError":
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        code, message = kwargs.get("code"), kwargs.get("message")
        if isinstance(code, ast.Attribute) and isinstance(message, ast.Constant):
            out[str(getattr(ExtractionErrorCode, code.attr).value)] = str(message.value)
    assert out, "runner.py 里一处 `ExtractionRunError(code=枚举, message=字面量)` 都找不到"
    return out


def test_every_way_an_extraction_can_fail_is_a_closed_enum() -> None:
    """`runner.py` 写下的每一个码都是 `ExtractionErrorCode` 的成员，反过来也一样。

    **它必须是封闭枚举**：读端要把它翻成中文，而开放字符串没法被枚举驱动的守卫罩住。
    """
    written = set(_runner_error_literals())
    declared = {e.value for e in ExtractionErrorCode}
    assert written == declared, (
        f"抽取失败的码对不上：runner.py 写了 {sorted(written)}，枚举声明了 {sorted(declared)}"
    )


def test_the_failure_wording_table_covers_every_code_and_falls_back_to_chinese() -> None:
    """**枚举驱动**：`_RUN_ERROR_LABEL` 一行都不许漏，认不出的也得说人话。"""
    missing = [e.value for e in ExtractionErrorCode if e.value not in activity._RUN_ERROR_LABEL]
    assert not missing, (
        f"抽取失败的措辞表漏了行：{missing}\n"
        "漏掉的那一行会以 `provider_failure` 的形态出现在小说作者的日志页上。"
    )
    assert len(ExtractionErrorCode) >= 3, "枚举遍历为空 —— 上面那条是永远绿的"

    dirty = {
        code: dev_shapes(text)
        for code, text in activity._RUN_ERROR_LABEL.items()
        if dev_shapes(text) or not re.search(r"[一-鿿]", text)
    }
    assert not dirty, f"抽取失败的说法里有研发术语：{dirty}"

    fallback = activity.run_error_label("some_brand_new_code")
    assert not dev_shapes(fallback) and re.search(r"[一-鿿]", fallback), (
        f"认不出的码退到了 {fallback!r} —— 封闭枚举认不出只可能是表漏了行，"
        "而漏的那一行不该由小说作者来读"
    )


def _seed_failed_run(book: dict[str, str], code: ExtractionErrorCode) -> str:
    """一条**真形态**的失败抽取：真的码 + `runner.py` 真写下的那句英文诊断。

    `tests/test_activity.py::seed_run` 种的是 `{"code":"boom","message":"模型没答话"}`
    ——一个编出来的码配一句中文。那个样本在两个运行时的守卫下都是绿的，
    **而它正是这条路径从来没被验过的原因**。
    """
    conn = connect(book["db"])
    try:
        pid = book["pid"]
        snapshot_id = SqliteStoryGraph(conn).chapter_snapshots(pid, 1)[0].snapshot_id
        run_id = new_id(EntityType.EXTRACTION_RUN, pid)
        conn.execute(
            """
            INSERT INTO extraction_run (
                id, project_id, chapter_number, snapshot_id, status, errors_json,
                valid_event_count, discarded_event_count, proposal_count,
                schema_version, prompt_hash, started_at, finished_at
            ) VALUES (?, ?, 1, ?, 'FAILED', ?, 0, 0, 0, 'm4.analysis.v1', 'ph',
                      '2026-08-10T00:00:00.100Z', '2026-08-10T00:00:01.100Z')
            """,
            (
                run_id,
                pid,
                snapshot_id,
                json.dumps(
                    [{"code": code.value, "message": _runner_error_literals()[code.value]}],
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


@pytest.mark.parametrize("code", list(ExtractionErrorCode))
def test_a_failed_extraction_never_hands_the_author_a_machine_code(
    client: TestClient, book: dict[str, str], code: ExtractionErrorCode
) -> None:
    """**红过的那一条。**

    修之前，每一个码都会让日志页长成 `provider_failure：chapter analysis provider
    failed`。折叠行和展开层各扫一遍——展开层那份 `errors` 走的是同一个读端。

    参数化是**枚举驱动**的：新加一种失败方式，这条自动多跑一遍。
    """
    run_id = _seed_failed_run(book, code)
    base = f"/api/projects/{book['pid']}"

    page = client.get(f"{base}/activity", params={"limit": 50})
    assert page.status_code == 200, page.text
    row = next(e for e in page.json()["entries"] if e["id"] == run_id)
    screen = {
        "title": row["title"],
        "subtitle": row["subtitle"],
        "jump": (row["jump"] or {}).get("label", ""),
    }

    detail = client.get(f"{base}/activity/{run_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    for i, line in enumerate(body["errors"]):
        screen[f"errors[{i}]"] = line
    for r in body["rows"]:
        screen[f"rows/{r['label']}"] = f"{r['label']}：{r['value']}"

    offenders = {where: dev_shapes(text) for where, text in screen.items() if dev_shapes(text)}
    assert not offenders, (
        f"一次失败的抽取把机器码摆到了作者脸上（{code.value}）：{offenders}\n"
        "措辞的唯一出处是后端（`activity._RUN_ERROR_LABEL`）。"
    )
    assert body["errors"], "失败了却一条原因都不说 —— 过度收窄一样是 bug"
    assert all(re.search(r"[一-鿿]", line) for line in body["errors"])


def test_the_maintainers_english_diagnosis_never_reaches_the_screen(
    client: TestClient, book: dict[str, str]
) -> None:
    """`ExtractionRunError.message` 是写给**维护者**的，库就在他手上。

    读端只翻 `code`，那句英文一个字都不许跟着出来。

    **两条读端一起扫**（2026-08-13 补的那一条）：日志页读 `/activity`，审阅面板读
    `/extractions/{run_id}`——它们读的是同一批 `extraction_run` 行，而当时只有前者
    翻对了。第二条把整个 `ExtractionRunError` 原样发出去，浏览器渲染的就是那句英文。
    """
    code = ExtractionErrorCode.PROVIDER_FAILURE
    run_id = _seed_failed_run(book, code)
    base = f"/api/projects/{book['pid']}"
    english = _runner_error_literals()[code.value]

    page = client.get(f"{base}/activity", params={"limit": 50}).text
    detail = client.get(f"{base}/activity/{run_id}").text
    run = client.get(f"{base}/extractions/{run_id}").text
    for where, payload in (("列表", page), ("详情", detail), ("审阅面板", run)):
        assert english not in payload, f"{where}把写给维护者的英文诊断交出去了：{english!r}"


@pytest.mark.parametrize("code", list(ExtractionErrorCode))
def test_the_review_panels_run_endpoint_speaks_the_authors_language(
    client: TestClient, book: dict[str, str], code: ExtractionErrorCode
) -> None:
    """**审阅面板轮询的那条端点**（`GET …/extractions/{run_id}`）也只说中文。

    它是这个 bug 的第二个现场：日志页那条 2026-08-11 就翻对了，而这一条把
    `{"code": "provider_failure", "message": "chapter analysis provider failed"}`
    原样发给浏览器，`ProposalReviewTab` 渲染的正是那个 `message`。
    **夹具里从来没有过一次失败的抽取**，所以两个运行时的守卫扫的都是一块永远干净的屏幕。

    参数化是**枚举驱动**的：新加一种失败方式，这条自动多跑一遍。
    """
    run_id = _seed_failed_run(book, code)

    body = client.get(f"/api/projects/{book['pid']}/extractions/{run_id}")

    assert body.status_code == 200, body.text
    errors = body.json()["errors"]
    assert errors, "失败了却一条原因都不说 —— 过度收窄一样是 bug"
    assert all(isinstance(line, str) for line in errors), (
        "出参还是 `{code, message}` —— 可翻译的那个值够得着，就总有人会去渲染另一个"
    )
    offenders = {line: dev_shapes(line) for line in errors if dev_shapes(line)}
    assert not offenders, f"审阅面板上摆着研发术语（{code.value}）：{offenders}"
    assert all(re.search(r"[一-鿿]", line) for line in errors)


def test_an_unknown_run_status_is_never_echoed_back(book: dict[str, str]) -> None:
    """`_RUN_STATUS` 认不出的状态**不原样回吐** —— 那一句原来是 `f"状态：{raw_status}"`。

    **这一条今天走不到，且那正是它值得钉住的理由**：`extraction_run.status` 上有一条
    `CHECK status IN (…)`，所以库里塞不进第五种状态（实测 `UPDATE … 'ABORTED'` 直接
    被约束拦下）。于是这条分支是「只有改了 schema 才会亮」的那一类——**没有守卫的
    死代码，改 schema 的那个人不会想起它**。这里从函数那一层量。
    """
    missing = [s.value for s in ExtractionRunStatus if s.value not in activity._RUN_STATUS]
    assert not missing, f"`_RUN_STATUS` 漏了状态：{missing}"
    assert len(ExtractionRunStatus) >= 3, "枚举遍历为空 —— 上面那条是永远绿的"

    conn = connect(book["db"])
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO extraction_run (id, project_id, chapter_number, snapshot_id,"
                " status, schema_version, prompt_hash) VALUES ('x', ?, 1, 'y', 'ABORTED',"
                " 'm4.analysis.v1', 'ph')",
                (book["pid"],),
            )
    finally:
        conn.close()

    # 库里塞不进第五种状态，所以直接喂给读端那一层：**量行为，不量源码**
    #（量源码的话，一句解释这件事的注释就能让守卫变绿）。
    row = {
        "id": "extraction_run:x",
        "project_id": book["pid"],
        "chapter_number": 1,
        "snapshot_id": "snapshot:x",
        "status": "ABORTED",
        "errors_json": "[]",
        "valid_event_count": 0,
        "discarded_event_count": 0,
        "proposal_count": 0,
        "model_call_id": None,
        "created_at": "2026-08-10T00:00:00.100Z",
        "started_at": None,
        "finished_at": None,
    }
    entry = activity._run_entry(row, {})
    assert not dev_shapes(entry.subtitle), (
        f"认不出的状态被原样摆上屏：{entry.subtitle!r} —— "
        "封闭枚举认不出只可能是 `_RUN_STATUS` 漏了行，而漏的那一行不该由作者来读"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. 浏览器那侧的「码 → 中文」表 —— 也拿枚举驱动
# ══════════════════════════════════════════════════════════════════════════


def _without_comments(source: str) -> str:
    """去掉 `//` 和 `/* */`。

    **必须去**：这几条守卫扫的是「代码里写了什么」，而解释这条守卫的注释本身就带着
    它要拦的那个模式（「兜底写的是 `?? e.type`」）——不去掉，一句解释就能让守卫变绿，
    或者反过来让它对着文档开火。
    """
    no_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", no_block, flags=re.M)


def _ts_object_keys(source: str, name: str) -> list[str]:
    """`export const NAME: … = { A: "…", B: "…" };` 里的键。"""
    match = re.search(rf"export const {name}\s*:[^=]*=\s*\{{(.*?)\n\}};", source, re.S)
    assert match, f"types.ts 里找不到 {name}"
    return re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", match.group(1), re.M)


def _ts_union_members(source: str, name: str) -> list[str]:
    match = re.search(rf"export type {name} =\s*(.*?);", source, re.S)
    assert match, f"types.ts 里找不到 {name}"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_the_browser_side_edge_wording_lists_all_nine_kinds() -> None:
    """**枚举驱动**：`EdgeType` 有几个成员，浏览器那张表就得有几行。

    2026-08-11 之前这张表有**三份拷贝**（`BottomBar` / `EvidenceTab` / `LocalGraph`），
    每一份都只有 7 行，而前两份的兜底是 `?? e.type`——`PLANTED_IN` / `RESOLVED_IN`
    一旦被写出来，屏幕上就是四个大写字母。今天只有一份，且
    `Record<EdgeType, string>` 让「漏一行」变成编译错误。
    """
    source = API_TYPES.read_text(encoding="utf-8")
    declared = [e.value for e in EdgeType]
    assert sorted(_ts_union_members(source, "EdgeType")) == sorted(declared), (
        "浏览器那份 `EdgeType` 联合类型和 Python 的枚举对不上"
    )
    assert sorted(_ts_object_keys(source, "EDGE_ZH")) == sorted(declared), (
        "`EDGE_ZH` 漏了行 —— 漏掉的那一类关系会以引擎枚举的原样出现在作者屏幕上"
    )
    assert sorted(_ts_object_keys(source, "LABEL_ZH")) == sorted(n.value for n in NodeLabel)
    assert len(declared) >= 5, "枚举遍历为空 —— 上面几条是永远绿的"


def test_the_browser_knows_every_way_a_turn_can_stop_and_every_step_it_takes() -> None:
    """**枚举驱动**：写作助手那两个封闭枚举，浏览器那两份联合类型必须**逐个**对得上。

    这一条 2026-08-12 之前不存在，而代价当场兑现了：`StopReason` 加了第十一种
    （`ASKED_AUTHOR`，ADR 0024）之后，`ChatStopReason` 那份联合类型少了一个成员，
    **全仓只有那一处提到它、没有任何东西钉着**——于是「它停下来问了你一句」这一档
    在浏览器的类型系统里根本不存在：后端给了，界面收不到，而 `tsc` 一声不吭
    （多出来的字符串字面量只是不匹配任何一支，不是错误）。

    事件那一份同理，而且更脏：它是这一刀新开的输出面，每一种事件都对着作者的屏幕。

    **这两份不是「界面文案」**，它们是机器码（一个字都不上屏，形状网会咬住它们）。
    钉的是「界面认不认得出这一档」，不是「界面怎么说这一档」——后者的唯一出处
    在后端（`stop_wording()` / `TurnEvent.said_to_author`）。
    """
    from novel_harness.agent.loop import StopReason, TurnEventKind

    source = API_TYPES.read_text(encoding="utf-8")
    stops = [reason.value for reason in StopReason]
    kinds = [kind.value for kind in TurnEventKind]
    assert sorted(_ts_union_members(source, "ChatStopReason")) == sorted(stops), (
        "浏览器那份 `ChatStopReason` 和 Python 的 `StopReason` 对不上 —— "
        "漏掉的那一档界面在类型上根本认不出来"
    )
    assert sorted(_ts_union_members(source, "ChatTurnEventKind")) == sorted(kinds), (
        "浏览器那份 `ChatTurnEventKind` 和 Python 的 `TurnEventKind` 对不上"
    )
    # 枚举遍历为空的话上面两条永远绿 —— 同 `EdgeType` 那条的自守卫。
    assert len(stops) >= 10 and len(kinds) >= 8


def test_the_browser_keeps_exactly_one_copy_of_the_edge_wording() -> None:
    """一张表一份。**第二份拷贝就是第二份措辞源**，而它们不会一起被想起来。"""
    components = REPO / "frontend" / "src" / "components"
    offenders: dict[str, str] = {}
    for path in sorted(components.glob("*.tsx")):
        if path.name.endswith(".test.tsx"):
            continue
        text = _without_comments(path.read_text(encoding="utf-8"))
        # 判据：同一段里同时出现三个以上边类型键，才算一张「关系 → 中文」的表。
        # 两个的那种（`KnowledgeMatrix::STATE_ZH` 是 `Record<KnowledgeEdgeType, …>`）
        # 由 TS 类型强制全列，不是拷贝。
        for block in re.findall(r"=\s*\{(.*?)\n\};", text, re.S):
            keys = set(re.findall(r"^\s*([A-Z][A-Z_]+)\s*:", block, re.M))
            if len({e.value for e in EdgeType} & keys) >= 3:
                offenders[path.name] = sorted(keys)[0]
    assert not offenders, (
        f"这些组件里又长出了一张 edge_type → 中文 的表：{offenders}\n"
        "唯一那一份在 `frontend/src/api/types.ts::EDGE_ZH`（类型上强制 9 类全列）。"
    )


def test_no_component_falls_back_to_a_raw_identifier() -> None:
    """`?? id` / `?? e.type` —— 兜底不许把内部标识摆给作者。

    这正是 `ProposalReviewTab` 上刚修掉的那条（`rosterMap.get(id) ?? id.slice(-6)`
    印出了 `n:ID22`），而当天它在 `BottomBar` 和 `EvidenceTab` 里各还活着一份。
    """
    components = REPO / "frontend" / "src" / "components"
    bad = re.compile(r"\?\?\s*(?:id|e\.type|edge\.type|n\.label)\b")
    offenders: dict[str, list[str]] = {}
    for path in sorted(components.glob("*.tsx")):
        if path.name.endswith(".test.tsx"):
            continue
        hits = bad.findall(_without_comments(path.read_text(encoding="utf-8")))
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        f"这些组件的兜底会把引擎的标识原样摆给作者：{offenders}\n"
        "认不出就说「—」（同 `ProposalReviewTab`）：截短一个认不出的东西不会让它变成人话。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. 扫描面 —— 这份守卫自己覆盖到哪儿
# ══════════════════════════════════════════════════════════════════════════


def test_the_activity_screen_strings_are_all_scanned_by_shape(
    client: TestClient, book: dict[str, str]
) -> None:
    """整页扫一遍（成功 / 失败 / 调用 / 确认四种行都在），判据是形状不是词表。

    `tests/test_canon_edit_boundary.py` 的同名断言用的是词表（snake_case 那一段只有
    十来个词），所以 `provider_failure` 在那边是绿的。**两条一起才罩得住这一页。**
    """
    call_id = seed_call(book)
    seed_run(book, 1, call_id=call_id)
    _seed_failed_run(book, ExtractionErrorCode.ANALYSIS_FORMAT)

    base = f"/api/projects/{book['pid']}"
    page = client.get(f"{base}/activity", params={"limit": 50})
    assert page.status_code == 200, page.text
    entries = page.json()["entries"]
    assert len(entries) >= 2, "样本不够 —— 这条会变成扫一块空屏幕"

    offenders: dict[str, list[str]] = {}
    for entry in entries:
        texts: dict[str, str] = {
            f"{entry['id']}.title": entry["title"],
            f"{entry['id']}.subtitle": entry["subtitle"],
        }
        if entry.get("jump"):
            texts[f"{entry['id']}.jump"] = entry["jump"]["label"]
        detail = client.get(f"{base}/activity/{entry['id']}")
        assert detail.status_code == 200, detail.text
        body: dict[str, Any] = detail.json()
        for r in body.get("rows", ()):
            texts[f"{entry['id']}/{r['label']}"] = f"{r['label']}：{r['value']}"
        for i, line in enumerate(body.get("errors", ())):
            texts[f"{entry['id']}/错误{i}"] = line
        for where, text in texts.items():
            if found := dev_shapes(text):
                offenders[where] = found
    assert not offenders, f"活动记录把引擎的词摆到了作者脸上：{offenders}"
