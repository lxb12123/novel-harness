"""**轨道永远不进 Writer 的 prompt。** 这道守卫挡的是产品核心主张被从背后拆掉。

作者跳回去改第 12 章，引擎去后面的章里找相关设定（`track.py`）。那些总结里可能
写着**这一章的读者还不该知道的事**：

    第 18 章：「萧决终于知道自己身上养的是玄血蛊。」

把它塞进写第 12 章的 prompt，等于**把伏笔亲手告诉模型**。而这个仓库的一句话定义是
「唯一一个知道『谁在第几章还不该知道什么』的引擎」——那句话在那一刻就成了假的，
**而且是从背后成假的**：没有一条规则会报错，没有一个 Issue 会亮，作者只会觉得
「AI 好像有点太懂了」。它和 CLAUDE.md 头号错误第 5 条（完整 PLANNED 进 Writer prompt）
是同一个失败，只是从另一扇门进来。

**解法不是过滤，是分开：写的和验的不是同一个上下文。**

    Writer 的上下文：前文、下文、**前面**章节的总结     —— 干净的，从没见过轨道
    验证那一侧    ：轨道 + Writer 刚写的那段          —— 它可以看见不该说的东西

── 两道，各挡一种坏法 ────────────────────────────────────────────────────

1. **静态**：拼 prompt 的那几层一行都不许 import 轨道（下面第一组）。
   拦的是「顺手在装配那儿多塞一块」——而那正是会真的发生的那一种，因为轨道就在
   同一次请求里、手边就有。
2. **动态**：跑一次真的续写，把响应里那份轨道的每一段总结拿去在 prompt 里搜
   （下面第二组）。拦的是绕过静态那一网的所有写法：换个名字 import、经第三个模块
   转一手、把总结先拼成字符串再传进来。

**两道都要。** 只有静态的话，一个把总结先塞进 `goal` 的实现照样绿；只有动态的话，
它只覆盖那一条测试路径上的形状。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_draft_boundary import banned_symbols
from test_track import CHAPTERS, EDIT, HERE, SHORT, _book, _summarize

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

PROMPT_DIRS = frozenset({"draft", "calibration"})
"""**谁在拼要进 Writer prompt 的东西。**

`draft/` 是装配层本身；`calibration/` 是「本稿执行计划 / 目标章当前正文」那两块的
渲染器（`product_draft._append_execution_plan` 把它追加进产品 prompt）。

**扫描面搬家就要跟着改这一行**（同 `test_draft_boundary` 那条自述）：又长出第三个
往 prompt 里塞东西的地方而没被收进来，这道守卫在那儿等于不存在。下面第二组
（真链路）不吃这份名单，那是它存在的理由之一。
"""

TRACK_MODULES = ("track", "summary_index", "advisory_review")
"""轨道那三个模块。

`summary_index` 一并禁掉不是连坐：反查函数就在它里面，`chapters_after_mentioning`
一次能把后面 700 章的总结全捞出来。

**`advisory_review` 是 2026-08-23 加的第三个，它守的是这件事的另一半。** 前两个挡的是
「轨道进 Writer 的 prompt」；它挡的是「**轨道的评语回流进 Writer 的 prompt**」——
那个模块拿着轨道判完，出参虽然只有三个数（`TrackClash`：第几句 / 跟第几章 / 冲突类型），
但它一旦被拼进起草那一侧，「后面第 64 章有东西跟你这句话打架」本身就是一句关于未来的话。
**评语要回给谁是产品决定，而它的落点是右栏的通知，不是下一次 prompt。**"""

TRACK_SYMBOLS = frozenset(
    {
        "AdvisoryOutcome",
        "SecretSlip",
        "Track",
        "TrackClash",
        "build_track",
        "chapters_after_mentioning",
        "chapters_mentioning",
        "ensure_index",
        "mentions_in_chapter",
        "mentions_in_text",
        "review_saved_chapter",
    }
)
"""这三个模块的公开面。**`mentions_in_chapter` 也在里面**：它问的是「第 N 章的
总结提到了谁」，把 N 填成后面的章就是同一件事换了个问法。"""


def track_module_imports(source: str, filename: str = "<probe>") -> list[int]:
    """返回把轨道那两个模块拉进来的行号。**判据是模块，不是它导出的名字。**

    三种写法都算，因为三种都拿得到那个模块：`from ..track import x`（模块路径）、
    `from .. import track`（**模块名出现在 names 里，路径是空的**）、
    `import novel_harness.track`。

    和 `banned_symbols` 分工：那一网看的是「用没用那几个名字」，所以经第三个模块
    转一手（`from .helpers import Track`）它抓得住而这一网抓不住；反过来，
    模块拿到手再 `getattr` 出函数，这一网抓得住而那一网抓不住。**两网都要。**
    """
    tree = ast.parse(source, filename=filename)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            hit = any(mod == t or mod.endswith("." + t) for t in TRACK_MODULES)
            # `from .. import track`：模块路径是 `..`，模块名在 names 里。
            hit = hit or any(a.name in TRACK_MODULES for a in node.names)
            if hit:
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(
                a.name == t or a.name.endswith("." + t)
                for a in node.names
                for t in TRACK_MODULES
            ):
                lines.append(node.lineno)
    return sorted(set(lines))


def _scan(check) -> list[str]:
    offenders: list[str] = []
    for d in sorted(PROMPT_DIRS):
        for path in sorted((SRC / d).rglob("*.py")):
            rel = path.relative_to(SRC)
            for lineno in check(path.read_text(encoding="utf-8"), str(rel)):
                offenders.append(f"{rel}:{lineno}")
    return offenders


_WHY = (
    "轨道是**后面那些章**的总结，里面可能写着这一章的读者还不该知道的事"
    "（第 18 章：「萧决终于知道自己身上养的是玄血蛊。」）。\n"
    "把它给写第 12 章的模型看 = 把伏笔亲手告诉它 = 这个产品的核心主张被从背后拆掉，"
    "而且没有任何一条规则会报错。\n"
    "要用它就在**验证那一侧**用（它和 Writer 是两个上下文，见 `track.py` 模块头）。"
)


# ══════════════════════════════════════════════════════════════════════════
# 一、静态：拼 prompt 的那几层碰不到它
# ══════════════════════════════════════════════════════════════════════════


def test_no_prompt_layer_imports_the_track() -> None:
    offenders = _scan(track_module_imports)
    assert not offenders, f"这几处 import 了轨道：{offenders}\n{_WHY}"


def test_no_prompt_layer_names_a_track_symbol() -> None:
    offenders = _scan(lambda s, f: banned_symbols(s, TRACK_SYMBOLS, f))
    assert not offenders, f"这几处引用了轨道那几个符号：{offenders}\n{_WHY}"


AGENT_MAY_BORROW = frozenset({"TrackClash"})
"""`agent/` 唯一许从轨道那三个模块里借的名字。

**为什么它能借这一个**：`TrackClash` 是三个数（第几句 / 跟第几章 / 冲突类型），
手里没有轨道原文。轨道阶段 3 那条工具（`check_track`）要它当出参的形状。

**为什么别的一个都不许**：`Track` / `build_track` / `chapters_after_mentioning`
手里有后面章节的总结，`AdvisoryOutcome` 手里有秘密那一问的结论。
`agent/` 是模式二的会话层——工具结果会**永久留在对话历史里**（ADR 0019 边界六），
一旦这几个里的任何一个能被 import，「工具有权看轨道，agent 没有」就只剩一句自觉。
"""


def test_the_agent_layer_borrows_exactly_one_name_from_the_track_modules() -> None:
    """**工具表就是权限边界**（ADR 0019 边界一）在 import 这一层的落点。

    它红了代表 `agent/` 里有人直接够到了轨道本体：症状是下一次有人「顺手」把
    `build_track` 的结果拼进工具返回，于是第 64 章的总结进了写第 2 章的模型的
    对话历史——**而对话是持久且累积的，它再也出不去了**。
    """
    import ast

    offenders: list[str] = []
    for path in sorted((SRC / "agent").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not any(node.module.endswith(m) for m in TRACK_MODULES):
                continue
            for alias in node.names:
                if alias.name not in AGENT_MAY_BORROW:
                    rel = path.relative_to(SRC)
                    offenders.append(f"{rel}:{node.lineno} {node.module}.{alias.name}")
    assert not offenders, (
        f"`agent/` 从轨道模块里借了白名单以外的名字：{offenders}。"
        "工具可以持有轨道，agent 不行——出参只许是已经判完的那三个数。"
    )


def test_the_scanned_surface_is_the_one_that_matters() -> None:
    """名单里那几个目录都还在。**目录改名而名单没跟上 = 这道守卫静默失效。**"""
    for name in PROMPT_DIRS:
        assert (SRC / name).is_dir(), name
    assert TRACK_MODULES == ("track", "summary_index", "advisory_review")
    for module in TRACK_MODULES:
        assert (SRC / f"{module}.py").is_file(), module


# ── 守卫自己的守卫：证明它真的看得见那几种绕法 ─────────────────────────────

IMPORT_PROBE = '''
from __future__ import annotations
from .. import track

def assemble(conn, store, pid, chapter, text) -> str:
    """拿到模块，函数名拼出来——一个被禁的名字都没写下来。"""
    lookup = getattr(track, "build_" + "track")
    return "\\n".join(row.summary for row in lookup(conn, store, pid, chapter=chapter, text=text).chapters)
'''
"""**模块拿到手，函数名拼出来**：源码里一个被禁的名字都不出现，符号那一网全瞎。"""

SYMBOL_PROBE = '''
from __future__ import annotations
from .helpers import Track

def extra_block(track: Track) -> str:
    """轨道从第三个模块转了一手进来——本文件一次都没 import 过它。"""
    return "\\n".join(row.summary for row in track.chapters)
'''
"""**经第三个模块转一手**（re-export / 类型转发）：模块那一网看不见 `..track`，
符号那一网看得见 `Track` 这个名字。两网互补，缺一个就有一种绕法活着。"""


def test_the_import_scanner_sees_a_module_grabbed_without_naming_anything() -> None:
    assert track_module_imports(IMPORT_PROBE) == [3]


def test_the_symbol_scanner_sees_a_type_forwarded_through_a_third_module() -> None:
    # 第 3 行是那次转发 import，第 5 行是签名上的类型标注——**两处都算**：
    # 一个函数敢把 `Track` 写进签名，它就已经在这一层碰它了。
    assert banned_symbols(SYMBOL_PROBE, TRACK_SYMBOLS) == [3, 5]


def test_the_two_scanners_really_are_complementary() -> None:
    """互补性本身要被钉住：哪天有人把其中一网删了，另一网**看起来**还在守。"""
    assert banned_symbols(IMPORT_PROBE, TRACK_SYMBOLS) == [], "名字是拼出来的，抓不到"
    assert track_module_imports(SYMBOL_PROBE) == [], "本文件根本没 import 那个模块"


def test_the_import_scanner_also_sees_the_two_ordinary_ways() -> None:
    """最平常的两种写法当然也要抓到——上面那个 probe 太刁，别让它掩盖基本盘。"""
    assert track_module_imports("from ..track import build_track") == [1]
    assert track_module_imports("import novel_harness.summary_index") == [1]
    assert track_module_imports("from ..draft.rolling_summary import SummaryStore") == []


# ══════════════════════════════════════════════════════════════════════════
# 二、动态：真跑一次续写，轨道里的每一个字都不在 prompt 里
# ══════════════════════════════════════════════════════════════════════════


SPOILER = "萧决终于知道自己身上养的是玄血蛊。"
"""第 18 章的总结。**第 12 章的读者还不该知道这件事**，模型更不该。"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    book = _book(tmp_path)
    _summarize(book, 15, "玄铁令只能在水底唤醒，白光是假的。")
    _summarize(book, 18, SPOILER)
    monkeypatch.setenv("NH_DB", book["db"])
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from novel_harness.api.app import app

    with TestClient(app) as c:
        assert (
            c.put(
                "/api/settings",
                json={
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash",
                    "api_key": "sk-test",
                    "context_window": 128_000,
                },
            ).status_code
            == 200
        )
        c.pid = book["pid"]  # type: ignore[attr-defined]
        yield c


def _run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> tuple[str, dict]:
    """跑一次改旧章的续写，返回 `(发出去的整份 prompt, 响应里那份轨道)`。"""
    import novel_harness.draft.generate as generate_mod
    from novel_harness.draft.provider import CompletionResult

    observed: list[list[dict[str, str]]] = []

    def fake(messages, *, config=None, plan=None, client=None) -> CompletionResult:
        observed.append(messages)
        return CompletionResult(text="正文" * 75, model="fake", finish_reason="stop")

    monkeypatch.setattr(generate_mod, "complete", fake)
    reply = client.post(
        f"/api/projects/{client.pid}/chapters/{HERE}/draft",  # type: ignore[attr-defined]
        json={
                        "previous_tail": EDIT,
            "following_text": "他没有回头。门在身后合上。",
            "length": SHORT,
        },
    )
    assert reply.status_code == 200, reply.text
    assert len(observed) == 1
    return "\n".join(m["content"] for m in observed[0]), reply.json()["track"]


def test_the_later_chapter_summaries_never_reach_the_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这一条是整件事的地基。**

    判据不是「那个 spoiler 不在里面」这一条硬编码，是**轨道里的每一段总结**都不在
    ——数据驱动，将来轨道多带一格也自动罩住。
    """
    prompt, track = _run(client, monkeypatch)

    assert track["chapters"], "轨道是空的话这条测试什么都没验到"
    for row in track["chapters"]:
        assert row["summary"] not in prompt, (
            f"第 {row['chapter_number']} 章的总结进了写第 {HERE} 章的 prompt。\n{_WHY}"
        )
    assert SPOILER not in prompt
    assert "玄血蛊" not in prompt


def test_the_anchor_names_are_not_a_back_door_either(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一级那份命中清单也不许被顺手渲染成一块「本章相关」。

    它本身没有剧透（名字是角色册上的公开信息），但它**是按后面章节的相关性挑出来的**
    ——把它摆进 prompt 等于告诉模型「后面这几个东西还会回来」，那也是一种泄漏，
    而且长得很无辜。
    """
    prompt, track = _run(client, monkeypatch)

    assert track["anchors"], "一级空了的话这条测试什么都没验到"
    assert "【轨道】" not in prompt
    for row in track["chapters"]:
        assert f"第 {row['chapter_number']} 章" not in prompt


def test_the_track_did_come_back_on_the_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**对照组。** 上面两条只证明「prompt 里没有」——一个压根没算轨道的实现
    （比如那根线又断了）会在它们上面绿得毫无异常。这一条证明东西确实取到了，
    只是走了另一个出口。
    """
    _, track = _run(client, monkeypatch)

    assert track["frontier"] == CHAPTERS
    assert [row["chapter_number"] for row in track["chapters"]] == [15, 18]
    assert any(row["summary"] == SPOILER for row in track["chapters"])
