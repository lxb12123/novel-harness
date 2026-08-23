"""**跨在前后端那条缝上**：续写能带多少上文，只许有一份答案（2026-08-22）。

── 这条测试拦的是哪一种坏法 ────────────────────────────────────────────────

**两层各存一个数，后加的那层悄悄赢，而且不会红。** 这次的实例：

- 后端 `draft/assemble.py::product_tail_limit()` 按模型的真实窗口伸缩，
  200k 的模型算出来是一万六千多字；
- 前端 `frontend/src/continuation.ts` 写着 `TAIL_LIMIT = 1000`，**不问后端要**。

于是 32k 的模型和 1M 的模型送出去的都是 1,000 字，200k 上的利用率 2.0%——
后端整套伸缩设计被一个常量架空了，**而没有任何一处会红**：`tsc` 看不见后端，
pytest 看不见 `.ts`，作者只会觉得「AI 好像没在看我前面写的」。
`product_tail_limit` 的 docstring 早就写着这条：「只许把上文变长，不许变短——
变短了没人会发现」。

同一形状的纪律仓库里已经有一条：`tests/test_serve.py::test_vite_outdir_and_dist_agree`，
它的注释写着「它坏掉时没有任何别的东西会红」。这一条是第二条。

── 三头一起钉 ──────────────────────────────────────────────────────────────

1. **前端源码里不存在续写上限的数字字面量**（下面第一组）；
2. **设置返回里确实带着那个数，且它就是后端公式在当前能力下算出来的值**（第二组）；
3. **换个窗口更大的模型，那个数自己变大**（第三组）——写死一个常量的实现过不了它。
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness.db import connect, migrate
from novel_harness.draft.assemble import GATE_TAIL_CODE_POINTS, product_tail_limit
from novel_harness.draft.provider import ProviderConfig

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend" / "src"

#: **决定「截多少」**的那几个前端源文件。判据是「谁在做那一刀」，不是「谁提到过这个数」——
#: `SettingsDrawer.tsx` 只把它念给作者听，所以不在这儿（它那一栏里的 `32768` / `128000`
#: 是给作者看的例子，收进来只会假红，而假红会让下一个人把守卫关掉）。
#: 这一刀搬到新文件里就往这儿加一行：没被扫到的文件等于没有守卫
#: （同 `DevTerms.guard.test.tsx` 那条「扫描面」的道理）。
SCANNED = (
    FRONTEND / "continuation.ts",
    FRONTEND / "components" / "CodeEditor.tsx",
    FRONTEND / "components" / "CenterEditor.tsx",
)

#: 设置那条返回里报的数**用的输出预留**。这一格回答的是「这个模型的窗口最多值得带
#: 多少上文」，不是「这一次要给输出留多少」——后者由 `POST …/draft` 按这一稿的长度算，
#: 而 `product_tail_limit` 对预留单调不增，那条路只会把这个数往下夹。
#: 改 `api/app.py::_continuation_tail()` 里那个 0 而不改这儿，下面第二组当场红。
SETTINGS_RESERVE = 0

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?<!:)//[^\n]*")
_NUMBER = re.compile(r"\b\d[\d_]*\b")


def _code_only(source: str) -> str:
    """把注释剥掉，只留会真的跑起来的那部分。

    **注释里可以出现 1000**——这条 bug 的病历就写在 `continuation.ts` 的注释里，
    而把病历一起禁掉等于让下一个人不知道为什么不能写死这个数。
    `(?<!:)` 是为了别把 `https://…` 里那两个斜杠当成行注释。
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))


# ══════════════════════════════════════════════════════════════════════════
# 一、前端源码里不许再有那个数
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_frontend_holds_no_continuation_limit_literal(path: Path) -> None:
    """续写这条路上的前端代码里，一个 >= 地板值的整数字面量都不许有。

    判据是**形状**不是词表（同 `src/test/screenGuard.ts` 那条理由）：任何一个够大的
    整数摆在这几个文件里，都只可能是有人又给上文长度写了一个常量。
    地板值 `GATE_TAIL_CODE_POINTS` 从后端 import——门槛本身也不在这儿抄第二份。
    """
    code = _code_only(path.read_text(encoding="utf-8"))
    big = [n for n in _NUMBER.findall(code) if int(n.replace("_", "")) >= GATE_TAIL_CODE_POINTS]
    assert big == [], (
        f"{path.relative_to(REPO)} 里出现了 {big} —— 续写的上文上限只许后端算"
        "（`GET /api/settings` 的 `continuation_tail_limit`）。前端写死一个数会把"
        "`product_tail_limit()` 整套按窗口伸缩的设计架空，而症状是「模型忽然变笨」，"
        "没有任何别的东西会红。"
    )


def test_the_old_frontend_constant_is_gone_for_good() -> None:
    """`TAIL_LIMIT` 这个名字在整个前端**一次都不许再出现**。

    上一条只咬数字；这一条咬名字。两条都要：有人完全可以写
    `export const TAIL_LIMIT = someDefault` 绕过数字那一网。

    同样只看代码，不看注释——那个名字写在 `continuation.ts` 的病历里，
    而删掉病历只会让下一个人不知道它当初为什么错。
    """
    offenders = [
        path.relative_to(REPO).as_posix()
        for path in FRONTEND.rglob("*.ts*")
        if re.search(r"\bTAIL_LIMIT\b", _code_only(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], f"这几个文件里又有 TAIL_LIMIT 了：{offenders}"


def test_the_fixture_carries_the_number_so_vitest_can_eat_it() -> None:
    """那份**从真后端 dump 的** fixture 里带着这一位，前端测试吃的就是它。

    这一条是「别手写前端 fixture」那条纪律在本任务上的落点：字段被后端改名或删掉时，
    `tests/test_frontend_contract.py` 先红，而这一条说清楚为什么它不能没有。
    """
    fixture = json.loads(
        (FRONTEND / "__fixtures__" / "api.json").read_text(encoding="utf-8")
    )
    for key in ("settings", "settingsSaved"):
        assert "continuation_tail_limit" in fixture[key], key
        assert "continuation_tail_basis" in fixture[key], key


# ══════════════════════════════════════════════════════════════════════════
# 二、设置返回里那个数 == 后端公式在当前能力下算出来的值
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db = tmp_path / "book.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    monkeypatch.setenv("NH_DB", str(db))
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    # 环境变量是这条链的兜底档（`_draft_provider_config`）。跑测试的人 shell 里
    # 恰好有一个，出参就跟着他的模型变——这一组的判据全靠出参，必须清干净。
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _configure(client: TestClient, *, route: tuple[str, str], window: int | None) -> dict:
    reply = client.put(
        "/api/settings",
        json={"base_url": route[0], "model": route[1], "context_window": window},
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


def _expected(route: tuple[str, str]) -> int:
    """**这一半独立算一遍**，走的是装配层那个唯一的解析口子。

    它没有 import `api/app.py` 里任何一个私有函数——那样就成了「自己验自己」。
    """
    from novel_harness.api.deps import resolve_route_capabilities

    capability = resolve_route_capabilities(
        ProviderConfig(base_url=route[0], model=route[1], api_key="", temperature=None)
    )
    return product_tail_limit(capability.max_context_tokens, SETTINGS_RESERVE)


def test_settings_reports_exactly_what_the_backend_formula_says(client: TestClient) -> None:
    """配好一个认得出的模型 → 那个数就是 `product_tail_limit()` 在它窗口上的输出。"""
    route = ("https://api.deepseek.com", "deepseek-v4-flash")
    body = _configure(client, route=route, window=128_000)

    assert body["continuation_tail_limit"] == _expected(route)
    assert body["continuation_tail_basis"] == "model_window"
    # 而且它**确实比冻结的地板值大**——不然这条测试在「一切都塌回 800」时也会绿。
    assert body["continuation_tail_limit"] > GATE_TAIL_CODE_POINTS

    # GET 和 PUT 的回执必须是同一个数：前端拿的是 GET 那条，改完设置立刻重取。
    assert client.get("/api/settings").json() == body


def test_get_and_the_author_window_are_the_same_chain(client: TestClient) -> None:
    """作者手填的窗口压得过注册表这件事，在这一格上也成立。

    `deepseek-v4-flash` 注册表里是 1M 的窗口；作者填 32,000 就该按 32,000 算。
    漏掉手填那一档的实现会在这儿红——那正是 `deps.resolve_route_capabilities`
    docstring 点名的坑（少给上文是静默的）。
    """
    route = ("https://api.deepseek.com", "deepseek-v4-flash")
    told = _configure(client, route=route, window=32_000)["continuation_tail_limit"]
    registry = _configure(client, route=route, window=None)["continuation_tail_limit"]
    assert told < registry, (told, registry)


# ══════════════════════════════════════════════════════════════════════════
# 三、换个更大的窗口，那个数自己变大；拿不到窗口时**说出来**
# ══════════════════════════════════════════════════════════════════════════


def test_a_bigger_window_buys_more_prior_text_with_no_frontend_change(
    client: TestClient,
) -> None:
    """验收那一条：换一个窗口更大的模型，续写自动拿到更多上文，前端一个数都不用改。

    写死常量的实现（2026-08-22 之前那一版）在这条上必红：它三次都报同一个数。
    """
    route = ("https://api.deepseek.com", "deepseek-v4-flash")
    seen = [
        _configure(client, route=route, window=window)["continuation_tail_limit"]
        for window in (32_000, 128_000, 200_000)
    ]
    assert seen == sorted(seen) and len(set(seen)) == 3, seen


def test_an_unknown_window_says_so_instead_of_collapsing_quietly(client: TestClient) -> None:
    """自建端点（公共快照永远认不出）：数字塌回地板值，**但出参说得出为什么**。

    这一位是这次唯一的「诚实兜底」：`unknown_window` 和 `unconfigured` 算出来的数
    一模一样，不区分开的话，作者屏幕上「AI 忽然变笨」就是一件查不出原因的事。
    """
    body = _configure(client, route=("http://localhost:11434/v1", "qwen-plus"), window=None)
    assert body["continuation_tail_limit"] == GATE_TAIL_CODE_POINTS
    assert body["continuation_tail_basis"] == "unknown_window"


def test_nothing_configured_at_all_is_its_own_answer(client: TestClient) -> None:
    """连服务地址都没有：同样是地板值，但**不是**「认不出这个模型」那一档。"""
    body = client.get("/api/settings").json()
    assert body["continuation_tail_limit"] == GATE_TAIL_CODE_POINTS
    assert body["continuation_tail_basis"] == "unconfigured"


def test_the_reported_number_is_never_below_what_a_draft_will_use() -> None:
    """报出去的数**只许比 `/draft` 那次真正用的多，不许少**（方向是有意选的）。

    前端多送几百字只是白送几个字符（同一台机器上的 HTTP）；少送就再也补不回来——
    后端拿不到的上文，`assemble()` 变不出来。`product_tail_limit` 对输出预留单调不增，
    所以「预留传 0」得到的就是那个上界。这条把单调性本身钉住：哪天有人改了公式的形状
    （比如加一项和预留正相关的东西），这个方向就不再成立，而它是静默的。
    """
    for window in (32_000, 128_000, 200_000, 1_000_000):
        ceiling = product_tail_limit(window, SETTINGS_RESERVE)
        for reserved in (1_544, 10_000, 40_000, 150_000):
            assert product_tail_limit(window, reserved) <= ceiling, (window, reserved)


def test_the_floor_is_not_a_second_copy_of_eight_hundred() -> None:
    """两档兜底的数字都从公式里取，`api/app.py` 里没有第二个 800。

    这是「同一个数散落两处」的另一种长法：地板值 `GATE_TAIL_CODE_POINTS` 是 X0
    对照臂的定义（EVAL_PROTOCOL §2 冻结），抄一份到壳里，那份就会在改考卷时不跟着变。
    """
    source = (REPO / "src" / "novel_harness" / "api" / "app.py").read_text(encoding="utf-8")
    (func,) = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_continuation_tail"
    ]
    # `ast.unparse` 天生不带注释；docstring 是个真节点，得自己摘掉——
    # 那段文字里就写着「不静默塌回 800」，扫它等于扫病历。
    body = func.body[1:] if ast.get_docstring(func) else func.body
    code = "\n".join(ast.unparse(node) for node in body)
    assert str(GATE_TAIL_CODE_POINTS) not in code, code
