"""行内续写的记忆那一格（2026-08-22 任务 3 / 3.5）。

在这之前，续写送给模型的**全部内容**约 404 个字符：光标前的一段、几个不许写的名词，
就没了。`draft/product_draft.py` 那一支直接跳过整个记忆层，注释里写的理由是**延迟**
（「停手 400 毫秒就要出结果」），不是正确性——于是库里的滚动总结一条没送。

这一批钉三件事，每一件坏起来的样子都不一样：

1. **总结那一格真的进了 prompt。** 退回「跳过记忆层」就红。
2. **取到缺 / 旧的章 → 待办表里真的多了一单，且这一次的 prompt 里没有那几章。**
   「报了但没做」是 2026-08-22 刚在扫描那条路上修掉的病（判 stale、报「已排覆写」、
   下的单里一项总结都没有）；新入口不许再犯一次。
3. **只接这一格。** 人物档案和已确认事件要查图、要按字数往回数整章，进不了
   400 毫秒——它们进来的那一天，作者感觉到的是「补全变卡了」，而没有任何东西会红。

另外钉一条删除：**「没有总结就用一段原文顶上」那条线彻底没了**。它数据结构声明了、
拼 prompt 那儿也渲染了，可整个仓库没有一处给它赋过值——而一章原文 3,000–5,000 字、
一段总结 120 字，顶替一次就吃掉三十章的额度。正确做法是把总结补上（第 2 条）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_summary_schedule import _seed_summary, _wheel

from novel_harness.chapter_refresh import BRANCH_SUMMARY
from novel_harness.db import connect
from novel_harness.draft.rolling_summary import save_author_summary

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

SHORT = {"language": "zh", "min_units": 80, "target_units": 150, "max_units": 300}
"""续写档：一两段（同 `tests/test_draft_api.py` 里那一份）。"""

TAIL = "夜色沉下来，屋里没有点灯。"

CHAPTERS = 30
"""这本书有多少章。

要够多，「换个更大的窗口 → 多拿几章」那条才有东西可比：这个模型能配的最小窗口
（96,000，再小连输出预算都摆不下）算出来的总结那一格就够装 22 章了，书比它短的话
两次续写拿到的是同一批，测试会在一个恒真的比较上变绿。"""


# ── 脚手架 ────────────────────────────────────────────────────────────────


@pytest.fixture
def wheel(tmp_path: Path) -> dict[str, str]:
    return _wheel(tmp_path, CHAPTERS)


@pytest.fixture
def client(
    wheel: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", wheel["db"])
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    # 环境变量是连接参数的兜底档。跑测试的人 shell 里恰好有一个，窗口就跟着他的模型变，
    # 而下面每一条断言都吃这个窗口算出来的额度。
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _configure(client: TestClient, *, window: int = 128_000) -> None:
    reply = client.put(
        "/api/settings",
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
            "context_window": window,
        },
    )
    assert reply.status_code == 200, reply.text


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, str]]]:
    """把模型换成桩，并**把发出去的 messages 留下来**——这一批断言的对象就是它。"""
    import novel_harness.draft.generate as generate_mod
    from novel_harness.draft.provider import CompletionResult

    observed: list[list[dict[str, str]]] = []

    def fake(messages, *, config=None, plan=None, client=None) -> CompletionResult:
        observed.append(messages)
        # 落在 SHORT 的 80–300 里，免得触发那次「太短了再续一段」的第二次调用——
        # 下面有一条断言数的就是调用次数。
        return CompletionResult(text="正文" * 75, model="fake", finish_reason="stop")

    monkeypatch.setattr(generate_mod, "complete", fake)
    return observed


def _summarize(wheel: dict[str, str], chapter: int, text: str) -> None:
    """给这一章一份**照当前正文写的**总结（`summary_alignment` 判 `paired`）。

    走 `save_author_summary` 而不是自己拼 SQL：它顺手把 `source_snapshot_id` 指到
    当前快照上，正是「配对」的定义。而且它不调模型——这批测试一分钱都不该花。
    """
    conn = connect(wheel["db"])
    try:
        save_author_summary(
            conn, project_id=wheel["pid"], chapter_number=chapter, text=text
        )
    finally:
        conn.close()


def _continue(
    client: TestClient, wheel: dict[str, str], chapter: int
) -> dict[str, object]:
    reply = client.post(
        f"/api/projects/{wheel['pid']}/chapters/{chapter}/draft",
        json={"previous_tail": TAIL, "length": SHORT},
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


def _prompt(observed: list[list[dict[str, str]]]) -> str:
    assert len(observed) == 1, f"这一稿发了 {len(observed)} 次调用，应当只有一次"
    return "\n".join(message["content"] for message in observed[0])


def _summary_orders(wheel: dict[str, str]) -> set[int]:
    """待办表里**带着总结那一项**的单，按章号。

    判据是 `missing_branch_mask & BRANCH_SUMMARY`，不是「有没有 attempt 行」——
    「报了已排、单里没有总结那一项」正是这条判据存在的理由。
    """
    conn = connect(wheel["db"])
    try:
        rows = conn.execute(
            """
            SELECT c.number AS number, a.missing_branch_mask AS mask
              FROM chapter_refresh_attempt a
              JOIN chapter_refresh_run r ON r.id = a.run_id
              JOIN chapter c ON c.id = r.chapter_id
             WHERE c.project_id = ?
            """,
            (wheel["pid"],),
        ).fetchall()
    finally:
        conn.close()
    return {int(row["number"]) for row in rows if int(row["mask"]) & BRANCH_SUMMARY}


# ══════════════════════════════════════════════════════════════════════════
# 一、总结那一格真的进了续写的 prompt
# ══════════════════════════════════════════════════════════════════════════


def test_continuation_now_carries_the_earlier_chapter_summaries(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这条红了就说明那一支又跳过记忆层了**（2026-08-22 之前它就是这样）。"""
    _configure(client)
    _summarize(wheel, 1, "第一章：萧决推开门，屋里没有点灯。")
    _summarize(wheel, 2, "第二章：夜色沉下来，城主府的灯一盏盏亮起。")

    seen = _capture(monkeypatch)
    body = _continue(client, wheel, 3)
    prompt = _prompt(seen)

    assert "【更早章节滚动总结】" in prompt
    assert "第一章：萧决推开门，屋里没有点灯。" in prompt
    assert "第二章：夜色沉下来，城主府的灯一盏盏亮起。" in prompt
    assert body["memory"]["assembled"] is True
    assert body["memory"]["rolling_summaries"] == 2


def test_the_summaries_are_what_made_the_prompt_grow(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一次续写，只多了库里两条总结，prompt 就长出这么多。

    **对照组是有必要的**：只断言「含总结块」的话，一个把整章记忆前言塞进来的实现
    （档案 + 事件 + 总结）也会绿，而那正是这次明确不要的东西。
    """
    _configure(client)
    seen = _capture(monkeypatch)
    _continue(client, wheel, 3)
    bare = len(_prompt(seen))

    _summarize(wheel, 1, "第一章：萧决推开门，屋里没有点灯。")
    _summarize(wheel, 2, "第二章：夜色沉下来，城主府的灯一盏盏亮起。")
    seen.clear()
    _continue(client, wheel, 3)
    fed = len(_prompt(seen))

    assert fed > bare
    assert fed - bare > 40  # 两条总结的字数，不是一两个字符的抖动


def test_the_other_two_memory_slots_stay_out_of_the_400_millisecond_path(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**只接一格。** 人物档案和已确认事件要查图、要按字数往回数整章。

    它们混进来时作者感觉到的是「补全变卡了」，而不是任何一条断言变红——所以这条
    断言必须存在。整章起草那条路照旧装它们（`test_draft_api.py` 那几条钉着）。
    """
    _configure(client)
    _summarize(wheel, 1, "第一章：萧决推开门，屋里没有点灯。")

    seen = _capture(monkeypatch)
    _continue(client, wheel, 3)
    prompt = _prompt(seen)

    assert "【更早章节滚动总结】" in prompt
    assert "【在场人物资料】" not in prompt
    assert "【近八章事件】" not in prompt
    assert "【更早的相关事件】" not in prompt
    assert "已生效的故事记忆" not in prompt


def test_an_empty_slot_leaves_the_prompt_byte_identical(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一条总结都没有时**整块不出现**，而不是渲染一句「- 暂无」。

    续写的整份 prompt 只有几百字，凭空多一块什么都没说的东西既占地方、又把前缀
    缓存的形状改了（ADR 0019 边界六）。这一条同时是 `assemble_continuation` 的
    「空 = 原样返回」那条性质在真链路上的落点。
    """
    from novel_harness.draft.assemble import CONTINUATION_GOAL, assemble
    from novel_harness.draft.context import unknown_cast_constraints
    from novel_harness.draft.length import DraftLanguage, LengthSpec
    from novel_harness.draft.product_assemble import assemble_continuation
    from novel_harness.graph.sqlite_store import SqliteStoryGraph

    conn = connect(wheel["db"])
    try:
        ctx = unknown_cast_constraints(SqliteStoryGraph(conn), wheel["pid"], 3)
    finally:
        conn.close()
    spec = LengthSpec(language=DraftLanguage.ZH, min_units=80, target_units=150, max_units=300)
    args = {"goal": CONTINUATION_GOAL, "length": spec}

    assert assemble_continuation(ctx, (), **args) == assemble(ctx, **args)

    _configure(client)
    seen = _capture(monkeypatch)
    body = _continue(client, wheel, 3)

    assert "暂无" not in _prompt(seen)
    assert body["memory"]["assembled"] is False
    assert body["memory"]["rolling_summaries"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 二、取到缺 / 旧 → 下单（第三个触发源），且这一次不给那几章
# ══════════════════════════════════════════════════════════════════════════


def test_a_chapter_with_no_summary_is_ordered_and_left_out_of_this_prompt(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """模式一的做法：**这一次先不给，同时下一单，下一次就有了。**"""
    _configure(client)
    _summarize(wheel, 2, "第二章：夜色沉下来。")  # 第 1 章故意不给

    assert _summary_orders(wheel) == set(), "还没续写就有单 = 这条测试测的不是新入口"
    seen = _capture(monkeypatch)
    body = _continue(client, wheel, 3)
    prompt = _prompt(seen)

    assert "第二章：夜色沉下来。" in prompt
    assert body["memory"]["rolling_summaries"] == 1
    # 待办表里真的多了一单，而且单里带着总结那一项。
    assert 1 in _summary_orders(wheel)
    assert body["memory"]["unsummarized_chapters"] == [1]


def test_a_stale_summary_is_dropped_and_a_rewrite_is_ordered(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**「旧」和「缺」是两个问题，一个出口**（`summary_alignment`，2026-08-22 任务 B）。

    这一章有一份挂着的 ACTIVE 总结，可它照的是一版已经不存在的正文。只问「有没有」
    的实现会把那段字喂进 prompt——缺是瞎，过期是说错，而模型手里只有那一段字。
    """
    _configure(client)
    _seed_summary(wheel, 1, stale=True)  # 文本是「旧总结」，指向一份旧快照
    _summarize(wheel, 2, "第二章：夜色沉下来。")

    seen = _capture(monkeypatch)
    body = _continue(client, wheel, 3)
    prompt = _prompt(seen)

    assert "旧总结" not in prompt
    assert "第二章：夜色沉下来。" in prompt
    assert 1 in _summary_orders(wheel)
    assert body["memory"]["unsummarized_chapters"] == [1]


def test_ordering_does_not_buy_a_summary_on_the_spot(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**不当场补。** 生成一章总结是一次模型调用、几秒起步；续写的预算是 400 毫秒。

    `_prompt()` 里那句「应当只有一次」就是这条：这一次续写只许发生**一次**模型调用，
    单只是 `chapter_refresh_attempt` 里的一行，真正的生成由后台 dispatcher 领走。
    """
    _configure(client)
    seen = _capture(monkeypatch)
    _continue(client, wheel, 3)

    assert len(seen) == 1
    assert _summary_orders(wheel) == {1, 2}  # 全书这个窗口里缺的那两章都下了单


def test_a_retracted_summary_is_not_ordered_back(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """作者亲手撤掉的那一章**不算缺**：他删一次，系统别买回来一次。

    它照旧不进这一稿（撤回的语义就是「当这一章没有总结」），但**不下单**——
    否则续写这条新入口就成了那笔他没按过的付费调用的新来源。
    """
    from novel_harness.draft.rolling_summary import retract_summary

    _configure(client)
    _summarize(wheel, 1, "第一章：萧决推开门。")
    conn = connect(wheel["db"])
    try:
        retract_summary(conn, project_id=wheel["pid"], chapter_number=1)
    finally:
        conn.close()

    seen = _capture(monkeypatch)
    body = _continue(client, wheel, 2)

    assert "第一章：萧决推开门。" not in _prompt(seen)
    assert body["memory"]["rolling_summaries"] == 0
    assert _summary_orders(wheel) == set()


# ══════════════════════════════════════════════════════════════════════════
# 三、换个更大窗口的模型，自动多拿几章 —— 前端一个数都不用改
# ══════════════════════════════════════════════════════════════════════════


def test_a_bigger_window_buys_more_chapters_of_summary(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收那一条。两次续写之间**唯一变的是作者填的窗口**，请求体逐字节相同。

    写死一个「往回 N 章」的实现在这儿必红：它两次报同一个数。
    """
    _configure(client)
    for number in range(1, CHAPTERS - 1):
        _summarize(wheel, number, f"第 {number} 章：萧决做了些事。")

    _capture(monkeypatch)
    # ── 下界 2026-08-26 从 96,000 降回 64,000 ──────────────────────────────
    #
    # 这儿原来写着「取 96,000 而不是更小：这个模型在 64,000 上连『一稿 + 一次长度续写』
    # 的输出预算都摆不下（`generate._preflight_continuation_context` 直接 422）」。
    # **那句话是 `/draft` 那个 `ReasoningEffort.HIGH` 的产物**——HIGH 要给思考留一块
    # 输出预算，64,000 的窗口就摆不下了。续写改走 `OFF` 之后那块预算回来了，
    # 64,000 实测拿到 24 章（96,000 拿满 28 章 = 天花板，两个窗口报同一个数，
    # 这条验收就测不到东西了）。
    #
    # **所以这个数字不是随手调的**：它是「更大的窗口买到更多」这条性质还量得到的位置。
    _configure(client, window=64_000)
    small = _continue(client, wheel, CHAPTERS - 1)["memory"]["rolling_summaries"]
    _configure(client, window=1_000_000)
    large = _continue(client, wheel, CHAPTERS - 1)["memory"]["rolling_summaries"]

    assert 0 < small < large == CHAPTERS - 2


def test_the_window_is_one_formula_not_a_second_copy() -> None:
    """「预算 ÷ 单章上限」只有一处，而且它跟着预算单调不减。

    上一条验的是端到端，这一条验的是那个换算本身——端到端那条在两个窗口算出同一个
    章数时也可能因为别的原因绿（比如书太短），而这一条不会。
    """
    from novel_harness.draft.product_context import summary_window_chapters
    from novel_harness.draft.summarize import SUMMARY_MAX_CHARS

    assert summary_window_chapters(0) == 0
    assert summary_window_chapters(SUMMARY_MAX_CHARS - 1) == 0
    assert summary_window_chapters(SUMMARY_MAX_CHARS * 77) == 77
    seen = [summary_window_chapters(units) for units in (1_500, 6_000, 9_333, 17_142)]
    assert seen == sorted(seen) and len(set(seen)) == 4, seen


def test_the_trim_is_the_existing_one_and_it_drops_the_oldest(
    client: TestClient, wheel: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """超预算从**最旧**那头砍——`select_rolling_summaries`，起草和续写共用的那一份。

    两条路各写一遍裁剪的下场是「同一本书，续写记得的和起草记得的不是同几章」，
    而那个症状不会让任何东西变红。
    """
    from novel_harness.draft.product_context import select_rolling_summaries
    from novel_harness.draft.rolling_summary import ChapterSummary

    def row(number: int) -> ChapterSummary:
        return ChapterSummary(
            id=f"summary:{number}",
            project_id="p",
            chapter_id=f"chapter:{number}",
            chapter_number=number,
            summary="十个字的一段总结",
            summary_sha256="0" * 64,
            schema_version="v",
            prompt_hash="h",
            created_at="2026-08-22T00:00:00.000Z",
        )

    kept = select_rolling_summaries(
        [row(n) for n in (1, 2, 3)], draft_chapter=9, budget=16
    )
    assert [item.chapter_number for item in kept] == [2, 3]


# ══════════════════════════════════════════════════════════════════════════
# 四、「用一段原文顶上」那条线彻底没了
# ══════════════════════════════════════════════════════════════════════════


def test_the_raw_text_fallback_is_gone_for_good() -> None:
    """这个名字在 `src/` 里**一次都不许再出现**。

    它当初的形状是「数据结构声明了、拼 prompt 那儿也渲染了、整个仓库没有一处给它
    赋过值」——一条只有测试造得出数据的分支。别给它补生产方：一章原文 3,000–5,000
    字、一段总结 120 字，顶替一次就吃掉三十章的额度，而正确做法（把总结补上）
    拿到的是同一种东西、量可控、而且补完永久有效。
    """
    banned = re.compile(r"raw_fallback|RawTextFallback|raw_text_fallback|raw-text fallback")
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if banned.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"「原文顶替总结」那条线又回来了：{offenders}"
