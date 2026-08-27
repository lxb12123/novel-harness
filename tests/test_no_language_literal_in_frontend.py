"""前端不许再硬编码续写语言（国际化第一批 ②）。

── 这条测试拦的是哪一种坏法 ────────────────────────────────────────────────

`frontend/src/api/hooks.ts` 曾经写着：

    export const CONTINUATION_LENGTH = { language: "zh", ... };

续写永远走中文那一支，英文那半（`EN_WRITING_PROMPT` / `count_units` 早就是双语的，
ADR 0011 铺好了）永远跑不到——**而没有任何一处会红**：`tsc` 不知道这是「应该跟着
`Project.language` 走的一位」，pytest 看不见 `.ts`，作者只会觉得「AI 好像认不出
这本书是英文的」。

`docs_dev/2026-08-22-模式一整体任务台账.md` 第 8 条点名这类缝已经在本仓库栽过三次：
后端算出来的值被前端一个写死的字面量悄悄架空，且症状只在生产链路上才看得出来。
同一形状的纪律仓库里已经有两条：`tests/test_continuation_tail_limit.py`
（续写上文上限）、`tests/test_serve.py::test_vite_outdir_and_dist_agree`
（Vite outDir / FastAPI _DIST）。这是第三条。

── 判据是形状，不是词表 ────────────────────────────────────────────────────

拦的不是「zh」「en」这两个字符串本身——`BookShelf.tsx` 的语言覆盖开关合法地写着
`setLanguage.mutate("zh")`，`types.ts` 的 `DraftLanguage = "zh" | "en"` 也合法。
拦的是**这一个特定形状**：对象字面量里 `language` 这个键直接赋一个 `"zh"`/`"en"`
字符串常量——那正是「把语言焊死在请求体里」唯一会长出来的样子。真要给类型标注
（`language: DraftLanguage`）或做值比较（`language === "zh"`）都过得去这条正则，
故意留的口子，不是漏网。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend" / "src"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?<!:)//[^\n]*")
# `(?!\s*\|)` 是这条正则的全部关键：`language: "zh" | "en"` 是**类型标注**
# （联合类型，字面量在类型位置上，合法且到处都是——`Project.language`、
# `useSetProjectLanguage` 的参数类型都长这样），`language: "zh",` /
# `language: "zh" }` 才是**对象字面量赋值**（真的把语言焊死成了一个值）。
# 没有这条否定前瞻，第一次跑这条测试就会把自己的类型标注当成违规——已经踩过一次。
_LANGUAGE_LITERAL = re.compile(r"""language\s*:\s*['"](?:zh|en)['"](?!\s*\|)""")


def _code_only(source: str) -> str:
    """把注释剥掉，只留会真的跑起来的那部分（同 `test_continuation_tail_limit.py`）。

    **注释里可以出现 `language: "zh"`**——这条测试自己的模块 docstring 就写了一句
    历史病历，把病历一起禁掉等于让下一个人不知道为什么不能写死这个字段。
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))


def test_no_frontend_source_hardcodes_a_language_literal() -> None:
    """整个前端源码树里，`language: "zh"` / `language: "en"` 这个形状一次都不许出现。

    **扫全树，不扫一份点名的清单**：同类型的另外两条守卫（`test_continuation_tail_
    limit.py` / `test_wording_guard.py`）都点名具体文件，理由是「谁在做那一刀」；
    但那条理由的代价是「这一刀搬到新文件就得记得往清单里加一行」——续写语言这个字段
    只应该有一个合法来源（`Project.language`），全树扫描不会有正常写法误中，
    却能防住「有人在一个没被点名的新文件里重新焊死语言」这条路。
    """
    offenders: dict[str, list[str]] = {}
    for path in FRONTEND.rglob("*.ts*"):
        if path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
            continue
        code = _code_only(path.read_text(encoding="utf-8"))
        hits = _LANGUAGE_LITERAL.findall(code)
        if hits:
            offenders[path.relative_to(REPO).as_posix()] = hits
    assert offenders == {}, (
        f"这些文件把语言焊死在了对象字面量里：{offenders}\n"
        "语言是 `Project.language`（国际化第一批 ②），不是某处写死的默认值——"
        "续写请求今天已经不带 `length.language` 这个字段了（`ContinuationLengthSpec`），"
        "后端从 `proj.language` 补。真要读语言，去 `useProjects()` 的结果里找，"
        "别在这儿写一个新的字面量。"
    )


def test_continuation_length_request_has_no_language_field() -> None:
    """`CONTINUATION_LENGTH` 这个具体常量**不带 `language` 这个键**。

    上一条测的是「形状」，这一条测的是「这个曾经翻过车的常量，字段名都不许再出现」——
    同 `test_continuation_tail_limit.py::test_the_old_frontend_constant_is_gone_for_
    good` 那条「不只咬数字，也咬名字」的第二层网。
    """
    source = _code_only((FRONTEND / "api" / "hooks.ts").read_text(encoding="utf-8"))
    match = re.search(
        r"CONTINUATION_LENGTH\s*=\s*\{(.*?)\}\s*as const", source, re.DOTALL
    )
    assert match, "hooks.ts 里找不到 `CONTINUATION_LENGTH = { … } as const`——它是这条缝的另一半"
    assert "language" not in match.group(1), match.group(1)
