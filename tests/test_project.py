"""project.py —— `project` 表的唯一拥有者。"""

from __future__ import annotations

import inspect

import pytest

from novel_harness import project
from novel_harness.db import IN_MEMORY, connect, migrate
from novel_harness.project import (
    Project,
    ProjectNotFound,
    StaleBaseVersion,
    compare_and_bump_canon_version,
    create,
    get,
    require_canon_version,
)


@pytest.fixture
def conn():
    c = connect(IN_MEMORY)
    migrate(c)
    yield c
    c.close()


# ══════════════════════════════════════════════════════════════════════════
# API 面：这里测的是「不存在」
# ══════════════════════════════════════════════════════════════════════════

_MUTATION_PREFIXES = ("update", "delete", "remove", "edit", "set_", "patch", "drop", "purge", "bump")


def test_module_exposes_only_listed_project_operations() -> None:
    public = [
        name
        for name, obj in vars(project).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == project.__name__
    ]
    assert sorted(public) == [
        "apply_detected_language",
        "compare_and_bump_canon_version",
        "create",
        "get",
        "insert",
        "list_all",
        "override_language",
        "require_canon_version",
    ]
    mutations = [name for name in public if name.startswith(_MUTATION_PREFIXES)]
    assert mutations == []


def test_project_model_is_frozen() -> None:
    p = Project(id="project:X", name="青云记", root_path=".")
    with pytest.raises(Exception):  # noqa: B017 — pydantic 的 ValidationError，形状不是重点
        p.name = "别的"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# 往返：走的是真的 connect + migrate + INSERT，不是替身
# ══════════════════════════════════════════════════════════════════════════


def test_create_then_get_round_trip(conn) -> None:
    made = create(conn, name="青云记", root_path="./青云记")
    got = get(conn, made.id)

    assert got is not None
    assert got.id == made.id
    assert got.name == "青云记"
    assert got.root_path == "./青云记"
    assert got.canon_version == 0
    assert got == made


def test_insert_leaves_commit_to_the_caller(tmp_path) -> None:
    db_path = tmp_path / "project.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        conn.execute("BEGIN IMMEDIATE")

        made = project.insert(conn, name="未提交", root_path="books/未提交")

        assert get(conn, made.id) == made
    finally:
        conn.rollback()
        conn.close()

    reader = connect(db_path)
    try:
        assert get(reader, made.id) is None
    finally:
        reader.close()


def test_create_keeps_its_existing_commit_semantics(tmp_path) -> None:
    db_path = tmp_path / "project.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        made = project.create(conn, name="已提交", root_path="books/已提交")
    finally:
        conn.close()

    reader = connect(db_path)
    try:
        assert get(reader, made.id) == made
    finally:
        reader.close()


def test_canon_version_starts_at_zero(conn) -> None:
    # 它由 SQL 的 DEFAULT 起于 0，Python 侧永远不传它。
    assert create(conn, name="甲", root_path=".").canon_version == 0


def test_get_unknown_id_returns_none(conn) -> None:
    assert get(conn, "project:NOPE") is None


def test_two_creates_get_two_ids(conn) -> None:
    a = create(conn, name="甲", root_path="./甲")
    b = create(conn, name="乙", root_path="./乙")

    assert a.id != b.id
    # 两段不是三段：项目无法以自身为作用域（ids.new_project_id）。
    assert a.id.startswith("project:")
    assert a.id.count(":") == 1
    assert get(conn, a.id) == a
    assert get(conn, b.id) == b


def test_same_name_twice_is_two_projects(conn) -> None:
    # name 不是主键：同一个作者在两台机器上导同一本书是正常的。
    a = create(conn, name="青云记", root_path="./一")
    b = create(conn, name="青云记", root_path="./二")
    assert a.id != b.id


@pytest.mark.parametrize(
    ("name", "root_path"),
    [("", "."), ("青云记", "")],
)
def test_create_rejects_empty_fields(conn, name: str, root_path: str) -> None:
    with pytest.raises(ValueError):
        create(conn, name=name, root_path=root_path)


def test_create_has_no_canon_version_parameter() -> None:
    assert "canon_version" not in inspect.signature(create).parameters


def test_create_has_no_id_parameter() -> None:
    # id 由 new_project_id() 生成。开这个口子 = 让调用方塞一个非 ULID 进来，
    # 而 project_short() 对任何形状都给得出短指纹，于是错不会当场报，只会在
    # 「这个 node 属于哪个项目」上静默地永远返 False。
    assert "id" not in inspect.signature(create).parameters


def test_require_and_compare_bump_are_strict_and_do_not_commit(conn) -> None:
    made = create(conn, name="青云记-CAS", root_path=".")

    conn.execute("BEGIN IMMEDIATE")
    assert require_canon_version(conn, made.id) == 0
    assert compare_and_bump_canon_version(conn, made.id, 0) == 1
    assert require_canon_version(conn, made.id) == 1
    assert conn.in_transaction
    conn.rollback()

    assert require_canon_version(conn, made.id) == 0


@pytest.mark.parametrize("expected", [True, -1, 1.0, "0", None])
def test_compare_bump_rejects_non_strict_versions(conn, expected: object) -> None:
    made = create(conn, name="青云记-strict", root_path=".")
    with pytest.raises((TypeError, ValueError)):
        compare_and_bump_canon_version(conn, made.id, expected)  # type: ignore[arg-type]
    assert require_canon_version(conn, made.id) == 0


def test_compare_bump_distinguishes_missing_and_stale(conn) -> None:
    made = create(conn, name="青云记-stale", root_path=".")
    with pytest.raises(ProjectNotFound, match="project:missing"):
        require_canon_version(conn, "project:missing")
    with pytest.raises(ProjectNotFound, match="project:missing"):
        compare_and_bump_canon_version(conn, "project:missing", 0)
    with pytest.raises(StaleBaseVersion) as exc_info:
        compare_and_bump_canon_version(conn, made.id, 1)
    assert exc_info.value.project_id == made.id
    assert exc_info.value.expected == 1
    assert exc_info.value.current == 0
    assert require_canon_version(conn, made.id) == 0
