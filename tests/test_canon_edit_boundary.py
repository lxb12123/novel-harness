"""**对抗性验证**：「改一条已生效事实」的编辑入口（`/canon/events/{id}/cast`
和它在浏览器里的那个控件）有没有踩到四条线。

⚠️ 2026-08-25 之前这里有**两个**入口，另一个是 `/canon/knowledge`（改一格认知 / 在空格
上补一条）。它随秘密下线一起没了（ADR 0039），所以这份守卫今天只剩一个被测对象。
**四条线一条没减**——它们是既有约束在这条路径上的投影，不是那个入口独有的。

这四条线不是本次新加的规矩，它们是既有约束在**一条新路径**上的投影——而新路径正是
约束最容易漏掉的地方（`test_no_chapter_input.py` 只扫 Python 的命令面和 schema，
`test_frontend_product_language.py` 只扫前端**源码里写死的字**，两道守卫加起来
恰好把「后端算出来的一句话被前端原样摆到屏幕上」这条缝留在了外面）。

1. **章号**（约束 10 / ADR 0006）：表单里不许有让作者填章号的地方，请求体里也不许有
   章号作为**声明坐标**。`AS OF 第几章`的查询参数是合法的（`?scope=`、`/chapters/{n}/`），
   声明用的章号不是。
2. **文案**：屏幕上不许出现 `KNOWS` / `BELIEVES` / `valid_from` / `canon_version` /
   `PROVISIONAL` / `edge_id` 这类研发术语。**措辞的唯一出处是后端**——前端加一张映射表
   等于造出第二份措辞源，所以这里量的是**后端吐出来的那句话本身**。
3. **节点属性**：作者写在节点 `props` 上的东西（`twist` / `plot_note`）一个字都不许上屏；
   **反向也要成立**——显示名不在，作者就不知道自己在改哪一条，同样是 bug。
4. **`actor`**：前端不许说「谁改的」。它一旦能说，ADR 0020 想区分的
   「系统改的 vs 作者改的」就由前端说了算，而那正是那份日志唯一的价值。

── 为什么是一个新文件 ────────────────────────────────────────────────────

同 `test_no_chapter_input.py` 的理由：判据不同的守卫不该合住。这里的判据是
「**这条编辑路径**上，作者的输入能到哪儿、后端的字能到哪儿」，横跨前端源码扫描和真
HTTP 出参两种量法；塞进任何一份既有守卫都会把那份守卫变成杂物间，而杂物间会被关掉。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_harness import activity, decisions
from novel_harness.api.review import EventCastEditRequest

from test_activity import seed_call, seed_run
from test_api import _seed_provisional_event

from test_no_chapter_input import BANNED, model_chapter_fields

# `_without_comments` 是一份写对了很难、写错了很安静的东西（它要在剥注释的同时
# 保住字符串字面量和 JSX 文本）。**import 而不是复制**：两份剥注释的实现迟早只有一份
# 是对的，而错的那一份会让守卫安静地漏掉半个文件。
from test_frontend_product_language import _without_comments

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"
EDITORS = (
    FRONTEND / "components" / "KnowledgeMatrix.tsx",
    FRONTEND / "components" / "CanonEventCast.tsx",
    # 2026-08-13：「改一改再收下」（`POST …/proposals/{id}/edit`）。改的是一条**还没
    # 生效**的事实，但作者面对的表单形状和上面两个一模一样——「顺手让他确认一下生效章」
    # 那个下午在这一格同样会来，而它同样没有第二个东西拦得住。
    FRONTEND / "components" / "ProposalReviewTab.tsx",
)
"""**发出编辑请求**的那几处（判据是「作者的输入进了哪个请求体」）。"""

EDIT_CONTROLS = EDITORS + (FRONTEND / "components" / "CastPicker.tsx",)
"""再加上**只画控件、不发请求**的那一份。

名单勾选框 2026-08-13 从 `CanonEventCast.tsx` 提进了 `CastPicker.tsx`（提案那一格要用
同一份控件和同一句「勾上的就是改完之后的名单」）。**这一行是那次搬家当场红出来的**：
只扫 `EDITORS` 的话，全部勾选框一次搬家就整体离开了守卫的视野，而扫描器不会喊一声——
`test_neither_editor_draws_a_box_that_asks_for_a_number` 末尾那条 `total >=` 自守卫
就是为这种失败准备的，它真的响了。
"""
CORRECTIONS_PY = ROOT / "src" / "novel_harness" / "corrections.py"


# ══════════════════════════════════════════════════════════════════════════
# 扫描器
# ══════════════════════════════════════════════════════════════════════════


def _balanced(source: str, start: int) -> str:
    """从 `source[start] == '{'` 取到配对的 `}`（字符串字面量里的括号不算数）。"""
    depth = 0
    quote: str | None = None
    i = start
    while i < len(source):
        char = source[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError("没找到配对的 `}` —— 扫描器读错了，它现在是永远绿的")


_KEY = re.compile(r"[{,]\s*[\"']?([A-Za-z_$][\w$]*)[\"']?\s*:")
"""对象字面量里的键名。

**要求前面是 `{` 或 `,`**，不是裸的 `标识符:`：后者会把三元表达式
（`cond ? believed : null`）的第一支当成键名，而误报会让人把守卫关掉
（`test_arch_guard.py` 的原话）。
"""


def mutation_keys(source: str) -> list[str]:
    """这份 TSX 里每一次 `.mutate({…})` 请求体的键名，**含嵌套**。

    嵌套要收是因为条件展开是真会写的形状：
    `...(needsValue ? { believed_value: v } : {})` —— 只看顶层的话，
    `...(guess ? { valid_from: n } : {})` 这种写法整条从守卫眼皮下溜走。
    """
    clean = _without_comments(source)
    out: list[str] = []
    for match in re.finditer(r"\.mutate\(\s*", clean):
        start = match.end()
        if start >= len(clean) or clean[start] != "{":
            continue  # `mutate(variable)` —— 没有字面量可扫
        out += _KEY.findall(_balanced(clean, start))
    return sorted(set(out))


_INPUT = re.compile(r"<input\b.*?/>", re.S)


def input_tags(source: str) -> list[str]:
    """这份 TSX 里的每一个 `<input …/>`。

    `[^>]*` 不行：`onChange={(e) => …}` 里的 `=>` 会让标签在中途被截断，
    而截断之后长在后面的 `type="number"` 就扫不到了。
    """
    return _INPUT.findall(_without_comments(source))


DEV_TERMS = re.compile(
    r"\bKNOWS\b|\bBELIEVES\b|\bUNKNOWN\b|\bPROVISIONAL\b|\bCANON\b|\bRETRACTED\b"
    r"|\bLOCATED_AT\b|\bHAS_STATE\b|\bRELATED_TO\b|\bSTALE\b|\bFRESH\b"
    r"|valid_from|valid_to|canon_version|believed_value|knower_ids|participant_ids"
    r"|edge_id|event_id|evidence_id|decision_log|information_scope"
    r"|§\s*[\d.]+"
    r"|\b[A-Za-z][A-Za-z0-9]*:[A-Za-z0-9]+"
    r"|\bCharacter\b|\bSecret\b|\bLocation\b|\bFaction\b|\bForeshadow\b|\bStateDim\b"
)
"""摆到小说作者脸上就是 bug 的那些字。

三类：引擎的枚举值（`KNOWS` / `PROVISIONAL` / `Character`）、库字段名
（`valid_from` / `canon_version` / `believed_value`）、内部标识（`edge:01J…`、
写给维护者的 `§3.2` 注脚）。**只拿它扫真的会上屏的字符串**——拿它扫源码会对着
类型定义和注释开火，而那种守卫会被关掉。

── 裸 id 那一条 2026-08-11 从**前缀白名单**换成了**形状** ──────────────────
原来写的是 `\\b(?:edge|event|node|secret|character|project|evidence|decision|
proposal|alias):`，而它当场漏掉两种真的上过屏的东西：

1. 白名单外的前缀 —— 展开层里的 `snapshot:01J…` 和 `artifact:sha256:…`
   （`snapshot` / `artifact` 都不在表里，于是那两行印了一整轮没人看见）；
2. **被截断的 id** —— `ProposalReviewTab` 的 `id.slice(-6)` 把 `location:ID22`
   截成 `n:ID22`，`node:` 那一条正是冲它写的，截完就逃掉了。

同 `MACHINE_CODE`：**词表只覆盖写它那天想得到的几个词。** 浏览器一侧的同一份判据在
`frontend/src/test/screenGuard.ts`（两个运行时各一份，两边的自守卫喂的是同一批探针）。
"""


def dev_terms_in(text: str) -> list[str]:
    return [m.group(0) for m in DEV_TERMS.finditer(text)]


def screen_strings(page: activity.ActivityPage) -> dict[str, str]:
    """活动记录页**真的印在屏幕上**的每一个字段 → 值。

    `ActivityLog.tsx` 渲染的就是这三样（`title` / `subtitle` / `jump.label`）；
    `payload` 不在里面是有意的——那一份组件一个字都不渲染，扫它会造出假红。
    """
    out: dict[str, str] = {}
    for entry in page.entries:
        out[f"{entry.id}.title"] = entry.title
        out[f"{entry.id}.subtitle"] = entry.subtitle
        if entry.jump is not None:
            out[f"{entry.id}.jump"] = entry.jump.label
    return out


REFUSAL_CLASSES = frozenset({"CorrectionRefused", "FactNotFound"})
"""改正层的每一个拒绝异常。**加一个不补这儿，它写的每一句话都在守卫视野之外。**

（这儿 2026-08-14 加过一个 `FactAlreadyThere`：「补一条」撞上这一格已经有内容 → 409。
它随秘密下线一起删了，ADR 0039——**那次加它的教训仍然成立**：新异常类的构造器在扫描器
眼里根本不存在，而它端着一句会原样摆到小说作者错误框里的话。）判据是「谁被塞进这几个
构造器」，所以
下一个新异常同样要写进这个集合——`test_every_refusal_class_is_scanned` 拿
`corrections.CorrectionError` 的子类逐个来比，漏一个当场红。
"""


def refusal_literals(source: str) -> list[tuple[int, str]]:
    """`corrections.py` 里每一句**拒绝文案**：`(行号, 那句话)`。

    判据是「谁被塞进 `CorrectionRefused(...)` / `FactNotFound(...)` / …」的 AST，不是 grep：
    那两个类名在模块 docstring 和注释里到处都是，grep 会对着文档开火。
    f-string 只取其中的字面量段（`{character.name}` 是数据，不是措辞）。
    """
    tree = ast.parse(source, filename="corrections.py")
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in REFUSAL_CLASSES:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append((node.lineno, arg.value))
            elif isinstance(arg, ast.JoinedStr):
                text = "".join(
                    part.value
                    for part in arg.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                out.append((node.lineno, text))
    return sorted(out)


# ══════════════════════════════════════════════════════════════════════════
# 世界：一次真的改正 + 一次真的名单编辑，全走 HTTP
# ══════════════════════════════════════════════════════════════════════════

QUOTE = "萧决在青云城主府第一次听说了血脉秘密的真相。"


@pytest.fixture
def edited(client: TestClient, book: dict[str, str]) -> dict[str, Any]:
    """把两个编辑入口各按一次，**走真路由**，返回坐标。

    直接 INSERT 或直接调 `corrections.*` 都量不到这条缝：要验的正是
    「HTTP 出参 → 浏览器」那一段，中间少一层就等于换了一份数据。
    """
    pid = book["pid"]
    base = f"/api/projects/{pid}"

    provisional = _seed_provisional_event(book)
    version = client.get(base).json()["canon_version"]
    confirmed = client.post(
        f"{base}/chapters/1/provisional/confirm",
        json={
            "fact_kind": "event",
            "fact_ids": [provisional],
            "expected_canon_version": version,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    canon_event = confirmed.json()["events"][0]["event"]["id"]

    version = client.get(base).json()["canon_version"]
    cast = client.post(
        f"{base}/canon/events/{canon_event}/cast",
        json={"knower_ids": [book["萧决"]], "expected_canon_version": version},
    )
    assert cast.status_code == 200, cast.text

    # 一次抽取运行 + 一次模型调用。**这不是装饰**：日志页有三种展开层，而在它们进来
    # 之前这个 fixture 里只有「确认」那一种——于是
    # `test_the_expanded_row_never_prints_an_engine_word` 只扫过三分之一的屏幕，
    # 另外两种里躺着 `snapshot:01J…` / `artifact:sha256:…` / 一整段 `params_json`
    # 印了一整轮没人看见。**守卫的样本不全 = 守卫在骗人。**
    call_id = seed_call(book)
    seed_run(book, 1, call_id=call_id)

    return {
        "pid": pid,
        "base": base,
        "cast": cast.json(),
        "event_id": canon_event,
    }
def test_the_request_schemas_still_take_no_chapter() -> None:
    """后端这一侧再钉一次（`test_no_chapter_input.py` 已有一条，这里量的是同一件事的
    另一半：**前端发得出的东西后端也收不下**）。"""
    for model in (EventCastEditRequest,):
        assert model_chapter_fields(model) == [], f"{model.__name__} 长出了章号字段"
        assert model.model_config.get("extra") == "forbid", (
            f"{model.__name__} 不是 extra=forbid —— 前端多塞一个 chapter 会被静默吃掉"
        )


CHAPTER_PROBE = """
correct.mutate({ character_id: a, secret_id: b, since_chapter: 88, expected_canon_version: v });
"""
SPREAD_PROBE = """
correct.mutate({
  ...(guess ? { valid_from: n } : {}),
  expected_canon_version: v,
});
"""
TERNARY_PROBE = """
correct.mutate({ knower_ids: changed ? picked.knowers : null, expected_canon_version: v });
"""
COMMENT_PROBE = """
// 这里写 since_chapter: 88 是不行的
correct.mutate({ character_id: a, expected_canon_version: v });
"""
NUMBER_BOX_PROBE = """
<input type="number" value={ch} onChange={(e) => setCh(+e.target.value)} min={1} />
"""


def test_the_frontend_scanner_can_see_a_chapter_box() -> None:
    """**守卫的自守卫。** 三种真会被写出来的坏形态各喂一次，都必须红。

    没有这一条，上面两条在 `.mutate(` 换个写法（比如 `mutateAsync`）的那天会安静地全绿。
    """
    assert "since_chapter" in mutation_keys(CHAPTER_PROBE)
    assert "valid_from" in mutation_keys(SPREAD_PROBE), (
        "条件展开里的键必须扫得到：`...(cond ? { valid_from: n } : {})` 是真会写的形状"
    )
    # 干净的那一面也要钉住：误报会让人把守卫关掉，而三元表达式在这两个组件里就有。
    assert mutation_keys(TERNARY_PROBE) == ["expected_canon_version", "knower_ids"]
    assert mutation_keys(COMMENT_PROBE) == ["character_id", "expected_canon_version"]
    assert 'type="number"' in input_tags(NUMBER_BOX_PROBE)[0]
def test_the_correction_layer_writes_no_engine_words() -> None:
    """`corrections.py` 自己写的每一句拒绝，都是作者读得懂的话。

    从源码扫而不是从运行时扫，是因为有几句今天从工作台到不了（比如「已闭合又被撤回」）
    ——**到不了不等于不会到**，而它到的那天没有任何东西会拦住它。
    """
    offenders = {
        f"corrections.py:{lineno}": dev_terms_in(text)
        for lineno, text in refusal_literals(CORRECTIONS_PY.read_text(encoding="utf-8"))
        if dev_terms_in(text)
    }
    assert not offenders, (
        f"改正层的拒绝文案里有引擎的词：{offenders}\n"
        "这些句子会**原样**出现在作者的错误框里（`api/review.py::_correction_error`\n"
        "把 `str(exc)` 直接放进 `message`）。"
    )


_REWRITE = re.compile(
    r"/(?![/*])(?:\\.|\[[^\]]*\]|[^/\\\n])+/[dgimsuvy]*"  # 正则字面量
    r"|\.replace\(\s*[\"'][^\"']*[\"']"  # 字符串版的 replace
)
"""**把一段文字改写成另一段文字**的那些写法。

只认这两种是有意的：`{ KNOWS: "知道" }` 这种「结构化枚举 → 标签」的表**是合法的**
（`KnowledgeCell.state` / `ActivityEntry.actor` / `NodeRef.label` 后端发的就是枚举，
不是句子，总得有人把它画成中文，CLI 那边同样自己画）。不合法的是**改写后端已经
写好的一句话**——那才是第二份措辞源。
"""

_ENGINE_WORD = re.compile(r"KNOWS|BELIEVES|UNKNOWN|PROVISIONAL|believed_value|§")


def rewrite_patterns(source: str) -> list[str]:
    """这份源码里「改写一段文字」的每一处模式，只保留提到引擎词的那些。

    **文件里没有 `.replace(` 就直接放过**：改写只能经由它发生，而一个引擎词长在
    正则里的合法用法是有的（`harness.tsx` 拿 `?scope=PROVISIONAL` 匹配 URL）。
    不设这一条，这道守卫第一天就会对着路由表开火。
    """
    clean = _without_comments(source)
    if ".replace(" not in clean:
        return []
    return [m.group(0) for m in _REWRITE.finditer(clean) if _ENGINE_WORD.search(m.group(0))]


def test_the_frontend_keeps_no_second_glossary() -> None:
    """**前端不许把后端写好的那句话再改写一遍。**

    有了那张表，后端就可以一直吐 `KNOWS` —— 而表只覆盖写它那天想得到的几个词
    （`Character` / 裸 id / 「面板 §3.2」全在外面），第七个词照样上屏；更糟的是
    屏幕上的说法和 CLI、和日志不再是同一句话。这条约束在 `activity.py` 的措辞表
    那一节里已经写着（国际化第四批之后，那一节改口成"源搬到前端的
    `backendMessages.ts`，但源仍然只有一个"——被推翻的是"源必须在后端"，
    不是"源只能有一个"），这里把它变成一件 CI 事项。

    **注意它不禁「枚举 → 中文」的表**：`STATE_ZH` / `ACTOR_ZH` 那种是画一个结构化字段，
    合法且必要。一道连它们也咬的守卫会被关掉，而关掉的守卫等于没有守卫。
    """
    offenders: list[str] = []
    for path in sorted(FRONTEND.rglob("*.ts")) + sorted(FRONTEND.rglob("*.tsx")):
        if path.name.endswith((".test.ts", ".test.tsx")) or "__fixtures__" in path.parts:
            continue
        for pattern in rewrite_patterns(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(ROOT)}: {pattern!r}")
    assert not offenders, (
        "前端在改写后端写好的句子（第二份措辞源）：\n  " + "\n  ".join(offenders) + "\n"
        "后端的那句话要么本来就该是作者的话（去改 `corrections.py` / `activity.py`），\n"
        "要么根本不该给作者看。"
    )


GLOSSARY_PROBE = (
    'const G = [[/\\bKNOWS\\b/g, "「知道」"], [/§[\\d.]+/g, ""]];\n'
    "for (const [p, zh] of G) out = out.replace(p, zh);"
)
ENUM_TABLE_PROBE = (
    'const STATE_ZH: Record<K, string> = { KNOWS: "知道", BELIEVES: "以为" };\n'
    'const t = url.replace("/api", "");'
)
STRING_REPLACE_PROBE = 'const shown = message.replace("BELIEVES", "以为");'
ROUTE_TABLE_PROBE = '{ match: /\\/events\\?scope=PROVISIONAL/, body: fixtures.eventsProvisional }'


def test_the_glossary_scanner_can_see_a_rewrite() -> None:
    """**守卫的自守卫**：两种改写各喂一次，外加一张合法的枚举表防误报。"""
    assert len(rewrite_patterns(GLOSSARY_PROBE)) == 2, "正则版的词表必须扫得到"
    assert rewrite_patterns(STRING_REPLACE_PROBE), "字符串版的 replace 同样是改写"
    assert rewrite_patterns(ENUM_TABLE_PROBE) == [], (
        "「枚举 → 中文」的表不许被咬：后端发的是枚举不是句子，画它是前端的活"
    )
    assert rewrite_patterns(ROUTE_TABLE_PROBE) == [], (
        "拿引擎词匹配 URL 不是改写措辞——对着路由表开火的守卫会被关掉"
    )


DIRTY_MESSAGE_PROBE = "「萧决」对「血脉秘密」已经是 BELIEVES 了"
DOC_REF_PROBE = "改成以为必须说出他以为的是什么——面板 §3.2 渲染的就是这句话"
RAW_ID_PROBE = "边 edge:01J8XK 是一条已闭合又被撤回的历史事实"
SNAPSHOT_PROBE = "正文快照 snapshot:01J8XK · prompt 指纹 artifact:sha256"
TRUNCATED_ID_PROBE = "当前：萧决 在 n:ID22"
CLEAN_MESSAGE_PROBE = "「萧决」对「血脉秘密」已经是「以为」了 —— 这一处不用再改"
CLEAN_ROW_PROBE = "模型调用 · 抽取 · deepseek-v4 · 入 1200 / 出 400 token · 900 ms"


def test_the_wording_scanner_can_see_an_engine_word() -> None:
    """**守卫的自守卫**：真出现过的脏句子各喂一次，加两句干净的防误报。

    后两个探针是 2026-08-11 补的，它们是**这张网从前缀白名单换成形状**的理由：
    展开层里的 `snapshot:` / `artifact:` 不在旧白名单里，而 `n:ID22` 是一个被截断到
    连前缀都没剩下的 id。旧判据对这三样一个都不红。
    """
    assert dev_terms_in(DIRTY_MESSAGE_PROBE) == ["BELIEVES"]
    assert dev_terms_in(DOC_REF_PROBE) == ["§3.2"]
    assert dev_terms_in(RAW_ID_PROBE) == ["edge:01J8XK"]
    assert dev_terms_in(SNAPSHOT_PROBE) == ["snapshot:01J8XK", "artifact:sha256"]
    assert dev_terms_in(TRUNCATED_ID_PROBE) == ["n:ID22"]
    assert dev_terms_in(CLEAN_MESSAGE_PROBE) == [], "干净的句子不许被咬——假红会让人关掉守卫"
    # 时间戳里的冒号前面是数字，型号里的连字符不是下划线——都不许被咬。
    assert dev_terms_in(CLEAN_ROW_PROBE) == []
    assert dev_terms_in("2026-08-11 01:28:40") == []
# ══════════════════════════════════════════════════════════════════════════
# 线 4：`actor` —— 前端说不了「谁改的」
# ══════════════════════════════════════════════════════════════════════════


def test_neither_request_schema_lets_the_caller_say_who_did_it() -> None:
    """`actor` 一旦是入参，「系统改的 vs 作者改的」就由调用方说了算。

    ADR 0020 拿「事后可查」换掉了「事前逐条确认」，而那份日志唯一的价值就是
    **分得清哪几步是系统自己动的手**。前端能填这一栏 = 那个区分作废。
    """
    for model in (EventCastEditRequest,):
        assert "actor" not in model.model_fields, f"{model.__name__} 收了 actor"
# ══════════════════════════════════════════════════════════════════════════
# 线 2 续：**错误码不是一句话**
# ══════════════════════════════════════════════════════════════════════════
#
# 上面那几条量的是「后端写的那句话干不干净」。它们全绿，而屏幕上照样出现了
# `project_not_found` —— 因为**有几种拒绝后端根本没写话**，只发了一个 `error` 码，
# 而 `client.ts` 的 `ApiError` 拿 `body.message || body.error` 当 message，
# 前端把那个码原样摆进了错误框。
#
# 「后端写的每一句都干净」和「屏幕上每一句都是话」是两件事：**中间漏的是「后端一句都
# 没写」的那一格。** 下面两条钉住的就是那一格真的存在（前端因此必须自己兜底），
# 浏览器一侧的红在 `frontend/src/components/CanonEdit.boundary.test.tsx`。
#
# 判据换成**形状**而不是词表：一个中文界面上出现 `a_b_c` 形状的英文词就是机器码。
# 词表只覆盖写它那天想得到的几个词——`DEV_TERMS` 里就没有 `stale_base_version`，
# 也没有 `project_not_found`，而这两个正是真的上了屏的那两个。

MACHINE_CODE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def machine_codes(text: str) -> list[str]:
    """这段字里的 snake_case 标识符 —— 错误码、库字段名、枚举键。"""
    return sorted(set(MACHINE_CODE.findall(text)))


def _author_facing_sentence(detail: dict[str, Any]) -> str | None:
    """后端在这一条拒绝里**写给作者的那句话**。没写就是 `None`。

    判据只认 `message`：`error` 是给代码分支用的码，把它当话说出来就是把
    `project_not_found` 摆到小说作者脸上。
    """
    said = detail.get("message")
    return said if isinstance(said, str) and said.strip() else None
def test_the_code_scanner_can_see_a_bare_code() -> None:
    """**守卫的自守卫。** 真上过屏的那两个码各喂一次，加两句干净的防误报。

    没有这一条，`MACHINE_CODE` 哪天被改窄（比如要求 `\\b_\\b`）之后，
    上面两条会安静地全绿，而屏幕上照样印着码。
    """
    assert machine_codes("stale_base_version") == ["stale_base_version"]
    assert machine_codes("project_not_found") == ["project_not_found"]
    # 词表漏掉的正是这两个 —— 把这件事也钉住，免得有人以为 DEV_TERMS 够用。
    assert dev_terms_in("stale_base_version") == [], (
        "如果 DEV_TERMS 有一天收了它，这里改成断言两道判据都红；"
        "但**别把这条守卫换成往词表里加一行**——下一个码照样在表外"
    )
    assert machine_codes("「萧决」对「血脉秘密」现在就是你要改成的那一种") == []
    assert machine_codes("已确认的情节（谁在场、谁知道了）· 可信程度 60%") == []


# ══════════════════════════════════════════════════════════════════════════
# 线 2 再续：**改正层没写的那几句** —— 事件仓储的话原样穿到了作者脸上
# ══════════════════════════════════════════════════════════════════════════
#
# 上面两条（`test_every_refusal_the_editors_can_show_is_in_the_authors_words` 从运行时、
# `test_the_correction_layer_writes_no_engine_words` 从源码）合起来看着很密，**中间却有
# 一整格是空的**：
#
# - 源码那条扫的是 `CorrectionRefused(…)` / `FactNotFound(…)` 的**字面量实参**。
#   `_event_failure` 里那三处写的是 `CorrectionRefused(str(exc))` —— 实参是一次
#   `ast.Call`，扫描器按定义跳过它，于是**那几句话根本不在被扫的集合里**。
# - 运行时那条只跑「浏览器里真到得了」的几种，而名单编辑器那一侧它只跑了一种
#   （「名单没变」），而那一种的话恰好是 `corrections.py` 自己写的字面量。
#
# 空的那一格里装着的是 `graph/sqlite_events.py` 写给**维护者**的诊断句：
# `event {id} 是 ACTIVE / STALE`、`只能改已生效（CANON）的事件，{id} 是 PROVISIONAL`、
# `名单里只能是 Character，{id} 是 Secret`。它们经 `_event_failure` → `str(exc)` →
# `api/review.py::_correction_error` 的 `message` → `correctionError.ts` **原样**渲染。


def _cast_refusals(
    client: TestClient, book: dict[str, str], edited: dict[str, Any]
) -> dict[str, Any]:
    """名单编辑器上，**话不是 `corrections.py` 写的**那几种拒绝，各按一次。

    三种都从浏览器到得了：①②是「作者摊开编辑器的这段时间里，后台把那条情节整理掉了
    / 它压根还没确认」——ADR 0020 的常态（离开一章就自动整理）；③是名单那一维收了个
    不是人物的 id（候选人里那半个并集来自这条情节现有的名单，后端不认才拒）。
    """
    base = edited["base"]
    version = lambda: client.get(base).json()["canon_version"]  # noqa: E731
    provisional = _seed_provisional_event(book)
    return {
        "那条情节不在了": client.post(
            f"{base}/canon/events/event%3AGONE/cast",
            json={"knower_ids": [book["萧决"]], "expected_canon_version": version()},
        ),
        "还没确认的那一条": client.post(
            f"{base}/canon/events/{provisional}/cast",
            json={"knower_ids": [book["萧决"]], "expected_canon_version": version()},
        ),
        "名单里放了个不是人物的": client.post(
            f"{base}/canon/events/{edited['event_id']}/cast",
            json={
                "knower_ids": [book["萧决"], book["青云城主府"]],
                "expected_canon_version": version(),
            },
        ),
    }
def test_every_refusal_class_is_scanned() -> None:
    """**扫描器的名单 == 改正层真有的那几个异常类。**

    上面两个 AST 扫描器按**类名**认拒绝，而类名是手写的。新加一个异常类而这儿不补，
    它写的每一句话（会原样进作者的错误框）就整批离开守卫视野，且没有任何东西会喊一声
    ——这正是 `EDIT_CONTROLS` 那一行记着的那种失败（一次搬家，整批控件离开扫描面）。
    """
    from novel_harness import corrections

    real = {
        name
        for name, obj in vars(corrections).items()
        if isinstance(obj, type)
        and issubclass(obj, corrections.CorrectionError)
        and obj is not corrections.CorrectionError
    }
    assert real == set(REFUSAL_CLASSES), (
        f"改正层的拒绝异常是 {sorted(real)}，扫描器认的是 {sorted(REFUSAL_CLASSES)}。\n"
        "补 `REFUSAL_CLASSES`——名单少一个，那个类写的每一句话都不再被任何守卫看着。"
    )


def _refusal_call_args(source: str) -> list[tuple[int, str]]:
    """`CorrectionRefused(...)` / `FactNotFound(...)` / … 的**每一个**实参，含非字面量。

    和 `refusal_literals` 是一对：那个只收字面量（它要扫的是「措辞」），这个收全部
    （它要问的是「有没有一句话根本不是这儿写的」）。
    """
    tree = ast.parse(source, filename="corrections.py")
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in REFUSAL_CLASSES:
            continue
        for arg in node.args:
            out.append((node.lineno, ast.unparse(arg)))
    return sorted(out)


def test_no_refusal_borrows_someone_elses_sentence() -> None:
    """**结构守卫**：改正层的每一句拒绝都由改正层自己写，不许转发别人的 `str()`。

    这是上面那条运行时断言的源码版，理由和 `test_the_correction_layer_writes_no_engine_words`
    的一样：**到不了不等于不会到**。而它比那条严一格——那条只看得见字面量，
    于是「借来的那句话」在它眼里根本不存在（这条缝就是这么漏的）。
    """
    borrowed = [
        f"corrections.py:{lineno}: {arg}"
        for lineno, arg in _refusal_call_args(CORRECTIONS_PY.read_text(encoding="utf-8"))
        if not arg.startswith(("'", '"', "f'", 'f"'))
    ]
    assert not borrowed, (
        "改正层在转发别人写的句子：\n  " + "\n  ".join(borrowed) + "\n"
        "那些句子是写给维护者的诊断（带枚举值和裸 id），而它们会**原样**出现在\n"
        "小说作者的错误框里。按异常类型给出改正层自己的一句话。"
    )


BORROWED_PROBE = """
def _event_failure(exc):
    if isinstance(exc, EventCastError):
        return CorrectionRefused(str(exc))
    return FactNotFound(f"这条情节现在不在了")
"""


def test_the_borrowed_sentence_scanner_works() -> None:
    """**守卫的自守卫**：转发的那一种必须被看见，自己写的那一种不许被咬。"""
    found = _refusal_call_args(BORROWED_PROBE)
    assert ("str(exc)" in arg for _, arg in found)
    borrowed = [arg for _, arg in found if not arg.startswith(("'", '"', "f'", 'f"'))]
    assert borrowed == ["str(exc)"], f"扫描器没分清借来的和自己写的：{found}"


# ══════════════════════════════════════════════════════════════════════════
# 线 2 收尾：措辞表**漏一行**的那一天
# ══════════════════════════════════════════════════════════════════════════
#
# 上面几条量的是「今天屏幕上有没有引擎的词」。这一条量的是**明天**：`activity.py` 的
# 那几张表把封闭枚举画成中文，而这次改动**自己就往 `DecisionKind` 里加了两行**
#（`knowledge_edit` / `event_edit`）。加的人记得补表，所以今天是绿的——
# 而「记得补」不是一道守卫。


def test_the_wording_tables_cover_every_value_they_can_be_handed() -> None:
    """四张「封闭枚举 → 中文」的表，一行都不许漏。

    `_edge_label` 的 docstring 已经把代价写清楚了：漏掉的那一行会以 `RELATED_TO` 的
    形态出现在小说作者的屏幕上。这条把那句话变成一件 CI 事项。
    """
    from novel_harness.graph import EdgeType, NodeLabel

    missing = {
        "_KIND_LABEL": [k.value for k in decisions.DecisionKind if k.value not in activity._KIND_LABEL],
        "_EDGE_LABEL": [e.value for e in EdgeType if e.value not in activity._EDGE_LABEL],
        "_NODE_LABEL": [n.value for n in NodeLabel if n.value not in activity._NODE_LABEL],
        "_VERDICT_LABEL": [
            v.value for v in decisions.Verdict if v.value not in activity._VERDICT_LABEL
        ],
    }
    offenders = {name: gap for name, gap in missing.items() if gap}
    assert not offenders, (
        f"活动记录的措辞表漏了行：{offenders}\n"
        "漏掉的那一行会以引擎枚举的原样出现在小说作者的屏幕上（`activity.py` 的\n"
        "措辞表那一节）。补表——这几张表眼下还在后端，笔二（国际化第四批）会把它们\n"
        "搬到前端 backendMessages.ts，但今天仍归后端管，别在前端加第二份映射。"
    )
    # **守卫的自守卫**：四个枚举都真的有值可查（`model_fields` / `Enum` 换形状的那天，
    # 上面四个列表推导会一起变成空，而四条断言会安静地全绿）。
    counted = {
        len(decisions.DecisionKind),
        len(EdgeType),
        len(NodeLabel),
        len(decisions.Verdict),
    }
    assert min(counted) >= 3, "枚举遍历为空 —— 上面那条是永远绿的"
    assert "not_a_real_kind" not in activity._KIND_LABEL, (
        "判据本身要认得出「不在表里」这件事"
    )


def test_a_missing_row_never_falls_back_to_the_engines_word() -> None:
    """**表漏了行的那一天，退路也得是中文。**

    完整性守卫拦的是「有人加枚举忘了补表」，但补表这件事本身可以在一次 rebase 里
    被改没。`_edge_label` / `_node_label` 已经是这么写的（认不出退到「关系」/「条目」）；
    `_kind_label` 不是——它把 `knowledge_edit` 原样回吐，而那正是这次改动新加的两个值
    之一。**两条判据一起，才轮不到运气。**
    """
    unknown = {
        "_kind_label": activity._kind_label("some_brand_new_kind"),
        "_edge_label": activity._edge_label("SOME_NEW_EDGE"),
        "_node_label": activity._node_label("SomeNewLabel"),
        "_verdict_label": activity._verdict_label("some_new_verdict"),
    }
    offenders = {
        name: text
        for name, text in unknown.items()
        if machine_codes(text) or dev_terms_in(text) or not re.search(r"[一-鿿]", text)
    }
    assert not offenders, (
        f"措辞表认不出一个值的时候，把引擎的词摆给了作者：{offenders}\n"
        "封闭枚举认不出只可能是表漏了行，而漏的那一行不该由小说作者来读。"
    )
# ══════════════════════════════════════════════════════════════════════════
# 线 1 收尾：**整个前端**，不只是这两个编辑器
# ══════════════════════════════════════════════════════════════════════════


NAMED_CHAPTER_BOXES = (
    ("ChapterTitle.tsx", "chtitle-edit"),
    ("ChapterTitle.tsx", "chtitle-find"),
)
"""标签里带「章」字、但**收的不是章号**的输入框，逐个点名。

点名的形式是 `(文件, 那个框自己的 class)`：两者都对上才算数，所以换个文件、
改个类名都会红一次——**红一次的意思是「再想一遍它收的是不是章号」**，不是「去表里补一行」。
每一个为什么正当，写在下面那个函数的 docstring 里；那段话是这张表的唯一依据。
"""


def test_no_screen_in_the_whole_workbench_posts_a_chapter() -> None:
    """浏览器发出去的**任何**请求体里都没有章号键，且全前端只有一个数字输入框。

    上面那条只扫这次新加的两个编辑器。而 §5.9 讲的那个下午不挑组件——
    「顺手让作者确认一下生效章」在哪一格写出来都是同一件事，而这两条编辑路由只是
    今天最像会长出它的两处。

    **数字框今天是字面意义的零**（2026-08-13 起）：全前端**一个 `<input type="number">` 都没有**。
    在此之前唯一那个在 `DraftLengthControls.tsx`（问的是一段草稿写多长，不是第几章），
    它随「章节准备」那一页一起删了——那个控件写进 localStorage 的值没有任何人读，
    起草走的是后端的产品默认档。没有一个 `.mutate({…})` 的键撞得上章号
    （换章、后台整理、补总结都把章号放在**路径**里，那是 AS OF，是查询不是声明）。

    所以这条断言现在是 `== 0`：**下一个数字框出现时它必红**，而红了之后要问的是
    「它问的是不是第几章」——是就删，不是就把它写进这段话里说清为什么正当。

    所以这条是**零基线**守卫，不是允许清单守卫——真出现一个正当的例外时，
    该做的是把那一个具体的键写进这段话里说明为什么它正当，**不是**加一个开口
    （`test_no_chapter_input.py` 的原话：例外会被拓宽）。

    ── 逐个点名放行的两个（2026-08-13，章节选择器从顶栏搬进中栏那行章标题）──────

    两个都在 `ChapterTitle.tsx`，**都不收章号**，理由各自不同：

    - `chtitle-edit`（「改这一章的标题」）收的是**标题那行字**。章标题在磁盘上就是
      正文的第一行（`importer.chapter_files()` 只读到首个非空行），所以这个框改的是
      正文，落盘走 `PUT /chapters/{n}/text`——**章号在路径里，是这块屏幕正开着的那一章**，
      不是作者敲进去的。
    - `chtitle-find`（「按章号或标题找…」）是**一个过滤器**：敲进去的字一个字节都不出浏览器，
      它只决定下拉单子上还剩哪几行。挑中之后走 `openChapter(n)`，n 来自那本书**已经存在**
      的章目录，进的还是路径。查询不是声明。

    判据始终是 `test_no_chapter_input.py` 那一条：**作者的输入能不能到达 `valid_from`**。
    这两个都到不了。真到得了的那一个长什么样，看上面那条 offenders 断言——
    它扫的是请求体的键，而请求体是章号唯一可能被「填」进去的地方。
    """
    offenders: list[str] = []
    numbers: list[str] = []
    named: list[str] = []
    for path in sorted(FRONTEND.rglob("*.tsx")) + sorted(FRONTEND.rglob("*.ts")):
        if path.name.endswith((".test.tsx", ".test.ts")) or "__fixtures__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for key in mutation_keys(source):
            if BANNED.search(key):
                offenders.append(f"{path.relative_to(ROOT)}: {key}")
        for tag in input_tags(source):
            flat = " ".join(tag.split())
            # **两个筐分开装**：合成一个的话，给下面那两个点过名的框补一个
            # `type="number"` 就能溜过去——而那正好是这道守卫要拦的那一种。
            if 'type="number"' in flat:
                numbers.append(f"{path.relative_to(ROOT)}: {flat[:120]}")
            elif "章" in flat:
                named.append(f"{path.relative_to(ROOT)}: {flat[:120]}")

    assert not offenders, (
        "前端往请求体里放了章号：\n  " + "\n  ".join(offenders) + "\n"
        "章号是「这句引语落在哪一章」的产物，不是作者的输入（约束 10 / ADR 0006）。"
    )
    assert not numbers, (
        f"全前端多出了数字输入框：{numbers}\n"
        "2026-08-13 起这儿的基线是**零**。新出现的这一个要是在问第几章，\n"
        "它就是 §5.9 说的那个邀请污染的表单；不是的话，把它写进本函数 docstring 说清楚。"
    )
    assert len(named) == len(NAMED_CHAPTER_BOXES) and all(
        any(where in box and what in box for box in named) for where, what in NAMED_CHAPTER_BOXES
    ), (
        f"标签里带「章」字的输入框变了：{named}\n"
        f"点过名的只有这几个：{NAMED_CHAPTER_BOXES}（为什么正当写在本函数 docstring 里）。\n"
        "新长出来的那一个**要先在那段话里说清它收的不是章号**，再加进这张表——\n"
        "顺手加一行放进去，这道守卫就退化成一张会被拓宽的允许清单。"
    )


# ── 那两句的**运行时**红为什么没有（诚实说明）────────────────────────────────
#
# `correct_knowledge` 里那两句借来的（`node_refs` 的「节点不存在或跨项目」、
# `retract_canon` 的「边 … 已不是 ACTIVE、未闭合、非 STALE 的 current Canon」）
# **今天从浏览器按不出来**，实测过两条：
#
# - 删节点没有路由，所以 `node_refs` 找不到人这件事到不了。
# - `retract_canon` 和读端（`queries.current_knowledge_edges`）唯一的判据差是
#   `evidence_status is STALE`——而**今天没有任何东西会把一条证据标成 STALE**
#   （`importer.py` 模块头那段写着「标成 STALE、重定位、重校验是 M4，不是这里」；
#   实测：改掉那句引语再存，边仍然是 FRESH）。
#
# 所以这两句由 `test_no_refusal_borrows_someone_elses_sentence` 从源码钉住，理由和
# `test_the_correction_layer_writes_no_engine_words` 的一模一样：**到不了不等于不会到**，
# 而 M4 把 STALE 接上的那天，这条读端/撤回端的判据差会立刻变成一条常走的路。
