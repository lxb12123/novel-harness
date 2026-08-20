"""作者在设置页手填的那个窗口：它必须压过一切，而**没填时一个字节都不许变**。

## 这一位为什么存在

`max_context_tokens` 决定逐字上文给作者 800 字还是上万字（`assemble.product_tail_limit`），
差 50 倍。而**自建端点（本机 / 公司内网 / 私有网关）永远推断不出这个数**——
`draft/windows.py` 边界二写死了「主机名不认识就一个键都不查」，那是有意的：
照云端标价/规格去猜自己机器上那个量化过的小模型，比不猜更坏。

于是那些作者只剩「自己填」这一条路（Cline / Roo Code 同样让用户填）。这份测试钉三件事：

1. **填了就压过一切**：注册表、端点自报、公共快照，一档都不例外；
2. **没填 = 今天的行为**，逐字节相同（这条比第 1 条重要：它是「没人受影响」的全部证据）；
3. **自建端点填完，上文真的变长**——这个数唯一花钱的用途就在这儿。

判据全部用 `product_tail_limit` 落到「上文有多长」上，而不是停在「字段等于几」：
这个数错了的症状是**模型忽然变笨**，屏幕上没有任何一处会红。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from novel_harness import settings as settings_module
from novel_harness.api.deps import resolve_route_capabilities
from novel_harness.draft.assemble import GATE_TAIL_CODE_POINTS, product_tail_limit
from novel_harness.draft.capabilities import (
    CAPABILITY_REGISTRY,
    CapabilityError,
    ReasoningEffort,
    plan_structured_call,
)
from novel_harness.draft.discovery import resolve_with_discovery
from novel_harness.draft.provider import ProviderConfig
from novel_harness.draft.windows import AUTHOR_WINDOW_SOURCE, capabilities_from_author

#: 自建：公共快照**认不出**，今天一律 unknown（上文 800 字）。这条是这一位的全部理由。
HOMEBREW = ("http://localhost:11434/v1", "qwen-plus")
#: 公共快照认得出的一条（不在本仓注册表里）。
SNAPSHOT = ("https://api.deepseek.com", "deepseek-chat")
#: 本仓注册表里查证过的一条。**作者的数连它也压得过**。
REGISTERED = ("https://api.deepseek.com", "deepseek-v4-flash")

#: 一次整章起草的输出预算（同 `test_model_windows.py`，那儿也拿它算上文长度）。
RESERVED_OUTPUT = 7_024


@pytest.fixture(autouse=True)
def _author_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """设置写在临时目录里。**绝不许碰作者真实的 `~/.config`。**"""
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    yield


def _fill(route: tuple[str, str], context_window: int | None) -> None:
    settings_module.save(
        settings_module.Settings(
            base_url=route[0],
            model=route[1],
            api_key="k",
            context_window=context_window,
        )
    )


def _config(route: tuple[str, str]) -> ProviderConfig:
    return ProviderConfig(base_url=route[0], model=route[1], api_key="k", temperature=None)


# ══════════════════════════════════════════════════════════════════════════
# 1. 没填 = 今天的行为，逐字节相同
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("route", [HOMEBREW, SNAPSHOT, REGISTERED])
def test_an_empty_box_changes_nothing_at_all(route: tuple[str, str]) -> None:
    """**这条是「没人受影响」的全部证据**，所以它比「填了会怎样」还要紧。

    三档解析各取一条：认不出的（unknown）、公共快照认得的、本仓注册表登记的。
    """
    _fill(route, None)
    assert resolve_route_capabilities(_config(route)) == resolve_with_discovery(*route)


def test_a_registered_route_still_comes_back_as_the_very_same_object() -> None:
    """登记过的路由**连对象都不该换**——换了就说明中间多了一层拷贝，
    而拷贝是「某一位被悄悄改掉」唯一需要的条件（M2 判分链走的就是这些路由）。"""
    _fill(REGISTERED, None)
    assert resolve_route_capabilities(_config(REGISTERED)) is CAPABILITY_REGISTRY[REGISTERED]


@pytest.mark.parametrize("filled", [0, -1, None])
def test_zero_and_negative_are_not_a_window_they_are_no_answer(filled: int | None) -> None:
    """0 / 负数 = **没填**，不是「填了个 0」。

    0 的窗口会让 `plan_call` 当场抛，而作者清空那个框的意思显然不是「一个字都别记」。
    判据写在 `settings.py` 一处，这儿连着存盘读回一起验（磁盘上那份旧文件同样走它）。
    """
    _fill(HOMEBREW, filled)
    assert settings_module.load().context_window is None
    assert resolve_route_capabilities(_config(HOMEBREW)) == resolve_with_discovery(*HOMEBREW)


# ══════════════════════════════════════════════════════════════════════════
# 2. 填了就压过一切
# ══════════════════════════════════════════════════════════════════════════


def test_the_authors_number_beats_the_public_snapshot() -> None:
    """公共快照（社区维护、可能滞后一代）在作者自己那个数面前一钱不值。"""
    inferred = resolve_with_discovery(*SNAPSHOT)
    assert inferred.source.startswith("snapshot:"), "这条路由本来是快照答的，对照才成立"
    assert inferred.max_context_tokens not in (None, 40_000)

    _fill(SNAPSHOT, 40_000)
    resolved = resolve_route_capabilities(_config(SNAPSHOT))
    assert resolved.max_context_tokens == 40_000
    assert resolved.source == AUTHOR_WINDOW_SOURCE


def test_the_authors_number_beats_our_own_audited_registry() -> None:
    """**连本仓查证过的那张表也压得过。**

    看着激进，实际是唯一说得通的顺序：作者可能在同一个名字后面跑着一个自己部署的、
    量化过的小模型，而注册表登记的是官方那一个。他手上有的信息我们没有。
    """
    registered = CAPABILITY_REGISTRY[REGISTERED]
    assert registered.max_context_tokens == 1_000_000

    _fill(REGISTERED, 262_144)
    resolved = resolve_route_capabilities(_config(REGISTERED))
    assert resolved.max_context_tokens == 262_144
    # 其余每一位都还是注册表那份查证过的（作者知道自己机器吃得下多少，
    # 不代表他知道这个模型的推理方言和预留比例）。
    assert resolved.reasoning_dialect is registered.reasoning_dialect
    assert resolved.reasoning_levels == registered.reasoning_levels
    assert resolved.reserve_ratio_high == registered.reserve_ratio_high
    assert resolved.max_tokens_field == registered.max_tokens_field


def test_only_the_window_and_its_audit_trail_move() -> None:
    """**除了窗口，一位都不许动**——多改一位就是替作者编。

    出处也跟着换：这个数不再是那几份官方文档背书的，背书它的是把端点跑起来的人。
    留着旧出处 = 审计线索指向一份**说着另一个数字**的文档。
    """
    before = resolve_with_discovery(*REGISTERED)
    after = capabilities_from_author(before, 262_144)
    assert after is not None
    changed = {
        key for key, value in after.model_dump().items() if before.model_dump()[key] != value
    }
    assert changed == {"source", "source_urls", "max_context_tokens", "max_output_tokens"}
    assert after.source_urls == (before.base_url,)


def test_a_small_window_drags_the_output_ceiling_down_with_it() -> None:
    """作者填的数比登记的输出上限还小时，**输出上限跟着往下夹**（信小的那个）。

    不夹的话 `ProviderCapabilities` 当场校验失败（不许 `输出 > 窗口`），而作者会看到
    一句「模型没配好」——**他明明配好了，只是填了个小数**。
    """
    registered = CAPABILITY_REGISTRY[REGISTERED]
    assert registered.max_output_tokens == 384_000

    _fill(REGISTERED, 100_000)
    resolved = resolve_route_capabilities(_config(REGISTERED))
    assert resolved.max_output_tokens == 100_000
    assert resolved.max_context_tokens == 100_000


# ══════════════════════════════════════════════════════════════════════════
# 3. 自建端点填完，上文真的变长 —— 这个数唯一花钱的用途
# ══════════════════════════════════════════════════════════════════════════


def test_a_homebrew_endpoint_finally_gets_a_long_tail() -> None:
    """**这条是整件事的收益，其余都是防它出错的网。**

    判据落在 `product_tail_limit` 上而不是字段值上：作者感觉得到的是「它记得住前文」，
    而不是「某个字段等于 131,072」。
    """
    _fill(HOMEBREW, None)
    blind = resolve_route_capabilities(_config(HOMEBREW))
    assert blind.source == "unknown"
    assert product_tail_limit(blind.max_context_tokens, RESERVED_OUTPUT) == GATE_TAIL_CODE_POINTS

    _fill(HOMEBREW, 131_072)
    told = resolve_route_capabilities(_config(HOMEBREW))
    assert told.max_context_tokens == 131_072
    tail = product_tail_limit(told.max_context_tokens, RESERVED_OUTPUT)
    assert tail > GATE_TAIL_CODE_POINTS * 10, "从 800 字到上万字，这才是作者填它的理由"


def test_the_number_is_believed_verbatim_and_never_rounded_to_a_famous_one() -> None:
    """填多少就是多少。**不许往最近的「常见窗口」上靠**——那正是本仓在公共表那一层
    拒绝模糊匹配的同一条理由：替他猜一个更大的数，症状是请求被拒。"""
    _fill(HOMEBREW, 12_345)
    assert resolve_route_capabilities(_config(HOMEBREW)).max_context_tokens == 12_345


# ══════════════════════════════════════════════════════════════════════════
# 4. 它顺带做了什么（有意的，不是副作用）
# ══════════════════════════════════════════════════════════════════════════


def test_filling_it_in_also_stops_this_route_from_being_unknown() -> None:
    """⚠️ **填了之后这条路由在能力表眼里不再是「未知」的。**

    这不是设计选择，是模型不变式逼出来的：带着窗口的 `unknown` 在
    `ProviderCapabilities._is_coherent` 那儿根本构造不出来。

    可见的后果只有一个：`plan_structured_call` 那道「未知就拒」的闸对这条路由不再拦，
    也就是抽取和滚动总结在自建端点上从「一律 422」变成「真的发出去试试」。
    方向是对的（那两条链本来就该在自建端点上能用），且失败当场可见（解不开的 JSON 会报错，
    不是悄悄写坏）。**钉在这儿是为了让它是一条有意的行为，而不是哪天被人当 bug 修掉。**
    """
    _fill(HOMEBREW, None)
    blind = resolve_route_capabilities(_config(HOMEBREW))
    with pytest.raises(CapabilityError, match="unknown capability"):
        plan_structured_call(1_000, ReasoningEffort.OFF, blind)

    _fill(HOMEBREW, 131_072)
    told = resolve_route_capabilities(_config(HOMEBREW))
    plan = plan_structured_call(1_000, ReasoningEffort.OFF, told)
    assert plan.capability.max_context_tokens == 131_072


def test_the_authors_box_can_never_reach_the_exam() -> None:
    """**卷子不许被作者的设置改一个字节**（协议 §2：wire 变了就是改卷子）。

    今天这条成立是结构上的：gate 走 `ProviderConfig.from_env()` +
    裸的 `resolve_capabilities`，压根不读设置文件。钉在这儿是因为**它靠的是
    「那一档只加在壳里」这一个事实**——哪天有人图省事把手填那一档下沉到
    `resolve_capabilities` 或 `resolve_with_discovery` 里，三臂的 prompt 预算会跟着变，
    而那时这条会红。
    """
    from novel_harness.draft.capabilities import resolve_capabilities

    _fill(REGISTERED, 12_345)
    assert resolve_capabilities(*REGISTERED) is CAPABILITY_REGISTRY[REGISTERED]
    assert resolve_with_discovery(*REGISTERED) is CAPABILITY_REGISTRY[REGISTERED]


def test_a_number_that_makes_the_capability_incoherent_is_simply_ignored() -> None:
    """**一个设置框不该有能力把起草弄挂。** 读不成一份自洽的能力 ⇒ 当作没填。"""
    unknown = resolve_with_discovery(*HOMEBREW)
    assert capabilities_from_author(unknown, 0) is None
    assert capabilities_from_author(unknown, None) is None


def test_the_authors_window_reaches_mode_two_drafting_too() -> None:
    """**作者填的那个数必须管到写作助手，不只是起草抽屉。**

    2026-08-13 查出来的洞：`agent/model.agent_call_plan` 自己调 `resolve_with_discovery`，
    而那一层**够不着设置**（`agent/` 不读作者的配置）。于是同一台机器上——

        自建端点 + 作者手填 131,072
          /draft、抽取、总结  → 上文 10,337 字
          写作助手起草        → 上文 800 字     ← 差 13 倍，而且不报错

    他填了一个数，**一半的功能听、一半不听**，而不听的那一半正是他最常用的。

    修法是让装配层把解析好的那一份交下去（`api/chat.py::_plan_or_422`），
    **不是把设置下沉到 `agent/`** —— 那会让引擎层长出一条读作者配置的路径。
    """
    import inspect

    from novel_harness.agent import model as agent_model
    from novel_harness.api import chat as chat_api

    # ① 口子在（`None` 那一档保留给 CLI / 测试：它们没有装配层）
    signature = inspect.signature(agent_model.agent_call_plan)
    assert "capability" in signature.parameters
    assert signature.parameters["capability"].default is None

    # ② 装配层真的用了它 —— **判据是源码里那次调用，不是「有这个参数」**：
    #    加了参数却没人传，正是这条洞原本的样子。
    source = inspect.getsource(chat_api._plan_or_422)
    assert "resolve_route_capabilities(config)" in source, (
        "写作助手又自己去解析能力了 —— 作者手填的窗口会再次只管住一半功能。"
    )
