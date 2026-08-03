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

from test_api import (
    _seed_low_confidence_proposal,
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

    # ── M4：提案审阅 / 被动确认（seed 走存储层，审阅动作走真 API）─────────────
    m4_proposal_id, m4_event_id, m4_base = _seed_low_confidence_proposal(book)
    m4_confirm_event = _seed_provisional_event(book)
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

    # ── 拒绝形态：前端有专门分支渲染它们，同样是契约 ────────────────────────
    ambiguous = client.post(
        f"{base}/declare/knows", json={"who": "师兄", "secret": "血脉秘密", "quote": "萧决"}
    )
    assert ambiguous.status_code == 409, ambiguous.text
    dump["errorAmbiguousName"] = norm.walk(ambiguous.json())

    short = client.post(f"{base}/aliases", json={"of": "萧决", "surface": "决"})
    assert short.status_code == 422, short.text
    dump["errorShortAlias"] = norm.walk(short.json())

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
