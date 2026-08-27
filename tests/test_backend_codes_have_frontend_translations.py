"""后端能发的每一个通知/错误码，前端 `backendMessages.ts` 都要有翻译（国际化第四批
Phase B）。

── 为什么现在没有一句是"后端拼好的作者话" ──────────────────────────────────

维护者裁定：界面语言独立于书的语言，后端结构上答不出"读这句话的人用什么界面语言"
（多数后台任务没有 HTTP 请求可读）。所以后端只发**码 + 原始参数**，整句在前端
`frontend/src/backendMessages.ts` 按当前界面语言渲染。这条测试钉的是那张表的
**覆盖率**：后端能发的码，前端表里必须有——漏一个，作者看到的就是一串下划线
英文（`saidToTheAuthor` 认不出码时的最后一道兜底）。

── 判据是三个**结构上无歧义**的形状，不是全仓扫 `"error": "..."` ────────────

`api/app.py` 等文件里还有一大批**更早就存在、故意保持"只有码没有话"**的
HTTPException（`chapter_not_found` / `evidence_not_found` / `project_not_found`
那一类——前端在具体那几格有本地兜底，翻译不该把那个既有缺口的责任揽过来，
`docs_dev` 记录过这条裁定）。它们和这一批的新码长得很像（都是
`{"error": "..."}`），**唯一可靠的区分不是文本形状，是它们从不经过这一批
新建的三条通道**：

1. `title_code=<literal>`（`enqueue_notification`/`enqueue_text_advisory`/
   `enqueue_extraction_yielded_nothing` 的关键字参数）；
2. `UnresolvedCast(<literal>, ...)` / `SummaryTextRejected(<literal>, ...)` /
   `ModelWindowsPullFailed(<literal>)`（这一批新建/改造的三个异常类，
   第一个位置参数就是码）；
3. 键集合**恰好是** `{"error"}` 或 `{"error", "params"}` 子集的字典字面量——
   带 `"message"` 或任何别的业务键（`"chapter"`/`"evidence_id"`/`"conflicts"`
   那一类）的，是第 ① 段说的那批旧形状，不在这批范围内，跳过。

用 AST 而不是正则：字典字面量的键值可能跨好几行，正则在这种形状上不可靠；
这个仓库另外两条"从 AST 上钉住"的守卫（`test_the_correction_layer_writes_
no_engine_words` / `test_no_refusal_borrows_someone_elses_sentence`）已经是
先例。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "novel_harness"
BACKEND_MESSAGES_TS = ROOT / "frontend" / "src" / "backendMessages.ts"

_CODE_CARRYING_EXCEPTIONS = frozenset(
    {"UnresolvedCast", "SummaryTextRejected", "ModelWindowsPullFailed"}
)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _codes_in_source(source: str, filename: str) -> set[str]:
    """三种无歧义形状里的字面量码，只扫一份源码文本。"""
    codes: set[str] = set()
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "title_code"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    codes.add(kw.value.value)
            name = _call_name(node)
            if name in _CODE_CARRYING_EXCEPTIONS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    codes.add(first.value)
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if keys and set(keys) <= {"error", "params"} and "error" in keys:
                value = node.values[keys.index("error")]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    codes.add(value.value)
    return codes


def backend_codes() -> set[str]:
    """扫 `src/novel_harness` 下每个 `.py` 文件，收三种无歧义形状里的字面量码。"""
    codes: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        codes |= _codes_in_source(path.read_text(encoding="utf-8"), str(path))
    return codes


def frontend_message_codes() -> set[str]:
    """`backendMessages.ts` 的 `MESSAGES` 对象里，顶层键的完整集合。"""
    text = BACKEND_MESSAGES_TS.read_text(encoding="utf-8")
    start = text.index("const MESSAGES")
    body = text[start:]
    # 顶层键：两个空格缩进 + 标识符 + 冒号 + `{`（对象模板）或 `(`（函数式模板）。
    return set(re.findall(r"^  ([a-z_][a-z0-9_]*): [{(]", body, re.MULTILINE))


def test_every_backend_code_has_a_frontend_translation() -> None:
    missing = backend_codes() - frontend_message_codes()
    assert not missing, (
        f"这些码后端会发，但 `backendMessages.ts` 没有翻译：{sorted(missing)}\n"
        "补进那张表——前端认不出码时会原样显示码本身，等于把下划线英文摆给作者看。"
    )


def test_the_scanner_can_see_all_three_shapes() -> None:
    """守卫的自守卫：三种形状各喂一次，外加两个"旧形状不许被咬"的反例。"""
    probe = '''
def f():
    enqueue_something(title_code="probe_title_code", title_params=None)
    raise UnresolvedCast("probe_unresolved_code", chapter=1)
    raise HTTPException(422, {"error": "probe_bare_code"})
    raise HTTPException(422, {"error": "probe_bare_code_with_params", "params": {}})
    # 旧形状：带 message，不该被收进来。
    raise HTTPException(404, {"error": "probe_legacy_code", "message": "x"})
    # 旧形状：带别的业务键，不该被收进来。
    raise HTTPException(404, {"error": "probe_legacy_code_2", "chapter": 1})
'''
    codes = _codes_in_source(probe, "probe.py")
    assert codes == {
        "probe_title_code",
        "probe_unresolved_code",
        "probe_bare_code",
        "probe_bare_code_with_params",
    }
