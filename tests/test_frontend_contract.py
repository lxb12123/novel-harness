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

import seed

from novel_harness.db import connect
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

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
    # **环境变量也得清干净。** `/api/settings` 的 `continuation_tail_limit` 是从
    # 「设置页优先、环境变量兜底」那条链上算出来的（`_draft_provider_config`）——
    # 谁的 shell 里恰好有 `NH_LLM_MODEL`，冻出来的 fixture 就跟着他的模型变，
    # 这份夹具就不再是「后端在干净状态下吐的东西」了。
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
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
    grab(
        "createNode",
        client.post(f"{base}/nodes", json={"label": "Character", "name": "顾清音"}),
    )
    grab(
        "createAlias",
        client.post(f"{base}/aliases", json={"of": "顾清音", "surface": "顾姑娘"}),
    )

    # 下面几份读端夹具需要图里真有一条**已生效的边**（子图那条、底栏那个区间）。
    # **播种位置一定要留在原处**：挪到后面去，上面那几份夹具会安静地变成「没有边」的
    # 形状，而 vitest 那边只会红在一句莫名其妙的
    # `Spread types may only be created from object types`。
    # （2026-08-24 之前这儿播的是一条 KNOWS 边；秘密下线之后换成地点边，ADR 0039。）
    seed.where(
        book["db"], book["pid"],
        who="萧决", loc="青云城主府",
        quote="萧决在青云城主府第一次听说了血脉秘密的真相。",
    )

    # ── 读路径（声明之后：图里现在有一条已生效的边，前端渲染测试要的就是这个形状）──
    cast = {"cast": "萧决,李管家"}
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
    # **生成那一次不再走 HTTP**：手动生成那条路由 2026-08-25 随按钮一起删了
    # （总结只剩两个自动触发）。这儿直接调生产上那个执行体——同一个 `ensure`、
    # 同一份幂等、同一次真模型调用（下面用量条那一档指望的就是它）。
    # 夹具本身照旧是**真 dump**，只是从 `GET` 那条读端抓：前端拿的本来也是这一份。
    deps_mod.build_summarizer().ensure(pid, 1)
    grab("summaryGenerated", client.get(f"{base}/chapters/1/summary"))
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
    # 这儿原来先打一次 `/canon/knowledge`，好让日志里有一条**带真跳转坐标**的
    # `knowledge_edit`。那条路由随秘密下线删了（ADR 0039），带真坐标的那一条现在由
    # 下面事件名单那一次提供（`event_cast` 那一档）。

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
    activity_page = client.get(f"{base}/activity", params={"limit": 8})
    grab("activity", activity_page)
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
    # 花钱那一档的**第二种长相**：一次章节总结（上面 `summaryGenerated` 那次真调用）。
    # 它和抽取那一份差两样东西，两样都是 2026-08-13 补的：跳转坐标指着右栏那一格
    # （不再是兜底的「去第 N 章」），展开层多一行**它到底总结了什么**。
    # 只冻抽取那一份的话，这两样在浏览器那侧一行都没被渲染过。
    #
    # **按结构挑那一行，不按标题**（这一页的第一条禁令就是「别从字面反推」）：
    # 用的是**规范化之前**的响应，因为 `dump` 里的 id 已经换成 `call:IDn` 了，拿它发不出请求。
    summary_call = next(
        entry["id"]
        for entry in activity_page.json()["entries"]
        if (entry.get("jump") or {}).get("target") == "summary"
    )
    grab("activitySummaryDetail", client.get(f"{base}/activity/{summary_call}"))
    # 展开一条作者亲手点过的确认：`payload` 是这一层唯一的泄漏面，前端照它渲染信封。
    # **按 actor 找，不绑死某条路由**：这条夹具原来靠的是那次认知改正，而那条路由
    # 随秘密下线删了（ADR 0039）——它要的从来是「一条 author 行的信封形状」，
    # 不是「哪条路由写的那一行」。
    author_row = next(
        entry["id"] for entry in activity_page.json()["entries"] if entry["actor"] == "author"
    )
    grab("activityDecisionDetail", client.get(f"{base}/activity/{author_row}"))
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
    # ── 「这个人的事件」时间线（2026-08-25）──────────────────────────────────
    # **抓在改名单之前**：这一份要的是「一件事挂在几个人名下」的原样，
    # 改完名单之后那条事件少一个知情人，夹具就少一档长相。
    grab(
        "characterEvents",
        client.get(f"{base}/characters/{book['萧决']}/events"),
    )
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
    # 「师兄」→ 两个人。**换成 `/declare/death` 只是换了个载体**：这条夹具要的是
    # `ambiguous_name` 那个拒绝形状（前端有专门分支渲染它），而那套拒绝一个字没变。
    ambiguous = client.post(f"{base}/declare/death", json={"who": "师兄", "quote": "萧决"})
    assert ambiguous.status_code == 409, ambiguous.text
    dump["errorAmbiguousName"] = norm.walk(ambiguous.json())

    short = client.post(f"{base}/aliases", json={"of": "萧决", "surface": "决"})
    assert short.status_code == 422, short.text
    dump["errorShortAlias"] = norm.walk(short.json())

    # ── 改一条已生效事实的三种拒绝 ────────────────────────────────────────
    # 三种含义完全不同，界面上必须说三句不同的话，所以三种形状都得是契约的一部分：
    # 409 = 这本书在别处刚被改过（**不许静默重试**）；404 = 他点的那条今天不在了；
    # 422 = 这次改动本身讲不通。手写这三份等于两份手写的东西互相验证。
    #
    # **载体 2026-08-24 从 `/canon/knowledge` 换成了 `/canon/events/{id}/cast`**：
    # 前者随秘密下线删了，而这三种拒绝形状是同一套 `_correction_error` 映出来的，
    # 一个字节没变——换的是打哪条路由，不是这份契约本身。
    event_id = target["event"]["id"]
    stale = client.post(
        f"{base}/canon/events/{event_id}/cast",
        json={"knower_ids": kept, "expected_canon_version": 0},  # 作者手上那份是很久以前的
    )
    assert stale.status_code == 409, stale.text
    dump["errorStaleCanon"] = norm.walk(stale.json())

    fresh = client.get(f"{base}").json()["canon_version"]
    absent = client.post(
        # 这条事件今天不在了（界面上这一行根本不该能点，这份夹具冻的是「他还是点到了」那条退路）。
        f"{base}/canon/events/event:{'0' * 26}/cast",
        json={"knower_ids": kept, "expected_canon_version": fresh},
    )
    assert absent.status_code == 404, absent.text
    dump["errorFactNotFound"] = norm.walk(absent.json())

    refused = client.post(
        f"{base}/canon/events/{event_id}/cast",
        # 名单和现在的一模一样 —— 这次改动本身讲不通。
        json={"knower_ids": kept, "expected_canon_version": fresh},
    )
    assert refused.status_code == 422, refused.text
    dump["errorKnowledgeRefused"] = norm.walk(refused.json())

    # ── 写作助手的会话（模式二，ADR 0019）──────────────────────────────────
    # **只把模型换成桩**（同上面那一轮总结）：库、工具派发、记账、会话表全走真代码。
    # 换的那一处是 `build_agent_model`——它就是壳里那个注入点。
    import novel_harness.api.chat as chat_mod
    from novel_harness.draft.provider import ToolCall

    scripted_calls = {"n": 0}

    def _tool_results(messages: Any) -> list[dict[str, Any]]:
        return [
            json.loads(m["content"])
            for m in messages
            if m.get("role") == "tool" and str(m.get("content", "")).strip()
        ]

    def scripted_agent(messages: Any, *, tools: Any, cancel: Any) -> Any:
        # 校准 → 封存 → （起草 + 查约束）→ 说话收手。起草不落盘，回执上多一份候选
        # ——前端那一栏（「写了两稿，挑一个」）照的就是这份 fixture。
        scripted_calls["n"] += 1
        n = scripted_calls["n"]
        if n == 1:
            return CompletionResult(
                text="",
                model="deepseek-v4-flash",
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCall(
                        id="cal0",
                        name="calibrate_scene",
                        arguments='{"chapter": 2}',
                    ),
                ),
            )
        if n == 2:
            results = _tool_results(messages)
            inspection_id = [r["id"] for r in results if "id" in r][0]
            return CompletionResult(
                text="",
                model="deepseek-v4-flash",
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCall(
                        id="seal0",
                        name="seal_scene_brief",
                        arguments=json.dumps({"inspection_id": inspection_id}),
                    ),
                ),
            )
        if n == 3:
            results = _tool_results(messages)
            chapter, calibration_id = [
                (r["chapter"], r["calibration_id"])
                for r in results
                if "calibration_id" in r
            ][0]
            return CompletionResult(
                text="",
                model="deepseek-v4-flash",
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="scene_constraints",
                        arguments='{"chapter": 2}',
                    ),
                    ToolCall(
                        id="c2",
                        name="draft_chapter",
                        arguments=json.dumps(
                            {"chapter": chapter, "calibration_id": calibration_id},
                            ensure_ascii=False,
                        ),
                    ),
                ),
            )
        return CompletionResult(
            text="第 2 章这一场，血脉那条先别说破。我写了一稿，你看看要不要。",
            model="deepseek-v4-flash",
            finish_reason="stop",
            prompt_tokens=1_200,
            completion_tokens=64,
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
    scripted_calls["n"] = 0  # 第一段对话已经消费了四步，长连接那一段重新从校准开始。
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

    # ── 作者交代过的那些规矩：一张回头能翻的表（ADR 0028 + 迁移 016）───────────
    #
    # ⚠️ **「这一章的规矩」那四份夹具 2026-08-14 撤了**
    # （`chatRules` / `chatRulesExpired` / `chatRulesNone` / `chatRuleRevoked`）。
    # 它们喂的是写作助手顶上那颗按钮和它背后的面板，而那两条路由连同界面一起删了
    # （[ADR 0028](../docs/adr/0028-rules-expire-by-situation.md)：规矩不上屏，
    # 有效期由模型按情境判）。**引擎侧一个字没动**——规矩照旧被记下、照旧进 prompt，
    # 那条链由 `tests/test_agent_rules.py` 端到端钉着，不经过 HTTP。

    # 而 2026-08-15 长出来的是**另一件事**：`GET /projects/{pid}/rules`——不是控件，
    # 是记录（「我到底跟它交代过什么」）。它得有一份非空的真夹具，否则前端那张表
    # 只在空态下被验过，而空态是它最不容易出错的那一档。
    #
    # **另开一段**（同上面那条理由）：这一轮会往历史里加东西，跑在前面那两段上会把
    # 已经抓好的夹具推着走。
    ruled = client.post(f"{base}/chats", json={"title": "沙地那一段"})
    assert ruled.status_code == 201, ruled.text
    ruled_id = ruled.json()["id"]

    def remembers(*rules: tuple[str, str]) -> Any:
        """一轮：模型先叫几次「记下来」（每次连时效一起交），再说一句话收手。"""
        script = [
            CompletionResult(
                text="",
                model="deepseek-v4-flash",
                finish_reason="tool_calls",
                tool_calls=tuple(
                    ToolCall(
                        id=f"r{i}",
                        name="remember_rule",
                        arguments=json.dumps(
                            {"rule": rule, "until": until}, ensure_ascii=False
                        ),
                    )
                    for i, (rule, until) in enumerate(rules)
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

    # 两条，**时效写法故意不一样**：一条挂在剧情上、一条挂在结构上。
    # 照一种写出来的那一列（比如以为它总是「第 N 章」）会在另一种面前当场崩。
    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda c, p, m=remembers(
            ("男主在这片沙地不杀人", "男主走出这片沙地为止"),
            ("冷一点", "这一场写完"),
        ): m,
    )
    ran = client.post(
        f"{base}/chats/{ruled_id}/turn",
        json={"chapter": 2, "said": "这片沙地里别让他杀人，整体也冷一点。"},
    )
    assert ran.status_code == 200, ran.text
    grab("recordedRules", client.get(f"{base}/rules"))
    # 自定义确定性规则（024 / Task 13）：R2/R3 常驻显示 + 一条作者规则。
    grab("validationRules", client.get(f"{base}/validation-rules"))
    vr_created = client.post(
        f"{base}/validation-rules",
        json={"title": "不许有玄铁令", "literal": "玄铁令", "blocks_downstream": True},
    )
    assert vr_created.status_code == 200, vr_created.text
    grab("validationRuleCreated", vr_created)

    # ── 多版本的一章：版本抽屉的「还原 / 删除」只在有第二版时才存在 ──────────
    # **放在最后**：这一步会改第 2 章的正文，前面每一个 grab 都不该看见它。
    # `chapterHistory` 那份只有一版（导入即当前），照它写出来的界面在真实的两版面前
    # 是没被验过的——所以这里真存一次，冻的是「有历史可还原」那个形态。
    two_versions_body = client.get(f"{base}/chapters/2/text").json()
    two_versions = two_versions_body["markdown"]
    saved_again = client.put(
        f"{base}/chapters/2/text",
        json={
            "markdown": two_versions + "\n后来又添了一段。\n",
            "expected_text_sha256": two_versions_body["text_sha256"],
        },
    )
    # 保存的回执也冻住：还原走的就是这条 PUT，测试桩得照它的真形状答话。
    grab("chapterSaved", saved_again)
    grab("chapterHistoryTwo", client.get(f"{base}/chapters/2/history"))

    # ── 书架：一个库里可以有多本书（`bootstrap` 往当前库里加项目）───────────
    # `projects` 那份是**建第二本之前**的状态，只有一本；侧栏的书架、切书弹窗、
    # 「从侧栏移除」全都只在两本以上时才存在形态，照一本写的界面等于没验过。
    grab("projectsTwo", client.get("/api/projects"))

    # ── 用量条第一档：**这本书每一次调用，用量和价钱都算得出来** ────────────────
    # 三档一次到齐（同上面 `summaries` 那一轮的理由）：夹具里只躺一种形状的样本，
    # 屏幕守卫扫的就是一块永远长一个样的屏幕。这一档落在第二本书上不是取巧——
    # 第一本从那次总结起就再也回不到「全报了」，而作者接 OpenAI 那四条路由时
    # 天天看见的正是这一屏。
    #
    # **`cost` 是 2026-08-13 加的**：在那之前这份夹具里一条带价钱的调用都没有，
    # 于是底栏「花费」那一格只可能渲染成「未记录」——它的另外两种长相
    # （算得出 / 只算得出一部分）在两个运行时的守卫下都没被看过。
    seed_call({**book, "pid": second_pid}, capability="summarizer", cost=0.34)
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
    # 单章保存（Task 2）不再靠整本 sync 顺带索引别的章，所以第 3 章要在这里显式
    # 索引一次——它躺在磁盘上（`check` 用的），`seed_run` 需要它的快照当锚。
    indexed = client.post(f"{base}/sync")
    assert indexed.status_code == 200, indexed.text
    failed_run = seed_run(book, 3, status="FAILED")
    grab("extractionFailed", client.get(f"{base}/extractions/{failed_run}"))
    # **日志页上那半块屏幕同样从来没被冻过。** 上面那份 `activity` 里三条 run 全是成功的，
    # 而「没跑成的那一行长什么样」是这一页唯一需要作者动手的地方（2026-08-13 起它带着
    # 一颗真能点的「再整理一次」——`jump.target = extraction_retry`）。
    grab("activityFailed", client.get(f"{base}/activity", params={"limit": 8}))
    grab("activityFailedDetail", client.get(f"{base}/activity/{failed_run}"))

    # ── 花费那一格的第三种长相：**算得出的只有一部分** ────────────────────────
    # 这本书此刻有三次调用（抽取的桩 / 那次真总结 / 下面这一条），只有一条填了价钱。
    # 合计和「其中几条算得出」必须一起摆，否则那个合计是一句看起来确定的假话
    #（`CostTotals.priced_calls` 那段注释）。**放在最后**：它会改 `/runs` 的合计，
    # 前面每一个 grab 都不该看见它。
    seed_call(book, cost=0.34)
    grab("runsPartlyPriced", client.get(f"{base}/runs"))

    # ── 作者在 WPS 里改完稿子，回到工作台点「读回改动」（2026-08-13）──────────
    #
    # `POST …/sync` 从 M1.5 起就在后端，而**浏览器里零调用方**：正文他看得见（章目录
    # 和正文都直接扫磁盘），可 `locate` 搜的是库里的快照——不跑这一下，他刚写的那句话
    # 选中之后会被告知「找不到」，而他的选择没有任何问题。
    #
    # **绕开 HTTP 直接写盘**，因为那正是作者干的事（WPS / VSCode / 手机）：
    # 走 `PUT …/text` 的话后端自己就 sync 了，冻下来的会是「什么都没变」那一档，
    # 而这条路由存在的全部理由就在「变了」那一档上。
    #
    # **放在最后**：它把第 2 章改了、把只在磁盘上的第 3 章落进库，前面每一个 grab
    # 都不该看见这些。
    root = Path(client.get(base).json()["root_path"])
    (root / "chapters" / "0002.md").write_text(
        (root / "chapters" / "0002.md").read_text(encoding="utf-8-sig") + "\n他在灯下改了这一段。\n",
        encoding="utf-8",
    )
    # 作者自己的东西（大纲、笔记）**不是错误**，回执要说得出「没动它们」。
    (root / "chapters" / "大纲.md").write_text("三卷的走向。\n", encoding="utf-8")
    grab("sync", client.post(f"{base}/sync"))
    # 再点一次：**「读了一遍，没有变化」和「读回来了 N 章」是两句不同的话**，
    # 而作者按这颗按钮时绝大多数时候落在前一档上。只冻后一档等于只验了一半。
    grab("syncUnchanged", client.post(f"{base}/sync"))

    # ── 章标之前躺着一整章那一份（2026-08-13）────────────────────────────────
    #
    # 在它之前这份夹具里唯一一次导入的 `preamble_chars` 是 0，于是「整本书的章号可能
    # 错一位」那块警告在 pytest 和 vitest 两侧扫的都是一块永远干净的屏幕——**而它是
    # 全书唯一一个「整本都错了」的早期信号**（门槛和它两边的余量写在
    # `api/manuscript.py::PREAMBLE_ALARM_CHARS`）。同 `extractionFailed` 那条的理由：
    # 正常数据下永远不亮的分支，正是它躲过守卫的方式。
    #
    # **放在最后**：它会往这个库里加第三本书，`projects` / `projectsTwo` 都不该看见。
    grab(
        "bootstrapPreamble",
        client.post(
            "/api/projects/bootstrap",
            json={
                "mode": "import",
                "name": "序章样书",
                # 第一段没有章标（切章器认的是行首的「第N章/节/回」），所以它整段落在
                # preamble 里。凑够门槛靠重复——**长度是判据，内容不是**。
                "text": "楔子\n\n" + ("风雪压着青云城的檐角。" * 100) + "\n\n第一章 起\n\n正文。\n",
            },
        ),
    )

    # ── 在一格「不知道」上**补**一条（2026-08-14）────────────────────────────────
    #
    # 抽取写得出 `event_knower`，而作者想手工补一条时没有路——右栏那条产品规则
    # （每一格「LLM 无感生成 + 作者可改**可增**」）差的就是这一半。

    # ── 系统通知（Task 10 / 022）：读列表 / count / 忽略 全走真服务 ──────────
    # 直接往通知 outbox 塞一条再物化（走真 `materialize_notification_outbox`），
    # 然后冻三条读端。**放在最末**：它不会往图里加东西，不影响上面任何夹具。
    from novel_harness.graph import TextAnchor
    from novel_harness.system_notifications import (
        background_failure_dedupe_key,
        enqueue_notification,
        enqueue_text_advisory,
        materialize_notification_outbox,
    )

    _notif_conn = connect(book["db"])
    try:
        _notif_pid = pid
        _notif_key = background_failure_dedupe_key(
            kind="summary_mismatch", subject_type="chapter", subject_id=book["萧决"],
            operation="reconcile", source_snapshot_id=None, job_id="job:contract",
        )
        _notif_conn.execute("BEGIN IMMEDIATE")
        enqueue_notification(
            _notif_conn,
            project_id=_notif_pid,
            kind="summary_mismatch",
            subject_type="chapter",
            subject_id=book["萧决"],
            chapter_number=1,
            # `summary_mismatch` 今天没有真的生产调用方（`NotificationKind` 里
            # 列着，但没有代码真的 enqueue 它）——这儿本来就是手搭的夹具数据，
            # 不是在复刻某个真实码，用占位码只是为了让契约测试跑得动这个形状。
            title_code="test_notice",
            title_params={"chapter": 1},
            dedupe_key=_notif_key,
        )
        # 第二条：**带锚**、且**只告警不阻断**的那一档（026）。两件事前端都要渲染，
        # 而上面那条 `summary_mismatch` 的 `jump` 是 null——只冻它，那两支渲染分支
        # 在夹具里就永远是暗的。
        _notif_chapter = next(
            ct.chapter_id
            for ct in SqliteStoryGraph(_notif_conn).current_snapshots(_notif_pid)
            if ct.number == 1
        )
        enqueue_text_advisory(
            _notif_conn,
            project_id=_notif_pid,
            chapter_id=_notif_chapter,
            chapter_number=1,
            title_code="clash_title",
            title_params={"sentence": 2, "chapter": 1, "conflict": "knowledge", "rest": 0},
            dedupe_key=background_failure_dedupe_key(
                kind="text_advisory", subject_type="chapter", subject_id=_notif_chapter,
                operation="secret_spoken", source_snapshot_id=None, job_id="job:contract",
            ),
            jump=TextAnchor(para_index=1, quote_text="萧决", occurrence_k=0),
        )
        _notif_conn.commit()
        materialize_notification_outbox(_notif_conn, project_id=_notif_pid, lease_owner="contract")
        _notif_conn.commit()
    finally:
        _notif_conn.close()
    grab("notifications", client.get(f"{base}/notifications"))
    grab("notificationsCount", client.get(f"{base}/notifications/count"))
    _nid = dump["notifications"][0]["id"]
    grab("notificationsIgnored", client.post(f"{base}/notifications/{_nid}/ignore"))

    # ── 人物基础信息 + 别名生命周期（Task 11 / §6.5）────────────────────────
    # 全走真服务。放在这里：它 bump canon version + 加一条 alias，别的夹具要的
    # 恰好是「加之前」的形状。
    grab(
        "characterProfile",
        client.get(f"{base}/characters/{book['萧决']}/profile"),
    )
    _ac = connect(book["db"])
    try:
        _canon_before = _ac.execute(
            "SELECT canon_version FROM project WHERE id = ?", (pid,)
        ).fetchone()[0]
    finally:
        _ac.close()
    alias_create_resp = client.post(
        f"{base}/characters/{book['萧决']}/aliases",
        json={"surface": "魔尊", "expected_canon_version": _canon_before},
    )
    assert alias_create_resp.status_code == 200, alias_create_resp.text
    grab("aliasCreated", alias_create_resp)
    alias_id = alias_create_resp.json()["id"]
    alias_reassign_resp = client.post(
        f"{base}/aliases/{alias_id}/reassign",
        json={
            "to_character_id": book["李管家"],
            "expected_canon_version": client.get(base).json()["canon_version"],
        },
    )
    assert alias_reassign_resp.status_code == 200, alias_reassign_resp.text
    grab("aliasReassigned", alias_reassign_resp)
    _alias2 = alias_reassign_resp.json()["id"]
    alias_edit_resp = client.patch(
        f"{base}/aliases/{_alias2}",
        json={
            "surface": "魔尊（北荒）",
            "expected_canon_version": client.get(base).json()["canon_version"],
        },
    )
    assert alias_edit_resp.status_code == 200, alias_edit_resp.text
    grab("aliasEdited", alias_edit_resp)
    _alias3 = alias_edit_resp.json()["id"]
    grab(
        "aliasDeleted",
        client.delete(f"{base}/aliases/{_alias3}"),
    )

    # ── 自动 Canon 边的纠错（Task 8 / ADR 0032）─────────────────────────────
    # 种法走抽取 ingest + `promote_clean_facts`（真代码），不手写：要的是
    # 「`source=extractor`、CANON、FRESH evidence」那种真形态——作者在日志页
    # 点到「去改这条自动生成的边」时面对的正是它。
    #
    # **放在最末**：它会往图里加一条 CANON 边、把 canon 版本推高几格、写
    # decision log，而上面 `matrix` / `characterState` / `subgraph` 三份夹具
    # 冻的正是「还没有这条边」的形状。
    from novel_harness.extract import (
        RawChapterAnalysis,
        RawCharacterProfile,
        RawEvent,
        RawStateUpdate,
    )
    from novel_harness.extract.auto_canon import promote_clean_facts
    from novel_harness.extract.service import ExtractionService

    _edge_conn = connect(book["db"])
    try:
        _edge_store = SqliteStoryGraph(_edge_conn)
        _chapter_row = _edge_conn.execute(
            "SELECT c.id AS chapter_id, s.id AS snapshot_id, s.text AS text "
            "FROM chapter c JOIN chapter_snapshot s ON s.chapter_id = c.id "
            "WHERE c.project_id = ? AND c.number = 1 AND s.text_sha256 = c.text_sha256",
            (pid,),
        ).fetchone()
        from novel_harness.graph import ChapterText

        _edge_report = ExtractionService(
            conn=_edge_conn,
            graph=_edge_store,
            event_store=SqliteEventStore(_edge_conn),
            proposal_store=SqliteProposalStore(_edge_conn),
        ).ingest(
            pid,
            ChapterText(
                chapter_id=_chapter_row["chapter_id"],
                number=1,
                snapshot_id=_chapter_row["snapshot_id"],
                text=_chapter_row["text"],
            ),
            RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="李管家在青云城主府听到了血脉秘密的真相。",
                        quote="萧决在青云城主府第一次听说了血脉秘密的真相。",
                        participants=("李管家",),
                        knowers=("李管家",),
                        confidence=0.95,
                    ),
                ),
                state_updates=(
                    RawStateUpdate(
                        kind="location",
                        subject="李管家",
                        object="青云城主府",
                        quote="萧决在青云城主府第一次听说了血脉秘密的真相。",
                        confidence=0.95,
                    ),
                ),
                # **两个人物、两种长度**：角色册那一格要按累计信息量排序，
                # 而全 0 或全相同的样本两个方向渲染出来一模一样，测试永远绿。
                character_profiles=(
                    RawCharacterProfile(
                        surface="李管家",
                        gender="男",
                        background="青云城主府的老管家",
                        personality="谨慎",
                        confidence=0.9,
                    ),
                    RawCharacterProfile(surface="萧决", gender="男", confidence=0.9),
                ),
            ),
            prompt_hash="prompt:canon-edge-contract",
        )
        promote_clean_facts(
            _edge_conn, pid, _edge_report, graph=_edge_store, events=SqliteEventStore(_edge_conn)
        )
        _edge_conn.commit()
        _edge_id = _edge_conn.execute(
            "SELECT id FROM edge WHERE project_id = ? AND type = 'LOCATED_AT' "
            "AND information_scope = 'CANON' AND source = 'extractor' "
            "ORDER BY rowid DESC LIMIT 1",
            (pid,),
        ).fetchone()["id"]
    finally:
        _edge_conn.close()

    # ── 角色册那两列数：**抓第二份，抓在最后** ────────────────────────────────
    #
    # 上面那份 `roster` 抓在建书之后、总结和抽取之前，所以它每一行都是 0。
    # 那一份不能动（几十个组件测试吃着它），但**只有 0 的样本证明不了排序**：
    # 前端那一格要按累计信息量排序 + 一颗倒序切换，全 0 的话两个方向渲染出来
    # 一模一样，测试永远绿。
    #
    # **位置必须在这儿**：`appearance_chapters` 要等总结落地，`information_score`
    # 要等上面那次带画像的抽取跑完。往前挪一行，两列里就有一列回到全 0。
    grab("rosterWithCounts", client.get(f"{base}/roster"))

    grab("canonEdge", client.get(f"{base}/canon/edges/{_edge_id}"))
    edge_edit_resp = client.patch(
        f"{base}/canon/edges/{_edge_id}",
        json={
            "kind": "location",
            # 只改地点、不换人物：edge id 保持不变（props 投影 + override）。
            "location_id": book["北荒"],
            "expected_canon_version": client.get(f"{base}/canon/edges/{_edge_id}").json()[
                "canon_version"
            ],
        },
    )
    assert edge_edit_resp.status_code == 200, edge_edit_resp.text
    grab("canonEdgeEdited", edge_edit_resp)
    edge_edited_id = edge_edit_resp.json()["edge_id"]
    edge_retract_resp = client.delete(f"{base}/canon/edges/{edge_edited_id}")
    assert edge_retract_resp.status_code == 200, edge_retract_resp.text
    grab("canonEdgeRetracted", edge_retract_resp)
    # 撤回后的旧 ID 再改 → 409（客户端不能用旧 selection state 继续 PATCH）。
    refused_again = client.patch(
        f"{base}/canon/edges/{edge_edited_id}",
        json={
            "kind": "location",
            "location_id": book["北荒"],
            "expected_canon_version": edge_retract_resp.json()["canon_version"],
        },
    )
    assert refused_again.status_code == 409, refused_again.text
    dump["errorCanonEdgeGone"] = norm.walk(refused_again.json())
    # 自动升 CANON 那一条决策日志：`_decision_jump` 现在认得它（`edges` payload 里
    # 恰好一条 → `jump.target = canon_edge`）。日志页那一行必须真的带这条跳转——
    # 「自动升上去的边改得掉」正是 Task 8 的全部主张。**放在本节最后**：它抓的是
    # 刚发生的事，别的夹具都不该看见这条新决策。
    edge_activity = client.get(f"{base}/activity", params={"limit": 8})
    canon_edge_jump = next(
        (entry for entry in edge_activity.json()["entries"]
         if (entry.get("jump") or {}).get("target") == "canon_edge"),
        None,
    )
    assert canon_edge_jump is not None, "自动升边的决策没有带上 canon_edge 跳转"
    dump["activityCanonEdge"] = norm.walk(canon_edge_jump)

    # 「维度」下拉框（Task 8 补记）：这个项目里全部 StateDim 节点 + 一条指向自由维度
    # 的真实 HAS_STATE 边。**独立一次 ingest/promote**，不搭在上面那次 LOCATED_AT
    # 边的车：`_decision_jump` 只在一次决策**恰好改了一条边**时才认得出
    # `jump.target = canon_edge`（上面 `activityCanonEdge` 冻的正是这一形状），
    # 塞进同一批就会把这条边和上面那条边混进同一条决策日志，把「恰好一条」冲掉。
    # 这本书至今没有任何 death 事件，所以 `stateDims` 里只有一条、且 `dim_key`
    # 是 `None`——这正是「多数维度没有机器键」的真实形状，手写会放过它。
    _state_conn = connect(book["db"])
    try:
        _state_store = SqliteStoryGraph(_state_conn)
        _state_report = ExtractionService(
            conn=_state_conn,
            graph=_state_store,
            event_store=SqliteEventStore(_state_conn),
            proposal_store=SqliteProposalStore(_state_conn),
        ).ingest(
            pid,
            ChapterText(
                chapter_id=_chapter_row["chapter_id"],
                number=1,
                snapshot_id=_chapter_row["snapshot_id"],
                text=_chapter_row["text"],
            ),
            RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="萧决的境界忽然跌回了炼气期。",
                        quote="萧决在青云城主府第一次听说了血脉秘密的真相。",
                        participants=("萧决",),
                        knowers=("萧决",),
                        confidence=0.95,
                    ),
                ),
                state_updates=(
                    RawStateUpdate(
                        kind="state",
                        subject="萧决",
                        dimension="武功境界",
                        value="炼气期",
                        quote="萧决在青云城主府第一次听说了血脉秘密的真相。",
                        confidence=0.95,
                    ),
                ),
                character_profiles=(),
            ),
            prompt_hash="prompt:canon-edge-contract-state",
        )
        promote_clean_facts(
            _state_conn, pid, _state_report, graph=_state_store, events=SqliteEventStore(_state_conn)
        )
        _state_conn.commit()
        _state_edge_id = _state_conn.execute(
            "SELECT id FROM edge WHERE project_id = ? AND type = 'HAS_STATE' "
            "AND information_scope = 'CANON' AND source = 'extractor' "
            "ORDER BY rowid DESC LIMIT 1",
            (pid,),
        ).fetchone()["id"]
    finally:
        _state_conn.close()

    grab("stateDims", client.get(f"{base}/canon/state-dims"))
    grab("canonEdgeState", client.get(f"{base}/canon/edges/{_state_edge_id}"))

    # 角色卡的红点（034 补记）：一件事挂两个新人物，删掉其中一个，
    # 剩下那个人身上这条事件该带一条非空 `cast_changed`。
    #
    # **不用「李管家」**：他是前面别名重指等夹具的真实主体，且被一条
    # proposal 确认链引用着——SQLite 那条「confirmation source links are
    # immutable」的完整性约束会拒绝删除。真书里这类人本来就删不掉（同一条
    # 约束保护的是审计链，不是这条测试的 bug），所以这里另建两个干净的人物，
    # 不去踩一个已经被别的夹具用掉的节点。
    #
    # **放在本节最末**：这是删除，前面所有夹具都已经拿到它们要的样子，
    # 不会被这一下影响。
    from novel_harness.declare import Ledger
    from novel_harness.graph import NodeLabel

    _cast_conn = connect(book["db"])
    try:
        _cast_store = SqliteStoryGraph(_cast_conn)
        _ledger = Ledger(_cast_store, _cast_conn, pid)
        _stayer = _ledger.declare_node(NodeLabel.CHARACTER, "沈知微").id
        _leaver = _ledger.declare_node(NodeLabel.CHARACTER, "沈知微的师弟").id
        _cast_conn.commit()
        _chapter_row2 = _cast_conn.execute(
            "SELECT c.id AS chapter_id, s.id AS snapshot_id, s.text AS text "
            "FROM chapter c JOIN chapter_snapshot s ON s.chapter_id = c.id "
            "WHERE c.project_id = ? AND c.number = 1 AND s.text_sha256 = c.text_sha256",
            (pid,),
        ).fetchone()
        _cast_report = ExtractionService(
            conn=_cast_conn,
            graph=_cast_store,
            event_store=SqliteEventStore(_cast_conn),
            proposal_store=SqliteProposalStore(_cast_conn),
        ).ingest(
            pid,
            ChapterText(
                chapter_id=_chapter_row2["chapter_id"],
                number=1,
                snapshot_id=_chapter_row2["snapshot_id"],
                text=_chapter_row2["text"],
            ),
            RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="沈知微和师弟一起发现了血脉秘密的真相。",
                        quote="萧决在青云城主府第一次听说了血脉秘密的真相。",
                        participants=("沈知微", "沈知微的师弟"),
                        knowers=("沈知微", "沈知微的师弟"),
                        confidence=0.95,
                    ),
                ),
                state_updates=(),
                character_profiles=(),
            ),
            prompt_hash="prompt:cast-changed-contract",
        )
        promote_clean_facts(
            _cast_conn, pid, _cast_report, graph=_cast_store, events=SqliteEventStore(_cast_conn)
        )
        _cast_conn.commit()
    finally:
        _cast_conn.close()

    delete_resp = client.delete(
        f"{base}/nodes/{_leaver}",
        params={"expected_canon_version": client.get(base).json()["canon_version"]},
    )
    assert delete_resp.status_code == 200, delete_resp.text
    grab(
        "characterEventsCastChanged",
        client.get(f"{base}/characters/{_stayer}/events"),
    )

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
