"""作者交代过的那些规矩，摆成一张回头能翻的表（`GET /api/projects/{pid}/rules`）。

── 这张表和 2026-08-14 撤掉的那块面板不是一回事 ──────────────────────────────

[ADR 0028](../docs/adr/0028-rules-expire-by-situation.md) 撤掉的是写作助手顶上那颗
「这一章的规矩」按钮和它背后的两条路由：**一块常驻的、带取消按钮的、说「这一章哪几条
生效」的面板**。作者 8-14 的原话是「这个原本就不需要展示给用户看……而不是显示出来给
用户选择」。

8-15 他要的是另一件事：「你在写某一章的时候用户讲过的规则，然后**你规定的时效**什么的
都可以记一下」。**「记一下」和「摆出来让他管」是两件事**，这份文件量的是前者：

| | 撤掉的那个 | 这一个 |
|---|---|---|
| 回答 | 这一章此刻哪几条生效 | 我到底跟它交代过什么 |
| 取消 | 有（存在的理由） | **一颗按钮都没有** |
| 生不生效 | 引擎按章号算 | **不说**（那是模型每轮按情境判的事） |

所以这份文件里有一条**反向**断言：出参里不许长出「还生不生效」那一列。

── 为什么要量「零的成色」 ────────────────────────────────────────────────

`remember_rule` 在真书上**一次都没开过火**（2026-08-15 实测：111935 那本库 7 个作者
回合、0 条规矩）。也就是说这张表最常见的样子就是空的，而空有两种：**一段对话都没有**
和**说过话但一条规矩都没记下**。屏幕上那两句话不一样，所以 `scanned_chats` 得是真的。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_harness.api.chat import RecordedRule

from test_chat_api import Scripted, configured, open_chat, says, use, wants  # noqa: F401

__all__ = ["configured"]  # re-export：这一份用的是 `test_chat_api` 那套 BYOK 装配


def _rules(client: TestClient, pid: str) -> dict[str, Any]:
    response = client.get(f"/api/projects/{pid}/rules")
    assert response.status_code == 200, response.text
    return response.json()


def _remember(rule: str, until: str) -> Any:
    return Scripted(
        wants(("remember_rule", json.dumps({"rule": rule, "until": until}))),
        says("好，记下了。"),
    )


def test_a_rule_the_model_recorded_shows_up_with_the_chapter_and_the_expiry(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一行四样：**第几章说的 / 那句话 / 管到什么时候 / 哪段对话**。

    「第几章」是这张表的时间轴。对一个写了 722 章的人来说它比几月几号有用，
    而且它已经存着（`ToolContext.working_chapter`，约束 10：不是他填的）。
    """
    pid = book["pid"]
    use(monkeypatch, _remember("男主在这片沙地不杀人", "男主走出这片沙地为止"))
    chat_id = open_chat(client, pid, title="沙地那一段")
    client.post(f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 3, "said": "沙地里别杀人"})

    body = _rules(client, pid)
    assert body["rules"] == [
        {
            "chapter": 3,
            "text": "男主在这片沙地不杀人",
            "until": "男主走出这片沙地为止",
            "chat_id": chat_id,
            "chat_title": "沙地那一段",
        }
    ]
    assert body["scanned_chats"] == 1


def test_the_table_never_says_whether_a_rule_still_applies(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 **出参里不许有「还生不生效」那一列，而且不许加。**

    那个答案只有读到规矩的那个模型知道（ADR 0028：有效期是情境的事，引擎不判）。
    引擎给一个出来就是编——**而编出来的那一列在屏幕上看起来完全正常**，
    正是这个仓库反复栽的那种「一句它不知道真假的话」。
    """
    pid = book["pid"]
    use(monkeypatch, _remember("冷一点", "这一场结束"))
    chat_id = open_chat(client, pid)
    client.post(f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 2, "said": "冷一点"})

    row = _rules(client, pid)["rules"][0]
    assert set(row) == {"chapter", "text", "until", "chat_id", "chat_title"}
    # 探针：这几个词是那一列可能长出来的样子。出参形状变了这条会红。
    assert set(RecordedRule.model_fields) == set(row)
    for forbidden in ("live", "active", "expired", "in_force", "chapter_wide", "seq"):
        assert forbidden not in RecordedRule.model_fields


def test_the_newest_chapter_comes_first(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """他最近在写的那一章排最前 —— 翻这张表的人是从「我刚才交代了什么」开始找的。"""
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    for chapter, rule in ((1, "别写打斗"), (3, "冷一点"), (2, "少写心理描写")):
        use(monkeypatch, _remember(rule, "这一章写完为止"))
        client.post(
            f"/api/projects/{pid}/chats/{chat_id}/turn",
            json={"chapter": chapter, "said": rule},
        )

    assert [r["chapter"] for r in _rules(client, pid)["rules"]] == [3, 2, 1]


def test_an_empty_table_says_which_kind_of_empty_it_is(
    client: TestClient, book: dict[str, str], configured: None
) -> None:
    """**零带着成色**（§10 约束 8）。

    「一段对话都没有」和「说过话但一条规矩都没记下」下一步动作不同，而后者今天是
    **默认**那一档：`remember_rule` 在真书上一次都没开过火。两种空长得一样的话，
    作者会以为这个功能坏了——而它只是没被触发过。
    """
    pid = book["pid"]
    assert _rules(client, pid) == {"rules": [], "scanned_chats": 0}

    open_chat(client, pid)
    assert _rules(client, pid) == {"rules": [], "scanned_chats": 1}


def test_a_rule_from_before_the_column_shows_an_empty_expiry_not_a_made_up_one(
    client: TestClient, book: dict[str, str], tmp_path: Path
) -> None:
    """迁移 016 之前记下的规矩没有时效。**表上那一格是空的，不是编的。**

    补一个进去 = 替模型说一句它没说过的话，而那句话在表上读起来像是它当时的判断。
    """
    from novel_harness.agent.loop import AgentMessage, Role
    from novel_harness.agent.store import ChatStore
    from novel_harness.db import connect

    pid = book["pid"]
    chat_id = open_chat(client, pid)
    conn = connect(book["db"])
    try:
        ChatStore(conn).append(
            pid,
            chat_id,
            base_count=0,
            messages=(
                AgentMessage(role=Role.USER, content="别写打斗"),
                # 016 之前那种：有正文、有章号，**没有 `rule_until`**。
                AgentMessage(role=Role.SYSTEM, content="别写打斗", chapter=9),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    row = _rules(client, pid)["rules"][0]
    assert (row["chapter"], row["text"], row["until"]) == (9, "别写打斗", "")
