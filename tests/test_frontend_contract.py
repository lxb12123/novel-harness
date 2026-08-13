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
            # 手填的窗口也进这份 dump：**「没填」那一份由上面的 GET 提供**，
            # 两种长相前端都要渲染（空框 vs 显示他填的数），只捕一种等于只验了一半。
            "context_window": 128_000,
        },
    )
    assert saved.status_code == 200, saved.text
    grab("settingsSaved", saved)

    # ── 读路径（声明之前的状态）────────────────────────────────────────────
    grab("projects", client.get("/api/projects"))
    imported = client.post(
        "/api/projects/bootstrap",
        json={
            "mode": "import",
            "name": "契约样书",
            "text": "第一章 契约\n\n这一章由真实 API 导入。\n",
        },
    )
    grab("bootstrapImport", imported)
    # 第二本书的 id。**用量条那三档里的「全报了」要靠它**：第一本书从这一步之后就一直
    # 躺着一次不报 usage 的调用（下面那次总结），再也回不到「全报了」那一档。
    second_pid = imported.json()["project"]["id"]
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
    # ── 用量条第三档：**这本书唯一一次模型调用，供应商没报 usage** ──────────────
    # 桩返回的 `CompletionResult` 不带 `prompt_tokens`，也就是流式下 DeepSeek 的真实
    # 形状（`supports_stream_usage` 不是 True ⇒ 不加 `stream_options` ⇒ 两个数都 NULL）。
    # **这一档必须是真 dump 而不是在前端拼一个**：它正是作者按 README 默认配置写书时
    # 天天看见的那一屏，而底栏原来在这一屏上说「读入 0 token」。
    # 抓在这里是因为再往下那次 `seed_call` 就把这本书推进「报了一部分」那一档了。
    grab("runsUnreported", client.get(f"{base}/runs"))
    # 第 12 章 → 窗口是第 1–3 章：**三种状态一次到齐**（已生成 / 有正文没生成 /
    # 根本没写）。少一种，前端就有一条分支是照着想象写的。
    grab("summaries", client.get(f"{base}/chapters/12/summaries"))

    # ── 作者改得动它（迁移 013）：单章读端 + 改 + 撤回 ──────────────────────
    # **改和撤回落在第 2 章上**，不落在第 1 章：上面那份 `summaries` 里第 1 章是
    # 「已生成」那一档的唯一样本，动了它前端就少一种长相。
    grab("summaryChapter", client.get(f"{base}/chapters/1/summary"))
    grab(
        "summaryEdited",
        client.patch(
            f"{base}/chapters/2/summary",
            json={"summary": "萧决把玄铁令收进袖中，谁也没告诉。"},
        ),
    )
    grab("summaryRetracted", client.delete(f"{base}/chapters/2/summary"))

    # ── 总结 = 可反查的记忆点（T6）：这一段提到了什么 + 还有哪几章提到它 ────────
    # **抓在这儿是有先后的**：上面第 1 章刚生成过一段总结（内容是
    # 「萧决在青云城主府听说了血脉秘密。」），三个 label 一次到齐（人物 / 地点 / 秘密）。
    # 少一种，前端就有一档芯片是照着想象画的——而秘密那一档正是「只出 NodeRef」
    # 那条纪律唯一验得出来的地方。
    #
    # 反查那一份**故意落在萧决身上**：第 1 章提到他，第 2 章那一段刚被撤回，
    # 于是它冻住的是「撤回过的章不在名单里」——这一层最贵的那条断言（索引跟着总结走）。
    grab("summaryMentions", client.get(f"{base}/chapters/1/summary/mentions"))
    grab(
        "summaryMentionTrail",
        client.get(f"{base}/nodes/{book['萧决']}/summary-mentions"),
    )

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
    # 缓存那两个数**给的是「报了」那一档**：夹具里只躺一种形状的样本，等于那条
    # 「屏幕上不摆研发术语」的断言扫的是一块永远长一个样的屏幕（`_RUN_ERROR_LABEL`
    # 记着这个仓库上一次栽在这上面的现场——三条 run 全是成功的，失败那条文案没人看过）。
    # 这里冻的是 DeepSeek 的真实形状：报了命中量、不报写入量。
    activity_call = seed_call(book, cache_read_tokens=960, cache_write_tokens=None)
    activity_run = seed_run(book, 1, proposals=2, call_id=activity_call)
    # 审阅面板轮询的那条端点（`GET …/extractions/{run_id}`）。**跑成了的那一份在这儿，
    # 没跑成的那一份在文件最后**——两份都得是真 dump，理由见那儿。
    grab("extractionRun", client.get(f"{base}/extractions/{activity_run}"))
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
    # 用量条第二档：**这本书两次调用，一次报了 usage（上面 seed 的）一次没报**
    # （那次总结走的是真 `record_call`，桩没给 token 数）。也就是「合计只算得上一半」
    # 那一屏——它是默认路由下最常见的一档，所以给它主名字。
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
    created = client.post(f"{base}/chats", json={"title": "", "write_rule": ""})
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

    # ── 长连接那一轮（ADR 0024）：**冻的是原始帧，不是一份「录像」** ──────────────
    #
    # ADR 0024 原文说「把一整轮的事件序列冻成一份录像」，落地当天把做法更正了：
    # **冻在 `tests/` 里的录像只是又一份手写夹具**，正是这条缝原本的病。
    # `api.json` 的价值从来不在「冻住」，在**前端吃的是后端 dump 出来的同一份字节**。
    # 所以这儿走的是既有那条路：多 dump 一段 `chatTurnEvents`，
    # 让对话面板的组件测试吃**同一份**——连帧格式（`event:` / `data:` / 空行）
    # 都是真的，于是浏览器那个解码器是在真字节上被验的。
    #
    # **另开一段对话**：跑在上面那段上会把 `chatDetail` / `drafts` / `chats` 三份
    # 已经抓好的夹具全推着走，而那三份是前端好几块屏幕照着写的。
    streamed_chat = client.post(f"{base}/chats", json={"title": "长连接那一轮"})
    assert streamed_chat.status_code == 201, streamed_chat.text
    events = client.post(
        f"{base}/chats/{streamed_chat.json()['id']}/turn/events",
        json={"chapter": 2, "said": "第 2 章这一场先写一稿看看。"},
    )
    assert events.status_code == 200, events.text
    assert events.headers["content-type"].startswith("text/event-stream")
    raw_frames = [block + "\n\n" for block in events.text.split("\n\n") if block]
    assert "".join(raw_frames) == events.text, (
        "拆帧和原始响应体对不上 —— 这份夹具不再是「同一份字节」了"
    )
    assert raw_frames[-1].startswith("event: receipt"), "最后一帧必须是回执"
    dump["chatTurnEvents"] = norm.walk(raw_frames)
    doomed = client.post(f"{base}/chats", json={"title": "删掉它"})
    grab("chatDeleted", client.delete(f"{base}/chats/{doomed.json()['id']}"))

    # ── 作者的规矩（ADR 0023 决策二）：**摆出来 + 能取消** ──────────────────────
    #
    # **又另开一段**（同上面那条理由）：这几轮会往历史里加东西，跑在前面那两段上会把
    # 已经抓好的夹具推着走。
    #
    # 三份一起冻，因为这块面板有三种长相，而**照一种写出来的界面等于只验过三分之一**：
    # 有规矩 / 一条都没有过 / 定过但都不作数了。后两种在屏幕上必须说不同的话
    # （§10 约束 8：零带着理由），而它们在真夹具里长得都是「空清单」。
    ruled = client.post(f"{base}/chats", json={"title": "定几条规矩"})
    assert ruled.status_code == 201, ruled.text
    ruled_id = ruled.json()["id"]

    def remembers(*rules: str) -> Any:
        """一轮：模型先叫几次「记下来」，再说一句话收手。"""
        script = [
            CompletionResult(
                text="",
                model="deepseek-v4-flash",
                finish_reason="tool_calls",
                tool_calls=tuple(
                    ToolCall(
                        id=f"r{i}",
                        name="remember_rule",
                        arguments=json.dumps({"rule": rule}, ensure_ascii=False),
                    )
                    for i, rule in enumerate(rules)
                ),
            ),
            CompletionResult(
                text="记下了。",
                model="deepseek-v4-flash",
                finish_reason="stop",
                prompt_tokens=900,
                completion_tokens=12,
            ),
        ]
        seen: list[Any] = []

        def model(messages: Any, *, tools: Any, cancel: Any) -> Any:
            seen.append(messages)
            return script[min(len(seen) - 1, len(script) - 1)]

        return model

    # 第一轮记一条，第二轮把同一条**再记一遍**（作者又说了）+ 另加一条。
    # 于是清单上一条是章级（说到第二遍自动升上来）、一条还是批级——**两种长相都在夹具里**。
    for said, remembered in (
        ("这一章别写打斗。", ("这一章别写打斗",)),
        ("我说真的，别写打斗；还有，冷一点。", ("这一章别写打斗", "冷一点")),
    ):
        monkeypatch.setattr(chat_mod, "build_agent_model", lambda c, p, m=remembers(*remembered): m)
        ran = client.post(
            f"{base}/chats/{ruled_id}/turn", json={"chapter": 2, "said": said}
        )
        assert ran.status_code == 200, ran.text

    grab("chatRules", client.get(f"{base}/chats/{ruled_id}/rules", params={"chapter": 2}))
    # 作者翻到第 7 章：那两条都不属于这一章了，**而它们仍然存在过**——空清单带着的
    # 那个数就是界面说「定过、这会儿都不作数了」的全部依据。
    grab("chatRulesExpired", client.get(f"{base}/chats/{ruled_id}/rules", params={"chapter": 7}))
    # 一条都没定过的那一段（`chat_id` 那一轮只查了约束、写了一稿）。**这不是边角**：
    # 每一段对话都是从这一档开始的，它是作者最常看见的那一屏。
    grab("chatRulesNone", client.get(f"{base}/chats/{chat_id}/rules", params={"chapter": 2}))
    # 点掉那条章级的。**它记过两遍，而撤销按身份撤掉每一份**——只划掉这个下标的话，
    # 前面那一遍还在，且没有任何东西会报错（`api/chat.py::revoke_rule` 写着那个形态）。
    chapter_wide = next(
        rule
        for rule in dump["chatRules"]["rules"]
        if "第 2 章" in rule["scope"]
    )
    grab(
        "chatRuleRevoked",
        client.delete(f"{base}/chats/{ruled_id}/rules/{chapter_wide['seq']}"),
    )

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

    # ── 用量条第一档：**这本书每一次调用供应商都报了 usage** ────────────────────
    # 三档一次到齐（同上面 `summaries` 那一轮的理由）：夹具里只躺一种形状的样本，
    # 屏幕守卫扫的就是一块永远长一个样的屏幕。这一档落在第二本书上不是取巧——
    # 第一本从那次总结起就再也回不到「全报了」，而作者接 OpenAI 那四条路由时
    # 天天看见的正是这一屏。
    seed_call({**book, "pid": second_pid}, capability="summarizer")
    grab("runsAllReported", client.get(f"/api/projects/{second_pid}/runs"))

    # ── 一轮没跑成，那句话**留在对话里**（2026-08-13，迁移 012）───────────────
    #
    # 作者第一次真用就撞到的那一档：他说了两句，助手一个字都没有（他那台机器到端点的
    # TLS 全断，那一轮在发出去之前就死了）。屏幕上确实弹过一句提醒，可它活在组件状态里
    # ——他再发一句就没了。现在它落库，于是 `GET …/chats/{id}` 里多出**第三种说话人**。
    #
    # **必须真 dump 一份**：前端那块屏幕要照着「系统」那一档写，而手写一份
    # 「我以为它长这样」正是这条缝原本的病。
    #
    # **又另开一段**（同上面几条理由）：这一轮会往历史里加东西。
    def unreachable(messages: Any, *, tools: Any, cancel: Any) -> Any:
        from novel_harness.draft.provider import ProviderError

        raise ProviderError("TLSV1_ALERT_INTERNAL_ERROR: api.example.com")

    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: unreachable)
    broke = client.post(f"{base}/chats", json={"title": "那一轮没跑成"})
    assert broke.status_code == 201, broke.text
    broke_id = broke.json()["id"]
    # **两轮，因为作者的屏幕上就是两轮**（你好 / fff）。两轮还顺手冻住了一件只有
    # 两轮才有的事：系统那一行的 `seq` 和紧跟其后那条作者发言**是同一个数**
    # （它不占历史下标）——照着一份只有一轮的夹具写出来的界面，会拿 `seq` 当 key，
    # 而那一天屏幕上会少掉一句话。
    for said in ("你好", "fff"):
        ran = client.post(f"{base}/chats/{broke_id}/turn", json={"chapter": 2, "said": said})
        assert ran.status_code == 200, ran.text
        assert ran.json()["reason"] == "model_unreachable", ran.text
    grab("chatDetailFailed", client.get(f"{base}/chats/{broke_id}"))

    # ── 一次**没跑成**的整理（2026-08-13）────────────────────────────────────
    #
    # **这份夹具里从来没有过一次失败的抽取**——三条 run 全是成功的，于是「失败了屏幕上
    # 说什么」这条路径在两个运行时的守卫下都是绿的，而它真出过事：日志页上曾经摆着
    # `provider_failure：chapter analysis provider failed`（`activity._RUN_ERROR_LABEL`
    # 记着现场）。那次只补了日志页那条读端；审阅面板读的是这一条，它把整个
    # `ExtractionRunError` 原样发出去，浏览器渲染的就是那句英文。**判据没错，样本缺了一半。**
    #
    # 种法走 `seed_run`（直接写表）：跑一次真抽取要模型、要钱，而它种的是**真形态**
    # ——`ExtractionErrorCode` 的真值 + `runner.py` 真写下的那句英文诊断。
    #
    # **放在最后**：它会往时间线和 `/runs` 里多加一行，前面每一个 grab 都不该看见它。
    # 落在第 3 章而不是第 1 章：一章一份快照一个 prompt 只有一条 run（库里有一条唯一约束），
    # 而第 1 章那条已经被上面那次成功的整理占着——**两份都要**，成功和失败在屏幕上是
    # 两块完全不同的界面。
    failed_run = seed_run(book, 3, status="FAILED")
    grab("extractionFailed", client.get(f"{base}/extractions/{failed_run}"))

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
