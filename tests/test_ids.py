"""ids.py 的 golden 值测试（ADR 0003 / PLAN §5.3 明令：10 个，钉死）。

── 先说清楚这 10 个 golden 值钉的是什么，因为很容易写成一个假测试 ──────────

ULID 含随机段，`new_id()` 的输出**没有**一个可以硬编码的完整值。硬编码不了就换个
东西钉，或者干脆放弃钉——两条都是错的。ADR 0003 要钉的东西其实是：

    **改了生成逻辑，全部历史 ID 对不上号。**

在 ULID 方案下，「对不上号」只可能来自两处，两处都是确定性的、都被下面钉死了：

    1. `{type}` 段：8 类节点的 label 小写形 + 8 类非节点实体
    2. `{project_short}` 段：sha256(project_id)[:8] —— 本文件的 GOLDEN 表

随机段能钉的只有**格式**（26 位 Crockford base32、可被 ULID 解析回来）。所以本文件
分成三块：golden 前缀表 / 格式契约 / 与 graph.NodeLabel 的交叉验证（第 3 块是真正
容易腐烂的一处：有人往 NodeLabel 里加了第 9 类节点，而 EntityType 没跟上）。
"""

from __future__ import annotations

import re
import time

import pytest
from ulid import ULID

from novel_harness.graph import NodeLabel
from novel_harness.ids import (
    ARTIFACT_PREFIX,
    PROJECT_SHORT_LEN,
    EntityType,
    artifact_id,
    belongs_to,
    new_id,
    new_project_id,
    project_short,
)

# ══════════════════════════════════════════════════════════════════════════
# GOLDEN：10 个固定输入 → 硬编码的 `{type}:{project_short}` 前缀
#
# 这张表是 ADR 0003 的那 10 个 golden 值。**任何人改动 project_short() 的算法
# （换哈希 / 换长度 / 加归一化 / 改成截 project_id 的尾巴）立刻红灯。**
# 改红了不要改这张表——先回答「已经写进库的那些 ID 怎么办」。
# ══════════════════════════════════════════════════════════════════════════

GOLDEN: list[tuple[EntityType, str, str]] = [
    (EntityType.CHARACTER, "project:01JZ0000000000000000000000", "character:4956977b"),
    (EntityType.LOCATION, "project:01JZ0000000000000000000001", "location:04c0f0f0"),
    (EntityType.FACTION, "project:01JZ0000000000000000000002", "faction:03c397a8"),
    (EntityType.SECRET, "project:01JZ0000000000000000000003", "secret:73ad463f"),
    (EntityType.FORESHADOW, "project:01JZ0000000000000000000004", "foreshadow:c2c1a751"),
    (EntityType.OBJECT, "project:01JZ0000000000000000000005", "object:0fa50faf"),
    (EntityType.STATE_DIM, "project:01JZ0000000000000000000006", "statedim:f08e12a3"),
    (EntityType.CHAPTER, "project:01JZ0000000000000000000007", "chapter:480c6f6c"),
    # 非 ULID 形状的 project_id：导入器 / 测试 fixture / 未来的迁移都可能塞一个进来。
    # project_short 取哈希而不是截尾巴，就是为了这一行仍然给得出 8 位十六进制。
    (EntityType.EDGE, "proj-legacy-import", "edge:ddd232ca"),
    # 非 ASCII + 内含 ':' 的 project_id：钉死「UTF-8 原始字节，不归一化，不切分」。
    (EntityType.EVIDENCE, "项目:青云", "evidence:b339193b"),
]

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
"""Crockford base32：无 I / L / O / U。"""


@pytest.mark.parametrize(("entity_type", "project_id", "want_prefix"), GOLDEN)
def test_golden_prefix(entity_type: EntityType, project_id: str, want_prefix: str) -> None:
    got = new_id(entity_type, project_id)
    assert got.startswith(want_prefix + ":")
    assert got[: len(want_prefix)] == want_prefix


@pytest.mark.parametrize(("entity_type", "project_id", "want_prefix"), GOLDEN)
def test_golden_short_is_stable(entity_type: EntityType, project_id: str, want_prefix: str) -> None:
    # 直接钉 project_short 本身：上面那条测的是拼装，这条测的是那个算法。
    assert project_short(project_id) == want_prefix.split(":", 1)[1]
    assert len(project_short(project_id)) == PROJECT_SHORT_LEN


def test_golden_covers_ten_cases() -> None:
    # ADR 0003 写的是 10 个，不是「大约 10 个」。少一个就该有人来解释为什么。
    assert len(GOLDEN) == 10
    assert len({(t, p) for t, p, _ in GOLDEN}) == 10


# ══════════════════════════════════════════════════════════════════════════
# 格式契约：随机段唯一能钉的东西
# ══════════════════════════════════════════════════════════════════════════


def test_shape_is_three_segments() -> None:
    got = new_id(EntityType.CHARACTER, "project:01JZ0000000000000000000000")
    parts = got.split(":")
    assert len(parts) == 3
    assert parts[0] == "character"
    assert len(parts[1]) == PROJECT_SHORT_LEN
    assert ULID_RE.match(parts[2])
    # 能被解析回来 = 它真是个 ULID，不是 26 个随机字符。
    assert str(ULID.from_str(parts[2])) == parts[2]


def test_event_id_uses_independent_hyperedge_prefix() -> None:
    project_id = "project:01JZ0000000000000000000000"
    got = new_id(EntityType.EVENT, project_id)

    assert got.startswith(f"event:{project_short(project_id)}:")
    assert ULID_RE.fullmatch(got.rsplit(":", 1)[1])
    assert "Event" not in {label.value for label in NodeLabel}


def test_extraction_run_id_uses_its_persisted_table_prefix() -> None:
    project_id = "project:01JZ0000000000000000000000"

    got = new_id(EntityType.EXTRACTION_RUN, project_id)

    assert got.startswith(f"extraction_run:{project_short(project_id)}:")
    assert ULID_RE.fullmatch(got.rsplit(":", 1)[1])


def test_020_new_id_types_use_their_own_prefixes() -> None:
    """020 / Task 9 新增的两类 id：application / analysis。**每个值对应一张真表**
    （ADR 0005 的增长规则），前缀不许和别的实体共用。"""
    pid = "project:01JZ0000000000000000000000"
    assert new_id(EntityType.EXTRACTION_APPLICATION, pid).startswith(
        f"application:{project_short(pid)}:"
    )
    assert new_id(EntityType.EXTRACTION_ANALYSIS, pid).startswith(
        f"analysis:{project_short(pid)}:"
    )


def test_ids_are_unique() -> None:
    pid = "project:01JZ0000000000000000000000"
    ids = {new_id(EntityType.EDGE, pid) for _ in range(1000)}
    assert len(ids) == 1000


def test_ids_sort_by_creation_time() -> None:
    """ULID 而非 UUIDv4 的**全部理由**（ADR 0003）：单调可排序。

    毫秒内不保证严格单调（python-ulid 不做同毫秒递增），所以这里跨毫秒测——
    「按创建顺序扫描」这个用例本来也是跨毫秒的。
    """
    pid = "project:01JZ0000000000000000000000"
    first = new_id(EntityType.EDGE, pid)
    time.sleep(0.005)
    second = new_id(EntityType.EDGE, pid)
    assert first < second


def test_project_id_has_no_scope_segment() -> None:
    pid = new_project_id()
    assert pid.startswith("project:")
    assert len(pid.split(":")) == 2
    assert ULID_RE.match(pid.split(":")[1])


def test_new_id_rejects_project_type() -> None:
    # 项目无法以自身为作用域——先有蛋才有鸡，这个调用一定是个 bug，不能给它一个「能跑」的答案。
    with pytest.raises(ValueError, match="new_project_id"):
        new_id(EntityType.PROJECT, "project:01JZ0000000000000000000000")


def test_project_short_rejects_empty() -> None:
    with pytest.raises(ValueError):
        project_short("")
    with pytest.raises(ValueError):
        new_id(EntityType.CHARACTER, "")


# ══════════════════════════════════════════════════════════════════════════
# artifact：ADR 0003 里 ULID 的唯一例外
# ══════════════════════════════════════════════════════════════════════════


def test_artifact_id_is_content_addressed() -> None:
    a = artifact_id("同样的 prompt".encode())
    b = artifact_id("同样的 prompt".encode())
    c = artifact_id("不一样的 prompt".encode())
    # 内容寻址的**全部**用途：回答「这段 prompt 是不是已经跑过」。ULID 答不了这个问题。
    assert a == b
    assert a != c
    assert a.startswith(ARTIFACT_PREFIX)
    assert len(a) == len(ARTIFACT_PREFIX) + 64


def test_artifact_golden() -> None:
    # 空输入的 sha256 是全世界最容易核对的一个 golden 值。
    assert artifact_id(b"") == (
        "artifact:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


# ══════════════════════════════════════════════════════════════════════════
# 跨项目误引用：project_short 前缀存在的理由
# ══════════════════════════════════════════════════════════════════════════


def test_belongs_to() -> None:
    a = "project:01JZ0000000000000000000000"
    b = "project:01JZ0000000000000000000001"
    node = new_id(EntityType.CHARACTER, a)
    assert belongs_to(node, a)
    assert not belongs_to(node, b)


def test_belongs_to_rejects_malformed() -> None:
    a = "project:01JZ0000000000000000000000"
    # 项目 ID 本身不是三段式，它不「属于」任何项目——包括它自己。
    assert not belongs_to(new_project_id(), a)
    assert not belongs_to("character:gu-qingyin", a)  # ADR 0003 推翻掉的那个方案
    assert not belongs_to("", a)
    assert not belongs_to(artifact_id(b""), a)


# ══════════════════════════════════════════════════════════════════════════
# 交叉验证：EntityType ≡ NodeLabel.lower()
#
# 这一块是本文件真正容易腐烂的地方。有人往 NodeLabel 加了第 9 类节点而 EntityType
# 没跟上时，症状是 `EntityType.for_node_label("Volume")` 抛 ValueError——在写入路径上，
# 也就是在作者点确认的那一刻。让它在这里先红。
# ══════════════════════════════════════════════════════════════════════════


def test_entity_type_covers_every_node_label() -> None:
    for label in NodeLabel:
        assert EntityType.for_node_label(label) == label.value.lower()


def test_node_labels_and_entity_types_agree() -> None:
    from_labels = {label.value.lower() for label in NodeLabel}
    assert from_labels <= {t.value for t in EntityType}
    assert len(from_labels) == 8  # §5.8 的 8 类，一个不多一个不少


@pytest.mark.parametrize("label", ["Volume", "Event"])
def test_for_node_label_rejects_non_node_entity_types(label: str) -> None:
    with pytest.raises(ValueError):
        EntityType.for_node_label(label)  # Event 是独立超边，不能借 ID 类型混进 NodeLabel。
