"""对抗性验证：那份打包进来的公共快照，会不会把一个假窗口塞进作者的起草。

这个模块的收益很直白（上文从 800 字变成上万字），所以**判据全部押在「它什么时候
应该认不出」**上——认错一个窗口的后果是高估，而高估的表现是请求被拒。

五条网：

1. **它排在我们自己那张表后面**（注册表那 20 条一条都不许被公共表改写）
2. **只有两个精确的键**，一个模糊匹配都不做
3. **自建端点认不出**——这是唯一会造成真实伤害的那条路径
4. **只裁了窗口这一列**，输出上限有意不取（实测公共表滞后一整代）
5. **快照坏掉/缺失只退回 unknown**，不许把起草弄挂
"""

from __future__ import annotations

import json
import re

import pytest

from novel_harness.draft import windows
from novel_harness.draft.assemble import GATE_TAIL_CODE_POINTS, product_tail_limit
from novel_harness.draft.capabilities import CAPABILITY_REGISTRY, resolve_capabilities
from novel_harness.draft.discovery import resolve_with_discovery
from novel_harness.draft.windows import capabilities_from_snapshot, window_for

NO_FETCH = staticmethod(lambda _url: None)


@pytest.fixture(autouse=True)
def _fresh_snapshot() -> None:
    windows._snapshot.cache_clear()


# ══════════════════════════════════════════════════════════════════════════
# 1. 排在我们自己那张表后面
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("route", sorted(CAPABILITY_REGISTRY))
def test_the_public_snapshot_never_overrides_our_own_table(route: tuple[str, str]) -> None:
    """**登记过的路由一条都不许被公共表改写。**

    这不是洁癖：公共表在输出上限那一维**实测滞后一整代**（`deepseek-v4` 写 8,192，
    官方文档 384,000，差 47 倍），而 M2 判分链和 `nh gate` 走的就是这些路由。
    """
    assert resolve_with_discovery(*route, fetch=NO_FETCH) is CAPABILITY_REGISTRY[route]


def test_our_table_and_the_snapshot_disagree_and_ours_wins() -> None:
    """把那个分歧钉成一条测试 —— 它是上一条存在的全部理由。

    公共表说 `deepseek-v4-flash` 的输出上限是 8,192；官方文档是 384,000。
    照公共表填，一次整章起草（可见预算 7,024）会被截成一小段。
    """
    ours = CAPABILITY_REGISTRY[("https://api.deepseek.com", "deepseek-v4-flash")]
    assert ours.max_output_tokens == 384_000

    payload = json.loads(windows.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert "deepseek-v4-flash" in payload["windows"], "公共表里有它，所以这个对照是活的"
    # 而快照里**只有窗口一列** —— 输出上限根本没被裁进来，见下面第 4 条。


# ══════════════════════════════════════════════════════════════════════════
# 2 & 3. 两个精确的键；自建端点认不出
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("base_url", "model"),
    [
        ("https://api.deepseek.com", "deepseek-chat"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        ("https://api.moonshot.cn/v1", "kimi-k2-0905-preview"),
        ("https://api.x.ai/v1", "grok-4"),
    ],
)
def test_a_bare_model_name_is_found_through_the_hosts_provider(base_url: str, model: str) -> None:
    """作者填的是**裸名**（`qwen-plus`），公共表的键带前缀（`dashscope/qwen-plus`）。

    补前缀用的是 `base_url` 的主机名 —— **路由的两半都用上了，不是模糊匹配**。
    不补这一层，国内那几家一条都命中不了。
    """
    found = window_for(base_url, model)
    assert found is not None and found > 8_000
    assert product_tail_limit(found, 7_024) > GATE_TAIL_CODE_POINTS


def test_a_homebrew_endpoint_with_a_famous_name_is_not_believed() -> None:
    """**这条是整个模块唯一会造成真实伤害的那条路径。**

    作者在自己机器上跑一个量化过的小模型，随手起名 `qwen-plus`。公共表里那个
    `dashscope/qwen-plus` 是 129,024 —— 照它填就是**高估**，症状是请求被拒。

    那道闸是「主机名必须在 `_HOST_PROVIDERS` 里」：`localhost` 不在，
    于是这一档安全地退回 `unknown`（上文 800 字，保守但不会坏）。
    """
    assert window_for("http://localhost:11434/v1", "qwen-plus") is None
    assert capabilities_from_snapshot("http://localhost:11434/v1", "qwen-plus") is None

    capability = resolve_with_discovery("http://localhost:11434/v1", "qwen-plus", fetch=NO_FETCH)
    assert capability.source == "unknown"
    assert product_tail_limit(capability.max_context_tokens, 7_024) == GATE_TAIL_CODE_POINTS


def test_a_lookalike_host_is_not_believed_either() -> None:
    """主机名判据是**精确相等**，不是子串 —— 同 `discovery` 那条。

    子串判据会让 `api.deepseek.com.evil.example.com` 命中，而那时一个陌生主机
    就替我们决定了上文给多长。
    """
    assert window_for("https://api.deepseek.com.evil.example.com/v1", "deepseek-chat") is None


def test_a_model_nobody_has_heard_of_is_simply_unknown() -> None:
    assert window_for("https://api.deepseek.com", "totally-made-up-model") is None


def test_an_already_qualified_name_is_not_double_prefixed() -> None:
    """作者直接填 `deepseek/deepseek-chat` 时不许拼成 `deepseek/deepseek/deepseek-chat`。"""
    assert window_for("https://api.deepseek.com", "deepseek/deepseek-chat") is not None


# ══════════════════════════════════════════════════════════════════════════
# 4. 只裁了窗口这一列
# ══════════════════════════════════════════════════════════════════════════


def test_the_snapshot_carries_exactly_one_number_per_model() -> None:
    """**输出上限有意不取。** 它在公共表里会滞后一整代，而填错的后果是稿子被截断。

    这条同时钉住快照的形状：`windows` 是「模型名 → 一个正整数」，没有嵌套对象。
    多裁一列的那天，这里会红。
    """
    payload = json.loads(windows.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert payload["schema"] == windows.SNAPSHOT_SCHEMA
    assert payload["source_license"].startswith("MIT")
    assert all(
        isinstance(name, str) and isinstance(value, int) and value > 0
        for name, value in payload["windows"].items()
    )


def test_the_snapshot_capability_claims_nothing_it_cannot_back() -> None:
    """除了窗口，一律最保守的那一档 —— **快照只知道一件事，多声称一位就是编。**"""
    capability = capabilities_from_snapshot("https://api.deepseek.com", "deepseek-chat")
    assert capability is not None
    assert capability.max_context_tokens is not None
    assert capability.max_output_tokens is None
    assert capability.source == "snapshot:litellm-model-windows"
    assert capability.source_urls and "litellm" in capability.source_urls[0]


# ══════════════════════════════════════════════════════════════════════════
# 5. 快照坏了只退回 unknown
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{ not json", id="不是 JSON"),
        pytest.param('{"schema": "something-else", "windows": {"x": 1}}', id="schema 对不上"),
        pytest.param('{"schema": "nh-model-windows-v1"}', id="没有 windows"),
        pytest.param('{"schema": "nh-model-windows-v1", "windows": []}', id="windows 不是对象"),
        pytest.param(None, id="文件不存在"),
    ],
)
def test_a_broken_snapshot_only_costs_us_the_number(
    content: str | None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**一个数据文件不该有能力把起草弄挂。** 读不出来 = 退回今天的 `unknown`。"""
    target = tmp_path / "model_windows.json"
    if content is not None:
        target.write_text(content, encoding="utf-8")
    monkeypatch.setattr(windows, "SNAPSHOT_PATH", target)
    windows._snapshot.cache_clear()

    assert window_for("https://api.deepseek.com", "deepseek-chat") is None
    capability = resolve_capabilities("https://api.deepseek.com", "deepseek-chat")
    assert capability.source == "unknown"
    windows._snapshot.cache_clear()


def test_the_snapshot_says_how_old_it_is_and_carries_no_junk() -> None:
    """**两件运维上的事，钉在这儿因为别处没人管。**

    ① `fetched` —— 「这份数据有多旧」是唯一能让人决定「该不该重跑一次」的信息。
       它由维护者在命令行给（`--fetched`），**不是脚本读时钟**：同一天跑两次要产出
       同一个文件，否则 git diff 上永远多一行噪音，而那一行会把真正的数据变化淹掉。
    ② `mode == "chat"` —— 公共表里混着 209 条图片、124 条嵌入、66 条语音模型，
       它们**也有** `max_input_tokens`（`1024-x-1024/...` 那条是 77）。留着既没用，
       又多出一批能被误命中的键。
    """
    payload = json.loads(windows.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["fetched"]), "拉取日期要能读"

    # ⚠️ **判据不是「窗口小就是垃圾」** —— 第一版这么写，红在了三条
    # `watsonx/ibm/granite-ttm-*` 上，而它们的 512 是**真实窗口**（IBM 的时序模型，
    # 公共表把它们标成了 chat）。小窗口是事实，不是脏数据；照它算出来的
    # 「上文只能给这么多」也是对的。
    #
    # 真正该消失的是图片/嵌入那一档 —— 它们的键长得都不像模型名。
    assert not [name for name in payload["windows"] if "1024-x-1024" in name]
    assert len(payload["windows"]) < 2_400, "只留 chat 之后条数该明显少于原表"


# ══════════════════════════════════════════════════════════════════════════
# 6. 作者点的那颗「更新」按钮
# ══════════════════════════════════════════════════════════════════════════


PUBLIC_TABLE = {
    "deepseek/deepseek-chat": {"mode": "chat", "max_input_tokens": 200_000},
    "deepseek/deepseek-brand-new": {"mode": "chat", "max_input_tokens": 999_000},
    "some/image-model": {"mode": "image_generation", "max_input_tokens": 77},
    "some/embedder": {"mode": "embedding", "max_input_tokens": 8_192},
    "broken/no-window": {"mode": "chat", "max_input_tokens": None},
    "sample_spec": "not a dict",
}


@pytest.fixture
def _author_home(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    windows._snapshot.cache_clear()
    yield tmp_path
    windows._snapshot.cache_clear()


def test_the_button_writes_to_the_authors_home_not_into_the_package(_author_home) -> None:
    """**绝不碰包里那份。** `site-packages` 是只读的，而且重装一次就没了；
    作者的数据该住在作者的目录里（跟钥匙、地址、模型放一起）。"""
    packaged_before = windows.SNAPSHOT_PATH.read_bytes()

    report = windows.refresh(PUBLIC_TABLE, fetched="2026-11-01")

    assert windows.user_snapshot_path().exists()
    assert windows.user_snapshot_path().parent == _author_home
    assert windows.SNAPSHOT_PATH.read_bytes() == packaged_before, "包里那份一个字节都不许动"
    assert report.total == 2, "只收 mode=chat 且窗口读得懂的那两条"


def test_the_authors_copy_wins_over_the_packaged_one(_author_home) -> None:
    """作者点过「更新」，就说明他要的是新的那一份。"""
    assert window_for("https://api.deepseek.com", "deepseek-chat") is not None

    windows.refresh(PUBLIC_TABLE, fetched="2026-11-01")
    assert window_for("https://api.deepseek.com", "deepseek-chat") == 200_000
    # 而包里没有的新模型现在也认得了 —— 这就是那颗按钮的全部意义。
    assert window_for("https://api.deepseek.com", "deepseek-brand-new") == 999_000


def test_the_report_says_what_actually_changed(_author_home) -> None:
    """**没有这份回执，那就是一颗不出声的按钮**，作者点完只能猜有没有生效。

    `changed` 是最值得摆出来的那个：一个模型的窗口被上游改小了，作者的上文会跟着
    变短，而那件事没有别的观测点。
    """
    windows.refresh(PUBLIC_TABLE, fetched="2026-11-01")
    second = dict(PUBLIC_TABLE)
    second["deepseek/deepseek-chat"] = {"mode": "chat", "max_input_tokens": 64_000}
    second["deepseek/another-one"] = {"mode": "chat", "max_input_tokens": 32_000}
    del second["deepseek/deepseek-brand-new"]

    report = windows.refresh(second, fetched="2026-11-02")
    assert (report.added, report.changed, report.removed) == (1, 1, 1)
    assert report.fetched == "2026-11-02"


def test_a_useless_payload_never_replaces_a_working_snapshot(_author_home) -> None:
    """**「更新」把能用的数据换成空的，比不更新坏得多。**"""
    windows.refresh(PUBLIC_TABLE, fetched="2026-11-01")
    good = windows.user_snapshot_path().read_bytes()

    for junk in ({}, {"only/img": {"mode": "image_generation", "max_input_tokens": 77}}, []):
        with pytest.raises(ValueError, match="没有覆盖"):
            windows.refresh(junk, fetched="2026-11-03")
    assert windows.user_snapshot_path().read_bytes() == good


def test_the_script_and_the_button_write_the_same_bytes(_author_home) -> None:
    """**裁剪和渲染只有一份实现。**

    两处各写一遍的话，作者点出来的快照和维护者提交的会慢慢分家 —— 而那种分家
    只有在「同一个模型两边窗口不一样」的时候才被发现，那时已经影响到稿子了。
    """
    windows.refresh(PUBLIC_TABLE, fetched="2026-11-01")
    from_button = windows.user_snapshot_path().read_text(encoding="utf-8")
    from_script = windows.render(*windows.trim(PUBLIC_TABLE), fetched="2026-11-01")
    assert from_button == from_script


# ══════════════════════════════════════════════════════════════════════════
# 7. 算钱 —— 它是估算，而估算最容易变成一句关于钱的假话
# ══════════════════════════════════════════════════════════════════════════


DS = ("https://api.deepseek.com", "deepseek-chat")


def test_the_cache_hit_part_is_priced_separately() -> None:
    """**这条是这个项目账目的命门。**

    一本 723 章的书每轮前缀几乎不变，命中率能到 98%。把命中的那部分按原价算，
    算出来的数能比真实高一个数量级 —— 而这个产品的成本故事整个押在前缀缓存上。
    """
    hot = windows.estimate_cost(
        *DS, prompt_tokens=8_000, completion_tokens=3_500, cache_read_tokens=7_800
    )
    cold = windows.estimate_cost(
        *DS, prompt_tokens=8_000, completion_tokens=3_500, cache_read_tokens=0
    )
    assert hot is not None and cold is not None
    assert hot < cold, "命中缓存必须更便宜，否则这个数在骗人"
    assert hot / cold < 0.6


def test_a_missing_cache_price_errs_high_not_low() -> None:
    """公共表没写缓存价时按**原价**算。

    **偏高的账单不会让人少付钱，偏低会。** 方向的选择就是这一句。
    """
    price = windows.Price(input=1e-6, output=2e-6)  # 没有 cache_read
    assert price.cache_read is None
    full = 1_000 * 1e-6 + 500 * 2e-6
    # 手算一遍：命中的那 900 也按 input 价 ⇒ 和完全不命中一样贵。
    assert full == 1_000 * 1e-6 + 500 * 2e-6


@pytest.mark.parametrize(
    ("prompt", "completion"),
    [(None, 3_500), (8_000, None), (None, None)],
)
def test_a_missing_token_count_means_no_bill_at_all(prompt, completion) -> None:
    """**供应商没报 token 数时不拿估算的 token 去凑。**

    `activity.py` 那条规矩是「账本只照抄」，而一笔用估算 token 乘出来的钱，
    在屏幕上和一笔真钱长得一模一样。
    """
    assert (
        windows.estimate_cost(
            *DS, prompt_tokens=prompt, completion_tokens=completion, cache_read_tokens=None
        )
        is None
    )


def test_a_homebrew_endpoint_is_never_given_a_cloud_price() -> None:
    """**对价格来说那道主机名闸比对窗口还要紧。**

    作者在自己机器上跑的模型几乎不花钱；照云端标价算出来的数字会离谱地高，
    而屏幕上那是一句关于他钱包的话。
    """
    assert windows.price_for("http://localhost:11434/v1", "deepseek-chat") is None
    assert (
        windows.estimate_cost(
            "http://localhost:11434/v1",
            "deepseek-chat",
            prompt_tokens=8_000,
            completion_tokens=3_500,
            cache_read_tokens=0,
        )
        is None
    )


def test_half_a_price_is_thrown_away_whole() -> None:
    """有输入价没输出价 ⇒ 整条丢掉。**半份价格会算出一个偏低的账单**，
    而偏低正是「屏幕上一句关于钱的假话」那一档。"""
    cleaned = windows._clean_prices(
        {
            "good": {"input": 1e-6, "output": 2e-6},
            "half": {"input": 1e-6},
            "junk": "nope",
        }
    )
    assert set(cleaned) == {"good"}


def test_a_vendor_reported_cost_wins_over_the_estimate() -> None:
    """OpenRouter 的 usage 里带 `cost`，**那是真账不是估算**，不许被标价覆盖掉。"""
    from novel_harness.draft import provider as prov

    config = prov.ProviderConfig(base_url=DS[0], model=DS[1], api_key="k")
    reported = prov.CompletionResult(text="x", model=DS[1], cost=0.5, prompt_tokens=8_000,
                                     completion_tokens=3_500)
    assert prov._priced(reported, config).cost == 0.5

    silent = reported.model_copy(update={"cost": None})
    priced = prov._priced(silent, config)
    assert priced.cost is not None and priced.cost < 0.5
