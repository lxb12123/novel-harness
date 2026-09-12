"""**边界一的网**：模式二的工具，一个字的秘密都不许交出去（ADR 0019 边界一）。

ADR 0019 把修复成本按边界拆开过，只有这一条写着「**最贵，而且不可回收**」：

> 一旦某个工具把 `Node` 交出去过，那段秘密就已经在作者的持久化对话里了，改代码不会把
> 它删掉。所以这一条必须**先有测试**：喂一个带 `twist` 的 Secret，断言工具返回和 prompt
> 里一个字都不出现它。

别的边界坏了都能修：边界二改个签名、边界三清掉会话表里那份多余的正文、边界四把含事实的
总结作废重生成。**这一条修不了**——对话是持久化的，秘密进去之后每一轮起草它都还在上下文里。
所以这份文件先于 agent loop（3.3）、先于会话表（3.4）存在，而不是跟着它们一起来。

── 这张网罩住哪四个面 ──────────────────────────────────────────────────

不是「工具的返回值」一个面，是**每一处模型看得见的字**：

1. **工具声明**（`tool_declarations()`）—— 它随每一次请求发出去，而且按边界六它还是
   少数几个能进稳定前缀的东西，写错一次就被缓存住。
2. **工具的返回值**（`ToolOutcome.content`）—— 成功那条路。
3. **工具的错误返回** —— 失败那条路。一句「「幽泉窟」还没登场，它第 200 章才出现，
   内容是……」同样是泄漏，而它长得像一句好心的解释。
4. **交给起草侧的那份约束**（`DraftContext`）—— 它是**真正进 prompt 的那一份**。
   前三个面全干净而这一面漏了，症状是模型写出来的正文里出现了不该出现的东西，
   而没有任何一条断言会红。

── 反向断言同样是必须的 ────────────────────────────────────────────────

**过度收窄是同一个 bug 的另一面**：清单里只剩一串 ULID 的话，作者看不出系统在拦哪条约束，
面板上那句「本场景 must_not_reveal：血脉秘密 · 玄铁令下落」（PLAN §3.2 画着的）就没了。
所以每条泄漏断言旁边都有一条「显示名必须在」。

── 自守卫（本仓惯例）──────────────────────────────────────────────────

**一个永远绿的守卫比没有守卫更糟，因为它还提供安全感。** 上面那些断言全靠「网真的看得见
泄漏」，所以下面用一个**故意泄漏的假工具**喂进同一张网，断言它红；再用一个干净的，
断言它不误报（误报会让人把守卫关掉，而关掉的守卫等于没有）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from novel_harness import project
from novel_harness.agent import tools as agent_tools
from novel_harness.agent.candidates import DraftCandidate
from novel_harness.agent.ports import DraftProduct, LandingReport, StoredDraft
from novel_harness.agent.tools import (
    TOOL_NAMES,
    TOOL_TABLE,
    ConstraintsResult,
    DraftAsk,
    SceneConstraintsArgs,
    ToolContext,
    ToolSpec,
    dispatch,
    dispatch_all,
    tool_declarations,
)
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.declare import Ledger
from novel_harness.draft.context import DraftContext, ResolvedConstraints
from novel_harness.draft.provider import ToolCall
from novel_harness.graph import (
    AliasKind,
    ChapterSpec,
    Node,
    NodeLabel,
    NodeProps,
    NodeRef,
    NodeSpec,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph

# 泄漏物：作者写在节点上的东西。**出现在任何一个模型看得见的面上都是泄漏**
# （`graph.models.NodeRef` 的 docstring 记着这两种实测形态）。
TWIST = "萧决其实是魔尊之子第200章揭晓"
SECRET_DESC = "血脉的真相是他母亲换了孩子"
PLOT_NOTE = "萧决在幽泉窟被顾清音所杀"

SECRET_CONTENT = {
    "Secret 节点 props 上的 twist": TWIST,
    "secret 扩展表的 description": SECRET_DESC,
    "未来地点 props 上的 plot_note": PLOT_NOTE,
}

CHAPTER = 7
KNOWS_QUOTE = "顾清音在藏书阁里读到了血脉秘密的真相。"
WHERE_QUOTE = "萧决独自走进了北荒的风雪里。"
CHAPTER_TEXT = f"{KNOWS_QUOTE}\n\n{WHERE_QUOTE}\n"


# ══════════════════════════════════════════════════════════════════════════
# 一本真书（走生产写路径建，不是手写数据）
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class World:
    conn: Connection
    project_id: str
    store: SqliteStoryGraph
    root: Path

    def context(self, **overrides: Any) -> ToolContext:
        base: dict[str, Any] = {
            "store": self.store,
            "project_id": self.project_id,
            "root_path": str(self.root),
        }
        base.update(overrides)
        return ToolContext(**base)


@pytest.fixture
def conn() -> Iterator[Connection]:
    connection = connect(IN_MEMORY)
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def world(conn: Connection, tmp_path: Path) -> World:
    """一个秘密（带 twist + 扩展表描述）、一个第 200 章才首现的地点（带 plot_note）、
    一章正文里提到两个人，其中一个知道那个秘密、另一个不知道。

    「另一个不知道」是本文件全部断言的前提：`must_not_reveal` 的判据是「在场的人里
    **至少有一个**还不知道」，两个人都知道的话清单是空的，网罩住的就是一个空集合。
    """
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters" / f"{CHAPTER:04d}.md").write_text(CHAPTER_TEXT, encoding="utf-8")

    pid = project.create(conn, name="青云记-agent", root_path=str(tmp_path)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)

    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    ledger.declare_node(NodeLabel.CHARACTER, "顾清音")
    ledger.declare_node(NodeLabel.LOCATION, "北荒")
    # 毒药挂在这个**未来实体**上：泄漏网量的是「作者写在节点 props 上的自由文本
    # 会不会穿过序列化」，跟它是哪一类节点无关（2026-08-24 之前它挂在秘密节点上）。
    # **两种毒挂同一个节点**：`upsert_node` 的幂等键是 (project, label, name)，
    # 分两次写会让后一次盖掉前一次。
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.LOCATION,
            name="幽泉窟",
            props=NodeProps.model_validate(
                {"first_appears_chapter": 200, "twist": TWIST, "plot_note": PLOT_NOTE}
            ),
        )
    )
    store.put_chapter(
        ChapterSpec(
            project_id=pid,
            number=CHAPTER,
            heading="第七章 藏书阁",
            path=f"chapters/{CHAPTER:04d}.md",
            text=CHAPTER_TEXT,
        )
    )
    conn.commit()

    ledger.declare_where(who="萧决", loc="北荒", quote=WHERE_QUOTE)
    conn.commit()
    return World(conn=conn, project_id=pid, store=store, root=tmp_path)


def _call(name: str, **arguments: Any) -> ToolCall:
    """模型发来的那个请求：`arguments` 是**字符串**，运输层没解析过它。"""
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


DRAFT_ID = "draft:01JTESTTESTTESTTESTTESTTEST"


class FakeDesk:
    """注入进来的那个起草台（`agent.ports.DraftDesk`）：**生成 / 落盘 / 读回**。

    三个动作各自都是一个模型看得见的面，所以这个假实现三个都要填满——
    只填一个的话，另外两块屏幕在这张网里从来没被扫过（ADR 0022 之后它们是新长出来的）。
    """

    def __init__(self) -> None:
        self.seen: list[DraftContext] = []

    def _candidate(self, chapter: int) -> DraftCandidate:
        return DraftCandidate(
            id=DRAFT_ID,
            chapter=chapter,
            ordinal=1,
            units=12,
            note="这一版更冷，删掉了那段回忆。",
            preview=f"（第 {chapter} 章草稿）风雪落在肩上。",
            created_at="2026-08-12T00:00:00.000Z",
        )

    def write(self, ask: DraftAsk, ctx: DraftContext, **kwargs: Any) -> DraftProduct:
        # 起草侧收到的这份约束**就是要进 prompt 的那一份**——第 4 个面在这里被捉住。
        self.seen.append(ctx)
        return DraftProduct(candidate=self._candidate(ask.chapter))

    def land(self, candidate_id: str) -> LandingReport:
        # `note` 也是模型看得见的一个面（落盘回执贴回对话里），所以它必须填上。
        return LandingReport(
            chapter=CHAPTER, landed=True, note=f"已经写进第 {CHAPTER} 章了（章标题保持原样）。"
        )

    def recall(self, candidate_id: str) -> StoredDraft:
        return StoredDraft(
            **self._candidate(CHAPTER).model_dump(),
            body="（第 7 章草稿）风雪落在肩上。",
        )


# ══════════════════════════════════════════════════════════════════════════
# 网本身
# ══════════════════════════════════════════════════════════════════════════


def leaks(surface: str, blob: str) -> list[str]:
    """`blob` 里有没有秘密内容 / 有没有 `props`。返回人读得懂的违规描述，空 = 干净。

    判据是**子串命中**而不是「解出来看看有没有那个字段」：泄漏的形态不止一种
    （props 整份序列化、拼进一句错误说明、被某个 docstring 抄进 schema 的 description），
    而它们的共同点只有一个——那几个字出现在了模型看得见的地方。

    `'"props"'` 单独算一条：它拦的是「今天那个节点上恰好没写 twist，所以子串检查过了」。
    出参里只要出现 `props` 这个键，下一本书的作者往那儿写一句话就会漏，**而那时没有测试会红**。
    """
    out = [f"{surface} 里出现了{what}" for what, text in SECRET_CONTENT.items() if text in blob]
    if '"props"' in blob:
        out.append(f'{surface} 里出现了 "props" —— 出参只许有 NodeRef 的三个字段')
    return out


def _surfaces_of(world: World) -> dict[str, str]:
    """模型看得见的**四个面**，全部拿到手里。见模块 docstring。"""
    desk = FakeDesk()

    from novel_harness.draft.rolling_summary import SummaryStore

    context = world.context(
        drafter=desk,
        summaries=SummaryStore(world.conn),
        # **索引层要在「知道作者写到第几章」的状态下被采样**：那是它会去算未来实体、
        # 会往返回里写「这是你还没写到的」的那一档，也就是最容易把秘密带出来的那一档。
        working_chapter=CHAPTER,
    )
    outcomes = dispatch_all(
        [
            _call("scene_constraints", chapter=CHAPTER),
            _call("character_state", chapter=CHAPTER, character="萧决"),
            _call("draft_chapter", chapter=CHAPTER, brief="写一场雪，收在他没抬头。"),
            # ADR 0022 拆出来的另外两个动作：它们各自是一个新的返回面。
            _call("save_draft", draft_id=DRAFT_ID),
            _call("read_draft", draft_id=DRAFT_ID),
            # ADR 0024 的问作者：出参会被界面直接摆成一张卡，**它也是一个面**。
            # 这里问的是一句干净的话——它测的不是「模型会不会说破」（那是语义判断，
            # 引擎判不了），是**引擎会不会往问句里加料**（handler 里一个 context 都没有）。
            _call(
                "ask_author",
                question="这一场你想让萧决知道那件事吗？",
                options=["让他知道", "先瞒着他"],
            ),
            # ADR 0023 的记规矩：出参会原样带回作者那句话，**它也是一个面**。
            # 这里记的是一句干净的偏好——测的不是「模型会不会把秘密写进规矩里」
            # （语义判断，引擎判不了），是**引擎会不会往里加料**（handler 里只有那一句）。
            _call("remember_rule", rule="这一章别写打斗", until="这一章写完为止"),
            # 书内索引的四层，一层都不能漏：它们新开了四个模型看得见的面。
            _call("book_index"),
            _call("character_chapters", characters=["萧决", "顾清音"]),
            _call("chapter_summaries", first_chapter=1, last_chapter=CHAPTER),
            _call("chapter_text", chapter=CHAPTER),
            # 失败那条路也要罩住：一句好心的解释同样进对话。
            _call("character_state", chapter=CHAPTER, character="血脉秘密"),
            _call("character_state", chapter=CHAPTER, character="幽泉窟"),
            _call("character_chapters", characters=["血脉秘密"]),
            _call("chapter_text", chapter=CHAPTER + 1),
            # 「这不是一条秘密」那句拒绝里会点那个节点的名 —— 它同样是一个面。
        ],
        context,
    )
    assert [o.ok for o in outcomes] == [True] * 11 + [False] * 4
    assert desk.seen, "起草工具没把约束交给起草侧 —— 第 4 个面没被采到，这条测试是空的"

    surfaces = {f"{o.name} 的返回（ok={o.ok}）": o.content for o in outcomes}
    surfaces["发给模型的工具声明"] = json.dumps(tool_declarations(), ensure_ascii=False)
    surfaces["交给起草侧的约束（进 prompt 的那一份）"] = desk.seen[0].model_dump_json()
    return surfaces


# ══════════════════════════════════════════════════════════════════════════
# 头条
# ══════════════════════════════════════════════════════════════════════════


def test_no_tool_surface_ever_carries_secret_content(world: World) -> None:
    """**这一条是本文件存在的理由。**

    工具一旦把秘密交出去过，改代码删不掉它——它已经在作者的持久化对话里，
    之后每一轮起草都带着它（ADR 0019 边界一 / 修复成本那一节）。
    """
    offenders = [
        line for surface, blob in _surfaces_of(world).items() for line in leaks(surface, blob)
    ]
    assert not offenders, (
        "模式二的工具把秘密交出去了：\n  " + "\n  ".join(offenders) + "\n"
        "`NodeProps` 是 extra=\"allow\"，作者写在秘密节点上的 twist 和写在未来地点上的 "
        "plot_note 会原样穿过任何一次 model_dump_json()。出参只许有 NodeRef（id/label/name）"
        "和纯量——**永不 Node**（ADR 0019 边界一）。\n"
        "而这是不可回收的：对话是持久化的，交出去过的那段话改代码删不掉。"
    )


def test_no_tool_schema_still_talks_about_secrets() -> None:
    """秘密 2026-08-24 就整个删了（ADR 0039），可 schema 里还在说这套话就是**每一轮都在
    骗模型**——不是过期注释。人设里那条「0 不等于没有」的规矩会让模型把「秘密相关字段是
    空的」读成「有秘密，只是我没查到」，然后多绕几轮或者写得畏手畏脚。

    判据是**机械**的，不是肉眼分类：把每个工具真正发出去的声明（name + description +
    parameters，`tool_declarations()` 的原样输出）序列化成一整块 JSON 再 grep。

    2026-08-27 这条曾经漏过一次：`BookIndexArgs.labels` 的 `Field(description=...)`
    里还留着 `Secret`，肉眼按「docstring 还是 description=」分类时把它当成了给开发者看的
    文档。**入参模型的类 docstring 和每个 `Field(description=...)` 都会原样进
    `model_json_schema()`**——那份表看起来像注释，实际是发给模型的 schema。出参模型
    （`RosterEntry` 一类）的 docstring 才真的不进（`tool_declarations()` 只序列化
    `spec.args`，从不序列化返回类型）。
    """
    for declaration in tool_declarations():
        blob = json.dumps(declaration, ensure_ascii=False)
        for needle in ("秘密", "Secret"):
            assert needle not in blob, (
                f"{declaration['function']['name']!r} 的 schema 里还有 {needle!r}，"
                f"而秘密整套已经下线：\n{blob}"
            )


def test_an_english_books_tool_declarations_have_no_chinese_characters() -> None:
    """国际化第三批：表里每一个工具发给英文书的 schema 里一个中文字都不许有。

    判据和上面那条秘密守卫同一个精神，只是换了一把尺——**逐条扫表里全部声明**
    （name + description + 递归到底的 parameters），而不是挑几个关键词。
    `translate_tool_declarations()` 缺一条翻译时会直接 `KeyError`，所以这条测试
    真正钉的是「翻完之后还剩下的那句中文」，不是「漏翻了会不会红」——后者已经由
    `KeyError` 保证了。
    """
    import re

    from novel_harness.draft.length import DraftLanguage

    cjk = re.compile("[一-鿿　-〿＀-￯]")

    for declaration in tool_declarations(DraftLanguage.EN):
        blob = json.dumps(declaration, ensure_ascii=False)
        assert cjk.search(blob) is None, (
            f"{declaration['function']['name']!r} 的英文 schema 里还有中文字符：\n{blob}"
        )


def test_the_english_persona_and_rule_templates_have_no_chinese_characters() -> None:
    """人设和规矩前缀是国际化第三批的另外两块，`tool_declarations()` 扫不到它们——
    它们走的是 `AGENT_SYSTEM_PROMPT_EN`/`RULE_PROMPT_PREFIX_EN`/`RULE_PROMPT_UNTIL_EN`
    这条完全独立的路径（`start_conversation`/`prompt_text` 各自的 `language` 分支），
    所以单独钉一条。
    """
    import re

    from novel_harness.agent.loop import AGENT_SYSTEM_PROMPT_EN
    from novel_harness.agent.rules import RULE_PROMPT_PREFIX_EN, RULE_PROMPT_UNTIL_EN

    cjk = re.compile("[一-鿿　-〿＀-￯]")
    for text in (AGENT_SYSTEM_PROMPT_EN, RULE_PROMPT_PREFIX_EN, RULE_PROMPT_UNTIL_EN):
        assert cjk.search(text) is None, text


def test_the_chinese_tool_declarations_are_unchanged_by_the_translation_layer() -> None:
    """国际化第三批最重要的那条回归：中文书拿到的声明必须和不传 `language` 时逐字节相同。

    `translate_tool_declarations()` 对 ZH 是恒等变换（见它自己的 docstring），
    这条测试量的正是这句话——`TOOL_TABLE`/那些 Pydantic 类一个字都没有为了
    双语化而改动，中文路径不需要，也没有，多付出任何东西。
    """
    from novel_harness.draft.length import DraftLanguage

    assert tool_declarations(DraftLanguage.ZH) == tool_declarations()


def test_every_rejection_message_has_both_languages_and_the_english_side_is_clean() -> None:
    """国际化第三批下半场：`ToolRefused`/`SealRefused`/`DraftRefused` 的运行时拒绝消息，
    和上面三条 schema/人设的守卫是同一个纪律，换到 `_MESSAGES` 这张表上。

    `message()` 缺一侧翻译时会 `KeyError`——那条覆盖率已经由它自己的实现保证；这条测试
    钉的是**存在的两侧都不许有另一半的病**：EN 模板里一个中文字都不许有，ZH/EN 都不许
    是空串（空串会静默通过 `KeyError` 检查但产出一句空消息，等于没拒绝清楚）。
    """
    import re

    from novel_harness.agent.prompt_terms import _MESSAGES
    from novel_harness.draft.length import DraftLanguage

    cjk = re.compile("[一-鿿　-〿＀-￯]")

    for key, by_language in _MESSAGES.items():
        for language in (DraftLanguage.ZH, DraftLanguage.EN):
            template = by_language.get(language)
            assert template, f"_MESSAGES[{key!r}] 缺 {language!r} 这一侧，或者是空串"
        english = by_language[DraftLanguage.EN]
        assert cjk.search(english) is None, f"_MESSAGES[{key!r}] 的英文模板里还有中文字符：{english!r}"


def test_the_constraints_tool_still_reports_cast(world: World) -> None:
    """**2026-08-31 改名**：原来还带一段「收窄过头」的反向断言（`forbidden_entities`
    不能只剩一串 ULID），那个字段随 `forbidden_entities` 一起删了（ADR 0041）。
    这个工具今天只剩「在场」这一件事，这条测试改成钉住这一件事。
    """
    outcome = dispatch(_call("scene_constraints", chapter=CHAPTER), world.context())
    assert outcome.ok, outcome.content
    result = ConstraintsResult.model_validate_json(outcome.content)

    assert set(result.cast) == {"萧决", "顾清音"}, "在场是从正文数出来的（ADR 0018）"
    assert result.cast_derived is True


def test_the_declaration_never_learns_the_secret(world: World) -> None:
    """工具声明是**静态**的，它连 store 都不碰——所以它天然不含数据。

    钉住这一条是因为反过来的写法很自然：「把本书的秘密清单塞进 description，模型就
    一眼知道该避开什么」。那正是把秘密钉进稳定前缀（边界六），一次写错永久缓存。
    """
    blob = json.dumps(tool_declarations(), ensure_ascii=False)
    assert "血脉秘密" not in blob and "幽泉窟" not in blob, (
        "工具声明里出现了这本书的数据。声明按边界六是能进稳定前缀的东西之一，"
        "它必须**跨章不变**；一旦装了逐章变的东西，缓存会把一条过期的约束钉死在 context 里。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 边界二：没有一个工具收约束
# ══════════════════════════════════════════════════════════════════════════

CONSTRAINT_SHAPED_FIELDS = frozenset(
    {"must_not_reveal", "forbidden_entities", "constraints", "secrets", "knows", "matrix"}
)
"""入参里出现任意一个 = 模型可以把**上一章的**约束递回来（ADR 0019 边界二）。

`ch40 的 must_not_reveal ⊇ ch90 的`——对话跨章累积，陈旧的那份更短，
而更短的方向正是 fail-open 的最坏那侧：模型以为「只有这两条不能说」。
"""


def test_no_tool_accepts_constraints_as_an_argument() -> None:
    """边界二在工具表上的落点：**模型没有机会把约束当参数传进来。**"""
    offenders = {
        spec.name: sorted(set(spec.args.model_fields) & CONSTRAINT_SHAPED_FIELDS)
        for spec in TOOL_TABLE
        if set(spec.args.model_fields) & CONSTRAINT_SHAPED_FIELDS
    }
    assert not offenders, (
        f"工具入参里出现了约束字段：{offenders}\n"
        "约束逐章算，对话跨章累积（一个会话能隔三个月回来）。让模型传约束 = 让它拿第 90 章的"
        "清单去写第 40 章，而那份清单更短——fail-open 的最坏那侧（ADR 0019 边界二）。\n"
        "约束必须由后端当场从 scene_view(chapter) 算。"
    )
    # ADR 0047：助手能给写手的只有「要写什么」和「补的资料」两格，**约束字段永远没有**。
    assert set(DraftAsk.model_fields) == {"chapter", "brief", "materials"}


def test_a_model_invented_constraint_argument_is_refused(world: World) -> None:
    """模型自作主张多传一个 `must_not_reveal`，`extra=\"forbid\"` 当场拒。

    静默忽略比拒绝更坏：模型会以为自己已经把约束交代清楚了，然后在后续推理里依赖它。
    """
    call = ToolCall(
        id="c1",
        name="draft_chapter",
        arguments=json.dumps(
            {"chapter": CHAPTER, "goal": "写一场雪", "must_not_reveal": ["血脉秘密"]}
        ),
    )
    outcome = dispatch(call, world.context())
    assert outcome.ok is False
    assert "must_not_reveal" in outcome.content and "参数不合法" in outcome.content


def test_the_backend_computes_the_constraints_for_the_drafter(world: World) -> None:
    """起草侧收到的约束是**后端按章号现算的**，不是模型给的。"""
    desk = FakeDesk()

    outcome = dispatch(
        _call("draft_chapter", chapter=CHAPTER, brief="写一场雪"),
        world.context(drafter=desk),
    )
    assert outcome.ok, outcome.content
    ctx = desk.seen[0]
    assert isinstance(ctx, ResolvedConstraints)
    assert ctx.chapter == CHAPTER


# ══════════════════════════════════════════════════════════════════════════
# 约束 10：入参里唯一的时间坐标是 AS OF
# ══════════════════════════════════════════════════════════════════════════


QUERY_COORDINATES = frozenset({"chapter", "from_chapter", "first_chapter", "last_chapter"})
"""工具入参里**允许**出现的章号字段。全部是查询坐标（AS OF / 区间），一个都不写进数据。

`valid_from` / `valid_to` / `since` 一个都不在这里，而且**这个集合本身是断言**：
往里加名字之前先回答「作者会不会以为他在告诉系统这条事实从第几章开始成立」。
索引层的 `from_chapter` / `first_chapter` / `last_chapter` 是「列表从哪儿开始 / 拉哪一段」，
与面板那条 `chapter` 参数同性质。
"""


def test_the_only_time_coordinates_in_any_tool_are_query_coordinates() -> None:
    """作者永远不填 `valid_from`（ADR 0006 / §5.9）。

    判据借 `test_no_chapter_input.BANNED` 那份既有正则（`chapter|valid_from|valid_to|since|
    ^ch$|^at$`），**但结论不同**：那份守卫盯的是**声明面**，那里一个章号输入框都不许有；
    这里是**查询面**，章号坐标合法（同面板那条 `chapter` 参数），不合法的是别的那几个。

    这条在工具表上今天之所以成立，还有一层结构性理由：**表里一条写图谱的工具都没有**，
    `ToolContext` 里也没有 `CanonWriter`——没有写路径就没有地方能把这个数存成一条边的
    `valid_from`。加写工具的那天，这条断言不再够用。

    索引层落地时这条从「必须恰好是 `chapter`」放宽成「必须落在 `QUERY_COORDINATES` 里」。
    **放宽的是形状，不是判据**：原来那句话同时管着两件事（「不许有 valid_from」和
    「每个工具都得有个 chapter」），而后者对 `character_chapters` 根本不成立——它问的是
    「这个人出现在哪些章」，章号是**出参**。把两件事分开写，第二件由下面那条单独钉。
    """
    from test_no_chapter_input import model_chapter_fields

    for spec in TOOL_TABLE:
        hits = set(model_chapter_fields(spec.args))
        assert hits <= QUERY_COORDINATES, (
            f"工具 {spec.name} 的入参里出现了 {sorted(hits - QUERY_COORDINATES)}，"
            f"只许有查询坐标（{sorted(QUERY_COORDINATES)}）。\n"
            "作者不记得第几章（200 万字写了三年），填错的产物是一条 valid_from 错了的 "
            "CANON 边——它在面板上长得完全正常。"
        )


def test_every_chapter_scoped_tool_still_takes_the_chapter(world: World) -> None:
    """**逐章变的东西，必须由入参的章号决定**（边界二 / 边界五）。

    上面那条放宽之后，「约束和处境是按章算的」就没人钉了。这条补上：三个逐章变的工具
    各自必须收 `chapter`，而不是从会话里那个 `working_chapter` 偷一个默认值——
    偷了就等于按「最近」切上下文，那正是边界五点名的那个「等着发生的跨章泄漏」。
    """
    by_name = {spec.name: spec for spec in TOOL_TABLE}
    for name in ("scene_constraints", "character_state", "draft_chapter"):
        assert "chapter" in by_name[name].args.model_fields, f"{name} 不收章号了"

    # 会话里带着 working_chapter 也不许改变按章号算出来的答案。
    plain = dispatch(_call("scene_constraints", chapter=CHAPTER), world.context())
    with_progress = dispatch(
        _call("scene_constraints", chapter=CHAPTER),
        world.context(working_chapter=CHAPTER + 90),
    )
    assert plain.content == with_progress.content


# ══════════════════════════════════════════════════════════════════════════
# 表本身：谁在里面，谁不在
# ══════════════════════════════════════════════════════════════════════════


def test_the_tool_table_stays_put() -> None:
    """**工具表就是权限边界**，所以它的成员是一条断言，不是一份现状记录。

    加一条之前先回答 ADR 0019 边界一那个问题：它的出参里有没有任何一条路径能走到
    `Node` / `node.props` / PLANNED 边的内容？

    `get_result` 的答案是「没有新路」：它回吐的是**已经**给过、已经躺在 canonical 里
    的工具返回（`_handle_get_result` 拿的是 loop 的只读表），不是新开一条通往图数据的路。
    """
    assert TOOL_NAMES == frozenset(
        {
            "scene_constraints",
            "character_state",
            "draft_chapter",
            # 写前校准那两条（`calibrate_scene` / `seal_scene_brief`，ADR 0033）2026-09-12
            # 随 ADR 0047 砍了：起草改收助手写的 brief + 它挑的 materials。
            # 书内索引四层（`agent/index.py`）：目录 / 人物轴 / 摘要 / 正文。
            "book_index",
            "character_chapters",
            "chapter_summaries",
            "chapter_text",
            # 起草那一摊的另外两个动作（ADR 0022）：生成不落盘了，落盘和读回各自是一条。
            "save_draft",
            "read_draft",
            # 问作者（ADR 0024）：表里第一条**不查东西也不做东西**的工具，
            # 它把一句问话交出去，然后这一轮就结束了。
            "ask_author",
                # 记规矩（ADR 0023 决策二）：**入参里没有章号**，那个数只能是引擎手里的
                # `working_chapter`（约束 10）。它也不写任何东西——贴进 canonical 的是 loop。
                "remember_rule",
                # 按编号取回这一轮被收起的结果（2026-08-15 设计，docs_dev 快照）。
                # **只读、只回吐已有出参**：权限边界和原来的查询是同一条。
                "get_result",
                # 轨道核对（2026-08-23）。它是这张表里**唯一一条自己手里有轨道**的：
                # 出去的只有 `TrackClash` 那三个数（第几句 / 跟第几章 / 冲突类型），
                # 后面那几章写了什么一个字都不出——「工具有权看，agent 没有」。
                "check_track",
                # 右栏那四栏（2026-09-12，`agent/panels.py`）：角色卡 / 事件 / 检验规则 /
                # 通知。**全是读**，读的是面板自己那条读法。边界一那个问题的答案：出参只有
                # NodeRef 和纯量；角色卡那几行字和进写作提示的是同一份收窄视图
                # （`CharacterProfileView`）；关系的值在 `graph/` 那边解读好
                # （`StateSnapshot.relations`），`agent/` 仍然一个 `.props` 都不碰。
                "character_card",
                "chapter_events",
                "validation_rules",
                "notifications",
            }
        )
    by_name = {spec.name: spec for spec in TOOL_TABLE}
    assert len(by_name) == len(TOOL_TABLE), "表里有重名 —— 后一条会静默盖掉前一条"


def test_the_declaration_prefix_is_append_only() -> None:
    """新工具**追加在表尾**，不插在中间（ADR 0019 边界六）。

    `tool_declarations()` 按表序生成，而它是少数几个能进稳定前缀的东西之一。在中间插一条
    会把整段前缀作废——那不是错误，是白付一次全量 token，而且没有任何东西会提示。
    """
    order = [spec.name for spec in TOOL_TABLE]
    assert order[:3] == ["scene_constraints", "character_state", "draft_chapter"]


def test_the_writer_banned_symbols_are_not_tools() -> None:
    """`secret_surfaces`（内容 tell）和 `resolve_cast`（第二份约束推导）不进工具表。

    判据直接取自第 4 道 arch-guard 的那个集合，**不另抄一份**——抄一份就会在那边加成员
    的那天漂掉。
    """
    from test_draft_boundary import WRITER_BANNED

    assert not (TOOL_NAMES & WRITER_BANNED)
    # 名字不同但干的是同一件事也不行：AST 层由下面的 `test_the_tool_layer_never...` 罩。
    assert WRITER_BANNED, "上游集合空了 —— 这条断言会变成永远绿的"


MANUSCRIPT_SHAPED_FIELDS = frozenset({"text", "body", "markdown", "content", "prose", "draft"})
"""工具入参里出现任意一个 = **模型能拿一段自己编的字去盖作者的书**。

ADR 0022 之前这条靠「表里根本没有写工具」成立；`save_draft` 落地之后它靠**入参形状**
成立——那条工具只收一个候选编号，而候选只能由后端按那一章的约束生成出来。
`draft_id` 不在这个集合里正是重点：**指着一稿说「这个」和交出一段文本是两件事。**
"""


def test_no_tool_takes_a_paragraph_of_prose() -> None:
    """**模型没有「只写不草」这个动作**（ADR 0019 边界一的推论，ADR 0022 之后的落点）。

    表里现在有一条会改作者的书的工具（`save_draft`），所以「表里没有写工具」这句话不再
    成立。取代它的是一条更硬、也更可断言的：**没有一个工具收得下一段正文。**
    能写进磁盘的只有刚刚由后端按那一章的约束生成出来的候选。
    """
    offenders = {
        spec.name: sorted(set(spec.args.model_fields) & MANUSCRIPT_SHAPED_FIELDS)
        for spec in TOOL_TABLE
        if set(spec.args.model_fields) & MANUSCRIPT_SHAPED_FIELDS
    }
    assert not offenders, (
        f"工具入参里出现了正文形状的字段：{offenders}\n"
        "那等于给模型一条「拿任意一段字去盖某一章」的路——它可以从对话里抄一段秘密原文"
        "写进书里，而唯一的闸（sha）只管「作者有没有更晚改过」，不管这段字是谁写的。"
    )


def test_there_is_no_tool_that_writes(world: World) -> None:
    """**表里没有一条能改 canon 的工具**——正文那条落盘 2026-08-11 开了（ADR 0021），
    2026-08-12 成了一条工具（ADR 0022 把它从起草的副作用拆成一个动作）。

    边界一的其余三条原样有效，而**「`ToolContext` 上没有写入面」是其中最硬的一条**：
    落盘发生在注入进来的那个起草台里（`agent/drafting.py` 握着 `GraphStore` 和一条连接），
    这个 dataclass 上一个字都没多。

    这条断言有三层，因为「按名字数」拦不住一个叫 `save_scene` 的东西：
    ① 入参形状（上面那条：谁都收不下一段正文）；② `ToolContext` 上没有写入面；
    ③ 那条理由写在源码里。
    """
    context = world.context()
    assert not hasattr(context, "conn")
    assert not hasattr(context, "writer")
    # StoryGraph 的五个方法里唯一能写的是 upsert_edge，而没有一个 handler 碰它。
    # **扫整个 `agent/`，不只是 tools.py**：索引层落地那天工具的实现第一次住在了别的文件里，
    # 只扫一个文件的守卫会在那一刻静默失效（同 test_draft_boundary 自己那条诚实说明）。
    # 落盘那个模块也在扫描范围里：它可以写**磁盘**（ADR 0021），不许写**图**。
    for name, agent_source in _agent_sources():
        assert "upsert_edge" not in agent_source, (
            f"agent/{name} 碰了写入面 —— 模型不许改作者的 canon"
        )
    # 事件那个只读端口同理：`EventStore` 上有三个写方法，收窄的那份不许把它们带回来。
    ports_source = dict(_agent_sources())["ports.py"]
    for writer in ("put_provisional", "clone_to_scope", "update_profile"):
        assert f"def {writer}" not in ports_source, (
            f"`EventIndex` 上长出了 {writer} —— 那是写入面，收窄的意义就没了"
        )
    # `ToolContext` 上的字段名单是**类型层**的那道闸：写入面一旦被塞回来，上面两条
    # 按符号扫的断言仍然是绿的（`save_chapter` 里一个 `upsert_edge` 都没有）。
    fields = set(ToolContext.__dataclass_fields__)
    assert not (fields & {"conn", "writer", "canon", "graph_store"}), (
        f"`ToolContext` 上长出了写入面：{sorted(fields)} —— "
        "ADR 0021 开的是「写磁盘」，不是「把 CanonWriter 交给模型」"
    )
    source = (Path(agent_tools.__file__)).read_text(encoding="utf-8")
    assert "`save_draft` 只收一个候选 id，收不到文本" in source, (
        "模块 docstring 里那段「落盘是一条工具了，但它凭什么仍然安全」不见了。"
        "它不是注释洁癖：下一个人会把「只收 id」读成一个麻烦，然后给它加一个 text 参数。"
    )


def test_declarations_are_generated_from_the_table_not_written_twice() -> None:
    """声明由表生成。第二份手写的 schema 会和表各自演化，而**模型只看得见发出去的那份**。"""
    declarations = tool_declarations()
    assert [d["function"]["name"] for d in declarations] == [s.name for s in TOOL_TABLE]
    for declaration, spec in zip(declarations, TOOL_TABLE, strict=True):
        assert declaration["type"] == "function"
        assert declaration["function"]["description"] == spec.description
        assert declaration["function"]["parameters"] == spec.args.model_json_schema()
        assert declaration["function"]["parameters"]["additionalProperties"] is False


# ══════════════════════════════════════════════════════════════════════════
# 派发：`json.loads` 在编排层，四种失败各有出口
# ══════════════════════════════════════════════════════════════════════════


def test_the_transport_layer_never_parsed_the_arguments() -> None:
    """运输层交回来的 `arguments` 还是字符串（`draft/provider.py` 的契约）。

    这一条钉的是**分工**：运输层一旦 `json.loads`，「解析失败算什么」就在那儿被决定了，
    而那是编排层的判断。同理它不校验工具名——认得工具名就等于有第二份工具表。
    """
    call = ToolCall(id="c", name="scene_constraints", arguments='{"chapter": 4')
    assert isinstance(call.arguments, str)
    assert call.name not in ("",)  # 名字原样带回，没有被过滤


def test_broken_arguments_are_an_answer_not_an_exception(world: World) -> None:
    """流式下 `arguments` 是一串 delta 拼起来的，**截断是真实会发生的事**。

    抛异常会让 agent loop 变成一串 try/except，漏掉一个就整个会话死掉；
    回一条模型读得懂的话，它自己重发一次就好了。
    """
    truncated = ToolCall(id="c1", name="scene_constraints", arguments='{"chapter": 4')
    outcome = dispatch(truncated, world.context())
    assert outcome.ok is False and "JSON" in outcome.content

    not_an_object = ToolCall(id="c2", name="scene_constraints", arguments="[7]")
    assert dispatch(not_an_object, world.context()).ok is False

    missing = ToolCall(id="c3", name="scene_constraints", arguments="{}")
    outcome = dispatch(missing, world.context())
    assert outcome.ok is False and "chapter" in outcome.content


def test_an_unknown_tool_name_is_stopped_here_and_only_here(world: World) -> None:
    """运输层故意不认识工具名，所以这是唯一的拦截点。"""
    outcome = dispatch(ToolCall(id="c", name="read_node", arguments="{}"), world.context())
    assert outcome.ok is False
    assert "read_node" in outcome.content
    for name in sorted(TOOL_NAMES):
        assert name in outcome.content, "拒绝时要把可用的工具列出来，否则模型只能瞎猜"


def test_a_refusal_says_why(world: World) -> None:
    """三种拒绝，三句话。**说不清理由的拒绝会让模型原样重试一遍。**"""
    ambiguous = dispatch(
        _call("character_state", chapter=CHAPTER, character="不存在的人"), world.context()
    )
    assert ambiguous.ok is False and "不存在的人" in ambiguous.content

    not_a_character = dispatch(
        _call("character_state", chapter=CHAPTER, character="北荒"), world.context()
    )
    assert not_a_character.ok is False and "不是人物" in not_a_character.content

    unwired = dispatch(
        _call("draft_chapter", chapter=CHAPTER, brief="写一稿"),
        world.context(drafter=None),
    )
    assert unwired.ok is False and "还没接" in unwired.content


def test_the_error_message_does_not_echo_the_models_input(world: World) -> None:
    """参数校验失败时不回显入参值。

    别处回显是好心（看得见自己填错了什么），这里是一条把任意字符串搬进持久化对话的通路。
    """
    call = ToolCall(
        id="c",
        name="scene_constraints",
        arguments=json.dumps({"chapter": "第七章", "note": TWIST}),
    )
    outcome = dispatch(call, world.context())
    assert outcome.ok is False
    assert not leaks("参数校验的报错", outcome.content)


# ══════════════════════════════════════════════════════════════════════════
# fail-closed：读不到正文时多禁，不是少禁
# ══════════════════════════════════════════════════════════════════════════


def test_without_the_manuscript_the_constraints_degrade_to_everything(world: World) -> None:
    """在场是从正文数出来的（ADR 0018）。数不出来时**退化成全禁**，并且**说自己退化了**。

    方向是有意的：多禁一条的代价是少写一段，漏禁一条的代价是崩人设
    （`panel/constraints.py`：「算不准就多禁」）。
    而 `cast_derived=False` 是约束 8 那条「零必须带着理由一起出现」——
    「一条都不用瞒」和「我没数出这一场有谁」在清单上长得一模一样。
    """
    outcome = dispatch(_call("scene_constraints", chapter=CHAPTER), world.context(root_path=None))
    result = ConstraintsResult.model_validate_json(outcome.content)

    assert result.cast_derived is False and result.cast == []
    assert not leaks("退化路径的返回", outcome.content)


def test_character_state_narrows_the_snapshot(world: World) -> None:
    """`StateSnapshot.node` / `.location` 都是完整的 `Node` —— 这里必须收成 `NodeRef`。"""
    outcome = dispatch(
        _call("character_state", chapter=CHAPTER, character="萧决"), world.context()
    )
    assert outcome.ok, outcome.content
    payload = json.loads(outcome.content)
    assert set(payload["character"]) == {"id", "label", "name"}, "只许有 NodeRef 的三个字段"
    assert payload["character"]["name"] == "萧决"
    assert payload["location"]["name"] == "北荒"
    assert payload["is_dead"] is False and payload["has_appeared"] is True
    assert "props" not in json.dumps(payload)


# ══════════════════════════════════════════════════════════════════════════
# 守卫自己的守卫
# ══════════════════════════════════════════════════════════════════════════
#
# 上面每一条都建立在「`leaks()` 真的看得见泄漏」这个前提上。前提坏掉的那天，
# 它们会安静地全绿——而全绿的那一刻正是秘密进了作者对话的那一刻。


class _LeakyResult(BaseModel):
    """一个**故意泄漏**的出参：它交出完整的 `Node`。

    这不是假想的坏写法，它是**最自然的那一种**：handler 里已经有 `node` 在手上，
    直接返回它比先 `NodeRef.of(node)` 少一行。
    """

    model_config = ConfigDict(frozen=True)

    node: Node


class _CleanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    node: NodeRef


def _the_poisoned_node(context: ToolContext) -> Node:
    """那个 props 上挂着作者自由文本的节点 —— 泄漏探针要交出去的就是它整份。"""
    resolutions = context.store.resolve(context.project_id, ["幽泉窟"])
    return resolutions[0].hits[0].node


def _leaky_handler(args: SceneConstraintsArgs, context: ToolContext) -> _LeakyResult:
    return _LeakyResult(node=_the_poisoned_node(context))


def _clean_handler(args: SceneConstraintsArgs, context: ToolContext) -> _CleanResult:
    return _CleanResult(node=NodeRef.of(_the_poisoned_node(context)))


LEAKY_PROBE = ToolSpec(
    name="_leaky_probe",
    description="故意把整个节点交出去（props 一起）。",
    args=SceneConstraintsArgs,
    handler=_leaky_handler,
)

CLEAN_PROBE = ToolSpec(
    name="_clean_probe",
    description="同一个节点，收窄成 NodeRef。",
    args=SceneConstraintsArgs,
    handler=_clean_handler,
)


def test_the_net_catches_a_leaky_tool(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """喂一个故意泄漏的假工具进**同一条派发路径**，断言网抓得住它。

    走 `dispatch()` 而不是直接调 handler：网罩的是 `ToolOutcome.content`，
    而序列化正是泄漏真正发生的那一步。
    """
    monkeypatch.setitem(agent_tools.TOOLS, LEAKY_PROBE.name, LEAKY_PROBE)
    outcome = dispatch(_call(LEAKY_PROBE.name, chapter=CHAPTER), world.context())
    assert outcome.ok, outcome.content

    assert TWIST in outcome.content, "fixture 自己就没带 twist，这条测试是空的"
    caught = leaks("假工具的返回", outcome.content)
    assert caught, "网看不见一个直接返回 Node 的工具 —— 上面所有断言都是永远绿的"
    assert any("twist" in line for line in caught)
    assert any("props" in line for line in caught)


def test_the_net_does_not_cry_wolf(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """误报会让人把守卫关掉，而关掉的守卫等于没有。

    同一个节点、同一条派发路径，只是收窄成了 `NodeRef` —— 必须是绿的，
    **而且显示名还在**（收窄过头是同一个 bug 的另一面）。
    """
    monkeypatch.setitem(agent_tools.TOOLS, CLEAN_PROBE.name, CLEAN_PROBE)
    outcome = dispatch(_call(CLEAN_PROBE.name, chapter=CHAPTER), world.context())

    assert outcome.ok, outcome.content
    assert leaks("干净工具的返回", outcome.content) == []
    assert "幽泉窟" in outcome.content, "显示名被收掉了 —— 作者看不出这是哪一条约束"


# ══════════════════════════════════════════════════════════════════════════
# AST：起草层那道墙也罩住 `agent/`
# ══════════════════════════════════════════════════════════════════════════
#
# `tests/test_draft_boundary.py` 自己的诚实说明写着：「**它只扫 `draft/` 和 `eval/`
# 两个目录**……runner 若写在别处，这堵墙就绕过去了」。`agent/` 现在也在拼要进 prompt 的
# 东西（它把约束交给起草侧），所以它是同一堵墙该罩的一面。
#
# 这里**复用那份扫描器**而不是抄一份：抄一份会在 `WRITER_BANNED` 加成员的那天漂掉。
# 那边的 `WRITER_DIRS` 是冻结断言（`test_the_banned_sets_stay_put`），不在这里动它。


def _agent_sources() -> list[tuple[str, str]]:
    src = Path(agent_tools.__file__).parent
    return [(str(p.name), p.read_text(encoding="utf-8")) for p in sorted(src.glob("*.py"))]


def test_the_tool_layer_never_touches_a_tell_or_node_props() -> None:
    """`agent/` 不许引用 `secret_surfaces` / `resolve_cast`，也不许读 `.props`。

    `.props` 是 tell 和 plot_note 住的地方；两个被禁的符号一个给内容 tell、一个给
    「第二份约束推导」开门。约束集**只有一个入口**：`panel/constraints.py`。

    **`store.resolve(...)` 在这里不禁**（那边的第四条守卫禁它）：`agent/` 合法地要把
    作者说的一个称呼解析成节点，那正是 `resolve` 的用途，而它没有构造第二份禁忌集——
    禁忌集仍然只从 `panel.constraints` 来，由上面 `test_no_tool_surface...` 罩住出口侧。
    """
    from test_draft_boundary import WRITER_BANNED, banned_symbols, props_reads

    offenders: list[str] = []
    for name, source in _agent_sources():
        offenders += [f"agent/{name}:{n}" for n in banned_symbols(source, WRITER_BANNED, name)]
        offenders += [f"agent/{name}:{n}（.props）" for n in props_reads(source, name)]
    assert not offenders, (
        f"工具层碰了不该碰的东西：{offenders}\n"
        "`.props` 里是作者写的自由文本，直接读它等于绕开出参收窄 —— 而工具的返回值\n"
        "进的是**持久化的对话历史**，泄完删不掉。约束要收算好的，别自己解析。"
    )


def test_that_ast_guard_can_see_the_bypass() -> None:
    """扫描器要是把目录找错了（或者 glob 为空），上面那条会永远绿着通过。"""
    from test_draft_boundary import (
        CAST_PROBE,
        PROPS_PROBE,
        WRITER_BANNED,
        banned_symbols,
        props_reads,
    )

    assert _agent_sources(), "agent/ 一个 .py 都没扫到 —— 上面那条守卫在扫一个空集合"
    assert {name for name, _ in _agent_sources()} >= {"tools.py"}
    assert banned_symbols(CAST_PROBE, WRITER_BANNED), "扫描器看不见 resolve_cast 被拿走"
    assert props_reads(PROPS_PROBE), "扫描器看不见 .props 被读"


# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
# 歧义称呼：**把所有候选都算在场，只加不减**（M2-c，2026-08-22）
# ══════════════════════════════════════════════════════════════════════════


AMBIGUOUS_CHAPTER = CHAPTER + 1
"""「顾清音」+ 一个歧义称呼同处一章。**顾清音知道那条秘密，师兄的另一个候选不知道。**"""


def _with_an_ambiguous_title(world: World, text: str) -> World:
    """给这本书加一个「师兄」→ 2 个人的称呼，并写下一章正文。"""
    ledger = Ledger(world.store, world.conn, world.project_id)
    ledger.declare_alias(of="萧决", surface="师兄", kind=AliasKind.TITLE)
    ledger.declare_alias(of="顾清音", surface="师兄", kind=AliasKind.TITLE)
    world.conn.commit()
    (world.root / "chapters" / f"{AMBIGUOUS_CHAPTER:04d}.md").write_text(text, encoding="utf-8")
    return world


def _derived_at(world: World, chapter: int) -> bool:
    """这一章的在场是**数出来的**（True）还是退化值（False）。

    （2026-08-24 之前这个辅助还回一份禁写清单——秘密下线之后没有那份清单了，
    留下的这一位仍然是那几条守卫真正断言的东西。）
    """
    outcome = dispatch(_call("scene_constraints", chapter=chapter), world.context())
    assert outcome.ok, outcome.content
    return ConstraintsResult.model_validate_json(outcome.content).cast_derived


def test_an_ambiguous_title_no_longer_flattens_the_chapter_to_ban_everything(
    world: World,
) -> None:
    """「师兄」不再把整章打成全书全禁，而是**把 2 个候选都算在场**。

    退化态（`cast_derived=False`）和算出来的清单在出参上长得不一样，所以这条断言
    的是**类型那一位**，不是「清单里有几条」——这本书只有一条秘密，光看清单两者相同。
    """
    _with_an_ambiguous_title(world, "师兄站在门口，一言不发。\n")
    derived = _derived_at(world, AMBIGUOUS_CHAPTER)
    assert derived is True, (
        "歧义称呼把这一章打回了退化态（全书全禁）—— 安全，但安全得没用："
        "AI 拿着一份「什么都别碰」的清单写不出能用的东西"
    )


def test_an_ambiguous_title_widens_the_cast_instead_of_dropping_it(world: World) -> None:
    """歧义称呼把**所有候选**都算进在场，不是把那一个人丢掉。

    ⚠️ **这条测试今天只记录行为，不再证明它是安全的**（2026-08-24，ADR 0039）。

    它原来叫 `test_more_candidates_only_ever_means_more_bans_never_fewer`，钉的是
    **方向**：候选多 ⇒ 禁令只多不少 ⇒ fail-closed。那是 ADR 0018 正确性证明唯一的
    数学理由，也是 ADR 0037 那条裁定站着的地基。

    **秘密下线之后禁令没了，那条论证跟着塌了。** 今天多算一个人的后果不再是「多一批
    禁令」，而是「多一份人物档案进 prompt」——那可能是噪声，不再明显是安全的那一侧。

    **`expand_ambiguous` 因此需要一个新理由，或者被重新裁定。** 在那之前这条测试
    只说清它做了什么，不替它声称那是对的。
    """
    _with_an_ambiguous_title(world, "顾清音看了师兄一眼。\n")

    from novel_harness.mentioned import mentioned_cast
    from novel_harness.text import paragraphs as split_paragraphs

    paras = split_paragraphs("顾清音看了师兄一眼。\n")
    narrow = mentioned_cast(world.store, world.project_id, paras)
    wide = mentioned_cast(world.store, world.project_id, paras, expand_ambiguous=True)
    assert narrow == ["顾清音"]
    assert set(wide) == {"顾清音", "萧决"}, "候选只加不减：「师兄」的两个候选都进来"
    assert set(narrow) <= set(wide), "展开只许加人，不许换掉一份名单"


# ══════════════════════════════════════════════════════════════════════════
# 轨道核对（`check_track`，2026-08-23）：**工具有权看轨道，agent 没有**
# ══════════════════════════════════════════════════════════════════════════


def test_the_track_tool_says_it_is_unwired_instead_of_returning_an_empty_list(
    world: World,
) -> None:
    """没接核对模型时**明说没接线**，不返回一份空清单。

    「没抵触」和「压根没核对」在模型眼里长成同一个空清单，而这两件事的下一步动作
    完全相反（§10 约束 8）。它红了代表那一档退化又变静默了。
    """
    outcome = dispatch(_call("check_track", chapter=CHAPTER), world.context())
    assert outcome.ok is False
    assert "没接线" in outcome.content


def test_the_track_tool_hands_back_three_numbers_and_nothing_else(
    world: World,
) -> None:
    """**出参锁死在三个数上**：第几句 / 跟第几章 / 冲突类型。

    多一个自由文本字段就等于给轨道原文开了一条进出参的路，而那条路一开，
    「写的那个看不见验的那个看见的东西」当场破功（`track.py` 模块头第一节）。
    它红了代表有人给评语加了「理由」那一栏。
    """
    from novel_harness.agent.ports import TrackVerdict

    assert set(TrackVerdict.model_fields) == {"chapter", "clashes", "note"}
    clash = TrackVerdict.model_fields["clashes"]
    assert clash.annotation is not None
    from novel_harness.advisory_review import TrackClash

    assert set(TrackClash.model_fields) == {"sentence", "chapter", "conflict"}


def test_the_track_tool_only_lets_the_model_pick_which_chapter(world: World) -> None:
    """**模型影响不了任何一个实质入参**（ADR 0019 边界二）。

    正文由后端读当前快照，轨道由后端算——递不进来一段自己编的正文，也指不定要跟
    哪几章比。它红了代表入参上长出了第二个格子，那个格子就是模型改判据的入口。
    """
    from novel_harness.agent.tools import CheckTrackArgs

    assert set(CheckTrackArgs.model_fields) == {"chapter"}


# ══════════════════════════════════════════════════════════════════════════
# 数不出人：**不猜**（2026-08-23 撤销 M2-b，见 ADR 0037 补记）——
# 有正文才有 cast，没正文就全禁，**不许从别的章借一份顶上**
# ══════════════════════════════════════════════════════════════════════════


def test_a_blank_chapter_bans_everything_instead_of_borrowing_the_cast_next_door(
    world: World,
) -> None:
    """**这条就是不许「往前借」长回来的那道守卫。**

    上一章明明数得出人（第 7 章有萧决和顾清音），这一章还是一片空白 —— 那这一章
    **没有名单**，不是「未知，从隔壁借一份」。

    撤掉借用不是因为实现有 bug，是判据本身错了：「多算一个人 = 多一批禁令 = 安全」
    只在往**真实名单上加人**时成立，而借用是拿另一章的名单**顶替**一份未知的名单——
    顶替得不到真实名单的超集。`synth/gate.db` 第 11 章实测 20 种两两组合里
    **15 种少禁**，最坏一组（真实在场是李管家、借来的是萧决）禁令从 5 条掉到 2 条：
    李管家不知道那三条，AI 当着他的面写破，三条人设一起崩。

    **断言的是类型那一位（`cast_derived`），不是清单里有几条**——这本书只有一条秘密，
    借来的清单和全禁的清单光看内容一模一样，借用当年就是从这条缝里溜进来的。
    """
    _with_an_ambiguous_title(world, "风雪落了一夜。\n")  # 一个角色册里的人都没提到

    assert _derived_at(world, AMBIGUOUS_CHAPTER) is False, (
        "上一章数得出人，这一章就说自己算准了 —— 那份名单只可能是从隔壁借来的，"
        "而顶替一份未知的名单不是它的超集"
    )

    from novel_harness.agent.tools import _scene_context_from_text
    from novel_harness.draft.context import UnknownCastConstraints

    ctx = _scene_context_from_text(world.context(), AMBIGUOUS_CHAPTER, "风雪落了一夜。\n")
    assert isinstance(ctx, UnknownCastConstraints), (
        "空章拿到了一份算出来的约束 —— 没有正文就数不出在场，那几个人是猜的"
    )


def test_the_constraints_type_has_nowhere_to_record_a_borrowed_cast() -> None:
    """**类型上也不留位置**：cast 只有一个来源 —— 本章正文里数出来的。

    撤销之前这里有一位 `cast_is_borrowed`，下游两处（`_append_execution_plan` 与
    `_with_memory`）靠它把记忆那条通道关上——那是在给一份猜出来的名单打补丁。
    名单不猜了，补丁和它要补的洞一起没了。

    它红了代表那一位又回来了，而它回来只有一个理由：有人又开始借名单。
    """
    from novel_harness.draft.context import ResolvedConstraints

    assert "cast_is_borrowed" not in ResolvedConstraints.model_fields
