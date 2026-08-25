"""FastAPI 薄壳 —— 把现有引擎函数暴露成 HTTP，给「很单纯的」web 工作台用（ARCHITECTURE §4）。

**壳不装业务。** 出参已经全是 Pydantic（`SceneConstraints` /
`StateSnapshot` …），直接当 API response schema，FastAPI 自动序列化。读走 `panel/` +
`StoryGraph`，写走 `Ledger`（P2 再接）。

这一层是**装配层**（另一处是 `api/launch.py`，启动器）：`api/deps.py` 是全壳唯一开连接的地方，因此进了
`tests/test_arch_guard.py` 的 `CONNECTION_OPENERS`。路由文件 `app.py` 不碰连接——它收
`Depends(get_store)`，就像规则收 `CheckContext`、面板收 `store`。

跑：`NH_DB=<库> uv run python -m novel_harness.api` → 开 http://127.0.0.1:8756
"""
