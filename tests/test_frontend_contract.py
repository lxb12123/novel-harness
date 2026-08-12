"""前端 ↔ 后端契约 —— 把真出参冻成 fixture，两头各钉一半。

**这是这个仓库里唯一真正的双份维护成本**：引擎 → CLI/HTTP 两个薄壳是加法
（`api/app.py` 第一行的纪律「只把引擎函数包成 HTTP，不装业务」保证了这点），
而前端 ↔ 后端契约是乘法。而且它坏起来是**无声的**：

- `tsc -b` 看不见后端。`frontend/src/api/types.ts` 自称「手写真相源」——手写的东西
  和后端一致只是**当时**一致，后端改了它不会跟着变，更不会红。
- 前端的组件测试就算有，喂的也是手写 fixture，同样和后端脱钩：两份手写的谎言互相验证。

所以这里的 fixture **不是手写的**：它是从真 app（`TestClient` + 真 SQLite）dump 出来的
真响应，规范化掉 ULID / 时间戳 / 路径之后冻在 `frontend/src/__fixtures__/api.json`。
于是这条缝的两半各有守卫：

- **后端侧（本文件）**：出参一变，重新 dump 出来的东西和冻住的对不上 → pytest 红。
- **前端侧（`frontend/src/**/*.test.tsx`）**：组件直接吃这份 fixture 渲染 →
  形状变了但没人改组件 → vitest 红。

改了后端出参之后正确的做法是 `NH_UPDATE_FIXTURES=1 uv run pytest tests/test_frontend_contract.py`，
**然后去看 git diff**——那个 diff 就是「前端要跟着改什么」的清单。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_activity import seed_call, seed_run
from test_api import (
    _seed_edge_conflict_proposal,
    _seed_low_confidence_proposal,
    _seed_new_character_proposal,
    _seed_provisional_event,
)

FIXTURE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "__fixtures__" / "api.json"

# 每次跑都不一样的东西。留着它们，这份 fixture 每次 dump 都是新的 diff，等于没有守卫。
# 注意**不是**把它们抹成同一个值：id 抹平了 React 的 key 就撞了，而 key 撞掉的渲染
# 恰恰是这份 fixture 该暴露的那类 bug。所以按首次出现顺序映射成稳定别名。
_ID = re.compile(r"[a-z_]+:(?:[0-9a-f]{8}:)?[0-9A-HJKMNP-TV-Z]{26}")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})")
_SHA = re.compile(r"\b[0-9a-f]{64}\b")


class _Normalizer:
    """ULID → `Label:ID1`（按首次出现顺序）。稳定的前提是后端的创建顺序和排序稳定——
    ULID 本身按时间单调，所以「按 id 排序」= 「按创建顺序排序」，跨次运行一致。
    """

    def __init__(self, tmp_root: str) -> None:
        self._seen: dict[str, str] = {}
        self._tmp_root = tmp_root

    def _id(self, match: re.Match[str]) -> str:
        raw = match.group(0)
        if raw not in self._seen:
            kind = raw.split(":", 1)[0]
            self._seen[raw] = f"{kind}:ID{len(self._seen) + 1}"
        return self._seen[raw]

    def text(self, value: str) -> str:
        value = value.replace(self._tmp_root, "<root>")
        value = _ID.sub(self._id, value)
        value = _TS.sub("<ts>", value)
        return _SHA.sub("<sha>", value)

    def walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.walk(v) for v in value]
        if isinstance(value, dict):
            return {self.text(k): self.walk(v) for k, v in value.items()}
        return value


def test_frontend_fixture_matches_the_real_api(
    client: TestClient,
    book: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前端吃的那份 fixture 必须还等于真后端今天吐的东西。

    捕的是 `frontend/src/api/hooks.ts` 里**每一个** hook 打的端点——读路径全量，
    写路径捕回执（`DeclareDrawer` / `RosterDrawer` 渲染的正是回执）。
    漏掉一个端点，那个端点的出参就能悄悄改而不被任何东西发现。
    """
    pid = book["pid"]
    base = f"/api/projects/{pid}"
    # 设置会写本机文件——测试必须指到临时路径，不许碰真实用户目录。
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    norm = _Normalizer(str(tmp_path))
    dump: dict[str, Any] = {}

    def grab(key: str, response: Any) -> Any:
        assert response.status_code == 200, f"{key} → {response.status_code} {response.text}"
        dump[key] = norm.walk(response.json())
        return dump[key]

    # ── AI 设置（BYOK）：GET 遮蔽 / PUT 合并回执 ──────────────────────────
    grab("settings", client.get("/api/settings"))
    saved = client.put(
        "/api/settings",
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-contract-key",
        },
    )
    assert saved.status_code == 200, saved.text
    grab("settingsSaved", saved)

    # ── 读路径（声明之前的状态）────────────────────────────────────────────
    grab("projects", client.get("/api/projects"))
    grab(
        "bootstrapImport",
        client.post(
            "/api/projects/bootstrap",
            json={
                "mode": "import",
                "name": "契约样书",
                "text": "第一章 契约\n\n这一章由真实 API 导入。\n",
            },
        ),
    )
    grab("roster", client.get(f"{base}/roster"))
    grab("chapters", client.get(f"{base}/chapters"))
    grab("chapterText", client.get(f"{base}/chapters/1/text"))
    grab("chapterHistory", client.get(f"{base}/chapters/1/history"))
    grab("scenes", client.get(f"{base}/chapters/2/scenes"))
    grab("resolve", client.get(f"{base}/resolve", params={"surface": "萧决"}))
    grab("resolveAmbiguous", client.get(f"{base}/resolve", params={"surface": "师兄"}))
    grab(
        "subgraph",
        client.get(f"{base}/subgraph", params={"center": book["萧决"], "chapter": 2, "hops": 1}),
    )
    grab(
        "characterState",
        client.get(f"{base}/characters/{book['萧决']}/state", params={"chapter": 2}),
    )

    # ── 写路径的回执 ──────────────────────────────────────────────────────
    grab("locate", client.post(f"{base}/locate", json={"quote": "萧决在青云城主府"}))
    grab(
        "createNode",
        client.post(f"{base}/nodes", json={"label": "Character", "name": "顾清音"}),
    )
    grab(
        "createSecret",
        client.post(
            f"{base}/nodes",
            json={"label": "Secret", "name": "玄铁令下落", "description": "在北荒"},
        ),
    )
    grab(
        "createAlias",
        client.post(f"{base}/aliases", json={"of": "顾清音", "surface": "顾姑娘"}),
    )
    grab(
        "declareKnows",
        client.post(
            f"{base}/declare/knows",
            json={
                "who": "萧决",
                "secret": "血脉秘密",
                "quote": "萧决在青云城主府第一次听说了血脉秘密的真相。",
            },
        ),
    )

    # ── 读路径（声明之后：矩阵里现在有 KNOWS，前端渲染测试要的就是这个形状）──
    cast = {"cast": "萧决,李管家"}
    grab("matrix", client.get(f"{base}/chapters/2/matrix", params=cast))
    grab("constraints", client.get(f"{base}/chapters/2/constraints", params=cast))
    grab("states", client.get(f"{base}/chapters/2/state", params=cast))
    grab("check", client.post(f"{base}/chapters/3/check"))
    # 在场是推出来的，不是作者填的：右栏靠这个显示「这一章提到了谁」。
    # 第 1 章有正文（has_text=true），第 99 章没有——两者出参必须长得不一样。
    grab("mentioned", client.get(f"{base}/chapters/1/mentioned"))
    grab("mentionedEmpty", client.get(f"{base}/chapters/99/mentioned"))

    # ── M4：提案审阅 / 被动确认（seed 走存储层，审阅动作走真 API）─────────────
    m4_proposal_id, m4_event_id, m4_base = _seed_low_confidence_proposal(book)
    m4_confirm_event = _seed_provisional_event(book)
    _m4_edge_proposal, _m4_edge_proposed = _seed_edge_conflict_proposal(book)
    _m4_newchar_proposal, _m4_newchar_surface = _seed_new_character_proposal(book)
    grab("proposals", client.get(f"{base}/chapters/1/proposals"))
    grab(
        "eventsProvisional",
        client.get(f"{base}/chapters/1/events", params={"scope": "PROVISIONAL"}),
    )
    grab(
        "proposalAccept",
        client.post(
            f"{base}/proposals/{m4_proposal_id}/accept",
            json={"expected_canon_version": m4_base},
        ),
    )
    m4_reject_id, _reject_event, m4_reject_base = _seed_low_confidence_proposal(book)
    grab(
        "proposalReject",
        client.post(
            f"{base}/proposals/{m4_reject_id}/reject",
            json={"action": "reject", "expected_canon_version": m4_reject_base},
        ),
    )
    grab(
        "provisionalConfirm",
        client.post(
            f"{base}/chapters/1/provisional/confirm",
            json={
                "fact_kind": "event",
                "fact_ids": [m4_confirm_event],
                "expected_canon_version": m4_reject_base,
            },
        ),
    )

    # ── 章节滚动总结：生成一次，再看窗口 ──────────────────────────────────
    # **只把模型换成桩**（同 `test_synth_artifact` 那一轮），库、幂等键、审计写入全走真代码。
    # 不换的话这个端点没法进 fixture，而没进 fixture 的端点就能悄悄改出参——
    # 它恰好是本轮补的洞，别让它一出生就在守卫视野外。
    import novel_harness.api.deps as deps_mod
    from novel_harness.draft.provider import CompletionResult

    monkeypatch.setattr(
        deps_mod,
        "complete",
        lambda messages, *, config=None, plan=None, client=None: CompletionResult(
            text="萧决在青云城主府听说了血脉秘密。", model="deepseek-v4-flash", finish_reason="stop"
        ),
    )
    grab("summaryGenerated", client.post(f"{base}/chapters/1/summary"))
    # 第 12 章 → 窗口是第 1–3 章：**三种状态一次到齐**（已生成 / 有正文没生成 /
    # 根本没写）。少一种，前端就有一条分支是照着想象写的。
    grab("summaries", client.get(f"{base}/chapters/12/summaries"))

    # ── 改一条**已经生效**的事实（1.1）+ 活动日志（2.1）─────────────────────
    # 顺序是硬的：`/canon/knowledge` 要先跑，日志里才有一条**带真跳转坐标**的
    # `knowledge_edit`。没有它，这份 fixture 里全是 `endpoints: []` 的兜底坐标，
    # 而前端要照着写的恰恰是「点这里去改」那条分支。
    version = client.get(f"{base}").json()["canon_version"]
    corrected = client.post(
        f"{base}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "他以为那只是个传闻",
            "expected_canon_version": version,
        },
    )
    grab("canonKnowledge", corrected)

    # 上面 `summaryGenerated` 已经写了一条**真的** `model_call`（capability=summarizer，
    # 走 `record_call`）。抽取运行和 extractor 调用走桩：跑一次真抽取要模型、要钱。
    activity_call = seed_call(book)
    activity_run = seed_run(book, 1, proposals=2, call_id=activity_call)
    grab("activity", client.get(f"{base}/activity", params={"limit": 8}))
    # 按 actor 过滤 —— ADR 0020 点名的那件事（作者点过的会被 system 行淹没）。
    # `actors[]` 的计数**不跟着过滤走**，这份 fixture 冻的就是这个差别。
    grab(
        "activityAuthorOnly",
        client.get(f"{base}/activity", params={"actor": "author", "limit": 4}),
    )
    grab("activityRunDetail", client.get(f"{base}/activity/{activity_run}"))
    # 三个 source 的展开层各冻一份。**少一份就有一整块屏幕没被守卫看过**：
    # 「界面上不摆研发术语」那条断言只能扫它真的渲染出来的东西，而这一份里曾经躺着
    # prompt 指纹、两个 `artifact:sha256:…` 和一整段 `params_json`。
    grab("activityCallDetail", client.get(f"{base}/activity/{activity_call}"))
    # 展开一条作者亲手点过的确认：`payload` 是这一层唯一的泄漏面，前端照它渲染信封。
    grab(
        "activityDecisionDetail",
        client.get(f"{base}/activity/{corrected.json()['decision_id']}"),
    )
    grab("runs", client.get(f"{base}/runs"))

    # ── 已生效事件 + 改它的知情/在场名单（1.1 的另一半）──────────────────────
    # **放在活动日志之后是有意的**：这次编辑会多写一条 `decision_log`，冻在上面那份
    # `activity` 里会让日志页那几条按 id 认行的测试跟着这一步的实现细节漂。
    # 前端要的是两个形状：能看的那张单子（`eventsCanon`）和改完的回执（`canonEventCast`）。
    canon_events = client.get(f"{base}/chapters/1/events", params={"scope": "CANON"})
    grab("eventsCanon", canon_events)
    views = canon_events.json()
    assert views, "第 1 章一条已生效事件都没有——名单那一格的夹具会变成空壳"
    target = views[0]
    # 去掉一个知情人：`knowers` 是抽取里唯一靠推断得来的一维，也是最需要改的一维
    # （ADR 0020 的代价那节点名了它）。**绝对集合**，所以这里发的是「改完之后是这些人」。
    kept = [k["id"] for k in target["knowers"]][:-1]
    assert len(kept) < len(target["knowers"]), "这条事件没有知情人可去掉，编辑会被后端判空"
    grab(
        "canonEventCast",
        client.post(
            f"{base}/canon/events/{target['event']['id']}/cast",
            json={
                "knower_ids": kept,
                "expected_canon_version": client.get(f"{base}").json()["canon_version"],
            },
        ),
    )

    # ── 拒绝形态：前端有专门分支渲染它们，同样是契约 ────────────────────────
    ambiguous = client.post(
        f"{base}/declare/knows", json={"who": "师兄", "secret": "血脉秘密", "quote": "萧决"}
    )
    assert ambiguous.status_code == 409, ambiguous.text
    dump["errorAmbiguousName"] = norm.walk(ambiguous.json())

    short = client.post(f"{base}/aliases", json={"of": "萧决", "surface": "决"})
    assert short.status_code == 422, short.text
    dump["errorShortAlias"] = norm.walk(short.json())

    # ── 改一条已生效事实的三种拒绝 ────────────────────────────────────────
    # 三种含义完全不同，界面上必须说三句不同的话，所以三种形状都得是契约的一部分：
    # 409 = 这本书在别处刚被改过（**不许静默重试**）；404 = 他点的那条今天不在了；
    # 422 = 这次改动本身讲不通。手写这三份等于两份手写的东西互相验证。
    fresh = client.get(f"{base}").json()["canon_version"]
    stale = client.post(
        f"{base}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "KNOWS",
            "expected_canon_version": 0,  # 作者手上那份是很久以前的
        },
    )
    assert stale.status_code == 409, stale.text
    dump["errorStaleCanon"] = norm.walk(stale.json())

    absent = client.post(
        f"{base}/canon/knowledge",
        json={
            # 这一格今天是「不知道」——没有可改的事实（界面上这一格根本不该能点，
            # 这份夹具冻的是「他还是点到了」那条退路）。
            "character_id": book["李管家"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "以为是假的",
            "expected_canon_version": fresh,
        },
    )
    assert absent.status_code == 404, absent.text
    dump["errorFactNotFound"] = norm.walk(absent.json())

    refused = client.post(
        f"{base}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",  # 上面那次改正之后它已经是这一种了
            "believed_value": "还是那句传闻",
            "expected_canon_version": fresh,
        },
    )
    assert refused.status_code == 422, refused.text
    dump["errorKnowledgeRefused"] = norm.walk(refused.json())

    # ── 写作助手的会话（模式二，ADR 0019）──────────────────────────────────
    # **只把模型换成桩**（同上面那一轮总结）：库、工具派发、记账、会话表全走真代码。
    # 换的那一处是 `build_agent_model`——它就是壳里那个注入点。
    import novel_harness.api.chat as chat_mod
    from novel_harness.draft.provider import ToolCall

    def scripted_agent(messages: Any, *, tools: Any, cancel: Any) -> Any:
        # 第一步要两个工具（真的会派发、真的会绑章号），第二步说话收手。
        # **第二个是起草**：ADR 0022 之后它不落盘，回执上多一份候选——前端那一栏
        # （「写了两稿，挑一个」）照的就是这份 fixture，而**不落盘正是它要画的常态**。
        if any(m.get("role") == "tool" for m in messages):
            return CompletionResult(
                text="第 2 章这一场，血脉那条先别说破。我写了一稿，你看看要不要。",
                model="deepseek-v4-flash",
                finish_reason="stop",
                prompt_tokens=1_200,
                completion_tokens=64,
            )
        return CompletionResult(
            text="",
            model="deepseek-v4-flash",
            finish_reason="tool_calls",
            tool_calls=(
                ToolCall(id="c1", name="scene_constraints", arguments='{"chapter": 2}'),
                ToolCall(
                    id="c2",
                    name="draft_chapter",
                    arguments=json.dumps(
                        {"chapter": 2, "goal": "两人在城主府对峙"}, ensure_ascii=False
                    ),
                ),
            ),
        )

    # 起草那一次真的模型调用也换成桩（同上面那一轮总结）：约束装配、候选表、
    # 记账全走真代码，换的只有「模型答了什么」。
    import novel_harness.draft.generate as generate_mod

    monkeypatch.setattr(
        generate_mod,
        "complete",
        lambda messages, *, config, plan, client=None: CompletionResult(
            text="〖自述〗：这一版更冷，收在他没抬头。\n\n" + "风雪落在肩上，他终于抬起头。" * 200,
            model="deepseek-v4-flash",
            finish_reason="stop",
            prompt_tokens=1_800,
            completion_tokens=2_600,
        ),
    )

    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: scripted_agent)
    created = client.post(f"{base}/chats", json={"title": "", "house_style": ""})
    assert created.status_code == 201, created.text
    dump["chatCreated"] = norm.walk(created.json())
    chat_id = created.json()["id"]
    grab(
        "chatTurn",
        client.post(
            f"{base}/chats/{chat_id}/turn",
            json={"chapter": 2, "said": "第 2 章能说破血脉的事吗？"},
        ),
    )
    grab("chatDetail", client.get(f"{base}/chats/{chat_id}"))
    # ── 候选稿（ADR 0022）：**摆出来让作者挑的那一栏** ────────────────────────
    # 列表不带正文（一次列二十稿就是二十章正文），要摊开那一版才单取一次。
    grab("drafts", client.get(f"{base}/drafts"))
    draft_id = client.get(f"{base}/drafts").json()["drafts"][0]["id"]
    grab("draftDetail", client.get(f"{base}/drafts/{draft_id}"))
    grab("chats", client.get(f"{base}/chats"))
    # 没在跑的时候按停：`stopped=false` **不是失败**，前端有一条分支照它渲染。
    grab("chatStopped", client.post(f"{base}/chats/{chat_id}/stop"))
    doomed = client.post(f"{base}/chats", json={"title": "删掉它"})
    grab("chatDeleted", client.delete(f"{base}/chats/{doomed.json()['id']}"))

    # ── 多版本的一章：版本抽屉的「还原 / 删除」只在有第二版时才存在 ──────────
    # **放在最后**：这一步会改第 2 章的正文，前面每一个 grab 都不该看见它。
    # `chapterHistory` 那份只有一版（导入即当前），照它写出来的界面在真实的两版面前
    # 是没被验过的——所以这里真存一次，冻的是「有历史可还原」那个形态。
    two_versions = client.get(f"{base}/chapters/2/text").json()["markdown"]
    saved_again = client.put(
        f"{base}/chapters/2/text", json={"markdown": two_versions + "\n后来又添了一段。\n"}
    )
    # 保存的回执也冻住：还原走的就是这条 PUT，测试桩得照它的真形状答话。
    grab("chapterSaved", saved_again)
    grab("chapterHistoryTwo", client.get(f"{base}/chapters/2/history"))

    # ── 书架：一个库里可以有多本书（`bootstrap` 往当前库里加项目）───────────
    # `projects` 那份是**建第二本之前**的状态，只有一本；侧栏的书架、切书弹窗、
    # 「从侧栏移除」全都只在两本以上时才存在形态，照一本写的界面等于没验过。
    grab("projectsTwo", client.get("/api/projects"))

    frozen = json.dumps(dump, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if os.environ.get("NH_UPDATE_FIXTURES"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(frozen, encoding="utf-8")
        return

    assert FIXTURE.exists(), (
        f"{FIXTURE} 不在。生成：NH_UPDATE_FIXTURES=1 uv run pytest {Path(__file__).name}"
    )
    assert FIXTURE.read_text(encoding="utf-8") == frozen, (
        "后端出参和前端吃的那份 fixture 对不上了。\n"
        "这**不一定是 bug**——如果是你有意改的出参，跑：\n"
        f"  NH_UPDATE_FIXTURES=1 uv run pytest tests/{Path(__file__).name}\n"
        "然后**看 git diff**：那个 diff 就是前端要跟着改什么的清单。\n"
        "（不看就更新等于把守卫关掉——它拦的正是「后端改了、前端没跟上、还没人发现」。）"
    )
