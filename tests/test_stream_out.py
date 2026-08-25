"""对抗性验证：**事件流第一次真的离开服务端**（ADR 0024 第二刀）。

这份文件不重复 `tests/test_chat_stream.py` 已经量过的东西（帧种类序列、两条路由不漂、
4xx 在第一个字节之前）。它只问四个那一刀的自述**没有量到底**的问题：

1. **那张泄漏网是不是在空集上转。** 「跑完一轮，把整条流拼起来搜毒」这句话本身
   一个字都不假，**但它绿的原因可能是毒根本没进过工具返回**。所以这儿先证明料喂满了
   ——同一轮里第二次模型调用收到的那份 payload 里**真的**躺着 `secret:…` / 正文原句
   ——**再**去搜那条连接。3.2 那次的泄漏网就是漏了前半句。
2. **模型编出来的字有没有一条路走上作者的屏幕。** 工具名是模型打进来的，而
   `TurnEvent.said_to_author` 是引擎写的中文——两者在 `tool_started` 那一声里碰头。
3. **掉线的浏览器到底带不带走那一轮**（这一刀的核心承诺，也是它选 SSE 而不是
   WebSocket 的第一条理由）。分两档，**结局完全不同**：流中途断掉 / **第一个字节
   都还没出去就断**。后一档 2026-08-12 当天实测挖出**两个**都会真的伤到作者的错，
   两条都在下面（一条把那段对话永久废掉，一条把整个进程 segfault 掉）。
4. **已经花掉的钱有没有留在账上。**

**这份文件里那两条最重的断言，写下来的时候是红的**（`…must_not_brick_the_conversation`
和 `…holds_a_connection_of_its_own…`）。修法落在 `api/chat.py` 的
`_turn_frames` / `stream_chat` 上，两条各自的 docstring 写着是什么、为什么。

**这儿一条 `TestClient` 的 SSE 断言都不写增量投递**：那一条已经有人用真 ASGI 出口
量过了（`test_the_frames_really_leave_the_server_before_the_turn_is_over`），
而 `TestClient` 会把流整个攒起来，在这上面写「边跑边发」的断言是永远绿的。
本文件用 `TestClient` 的地方问的都是「整条流里有没有 X」——那个问题它答得准。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.api.chat as chat_mod
from novel_harness.db import connect
from novel_harness.draft.provider import CompletionResult

from test_chat_api import Scripted, open_chat, says, use, wants


@pytest.fixture(autouse=True)
def _isolate_running_turns() -> Any:
    chat_mod.LIVE.clear()
    yield
    chat_mod.LIVE.clear()


@pytest.fixture
def configured(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 BYOK 指到一个登记过的端点（同 `test_chat_api.py` / `test_chat_stream.py`：
    本仓的做法是各写一份小夹具，import 过来会被同名参数遮住、ruff 当场判 F811）。"""
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


# 第 1 章正文里那一句。**它只在磁盘上和 `chapter_text` 的返回里存在**，
# 所以它出现在别处就是「读回来的东西被摊开了」。
CH1_LINE = "李管家什么也没说。"


def stream_turn(client: TestClient, pid: str, chat_id: str, **body: Any) -> str:
    response = client.post(f"/api/projects/{pid}/chats/{chat_id}/turn/events", json=body)
    assert response.status_code == 200, response.text
    return response.text


def frames(body: str) -> list[tuple[str, Any]]:
    """把一条 `text/event-stream` 拆成 `(帧名, 载荷)`。**有意手写**，不 import
    被测者自己的编码器——编码错了两边一起错，那条断言就永远绿着。"""
    out: list[tuple[str, Any]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name = ""
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data.append(line[len("data: ") :])
        out.append((name, json.loads("\n".join(data))))
    return out


def sent_to_the_model(model: Scripted, call: int) -> str:
    """第 `call` 次模型调用收到的那份 message 数组，序列化成一段字。

    **这就是「引擎交给模型什么」的全部**——工具返回原样躺在里面。拿它当泄漏网的
    「料喂满了没有」那一侧，比去库里翻 `chat_message` 更准：库里那份是**存下来的**，
    而这一份是**发出去的**，两者哪一份带毒都算数，但只有后一份能证明这一轮里
    引擎手上真的握着那段话。
    """
    return json.dumps(model.calls[call], ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════
# 一、边界一：**先证明料喂满了**，再去搜那条连接
# ══════════════════════════════════════════════════════════════════════════


def test_the_wire_is_clean_and_the_poison_was_really_there_to_leak(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一本带毒的书跑完整一轮：**毒确实进了工具返回，而那条连接上一个字都没有。**

    ── 为什么前半句必须是断言，而不是一句注释 ────────────────────────────────

    「跑一轮，把整条流拼起来搜毒」是一条**只会在真的漏了的时候红**的断言——但它
    **绿的时候有两种原因**：网罩住了，或者**毒根本没被喂进来**。后一种在这个仓库
    发生过（3.2 那次的泄漏网在空集上转了一整轮）。工具层今天已经把 `props.twist` /
    `plot_note` 收窄掉了（`TWIST` / `PLOT_NOTE` 一个字都到不了工具返回），所以
    **照着那两个常量去搜事件流的网是空转的**——真正到得了这一层的毒是另外三样：
    `NodeRef` 的裸 id、库字段名、和读回来的正文原句。

    所以这条断言分两半，缺一半就没有意义：

    | | 断言什么 | 它红的时候说明 |
    |---|---|---|
    | 前半 | 第二次模型调用收到的 payload 里**有**那三样 | 这一轮根本没查到东西 ⇒ 后半是空转 |
    | 后半 | 那条连接上**没有**那三样 | 边界一在事件流上破了（**不可回收**） |
    """
    pid = book["pid"]
    model = Scripted(
        wants(
            ("scene_constraints", json.dumps({"chapter": 2})),
            ("chapter_text", json.dumps({"chapter": 1})),
        ),
        says("知道了，这一场血脉那条先不说破。"),
    )
    use(monkeypatch, model)
    chat_id = open_chat(client, pid)
    body = stream_turn(client, pid, chat_id, chapter=2, said="查一下第 2 章能说什么")
    seen = frames(body)

    # ── 料喂满了没有（三条探针，全部是「这一轮真的发生过」的证据）─────────────
    turns = [payload for name, payload in seen if name == "turn"]
    assert len(turns) >= 4, f"这一轮只喊了 {len(turns)} 声 —— 下面那条搜毒断言在空集上转"
    finished = [t for t in turns if t["kind"] == "tool_finished"]
    assert [t["ok"] for t in finished] == [True, True], f"工具没真跑成：{finished}"
    receipt = next(payload for name, payload in seen if name == "receipt")
    assert receipt["lookups"] == 2

    # **毒真的在工具返回里** —— 也就是这一轮里引擎手上真的握着这几段话。
    handed_to_model = sent_to_the_model(model, 1)
    assert "forbidden_entities" in handed_to_model, "约束那一条没带回未来实体清单 —— 料不满"
    assert CH1_LINE in handed_to_model, "正文那一条没读回来 —— 料不满"

    # ── 那条连接上一个字都没有 ─────────────────────────────────────────────
    assert "secret:" not in body
    assert "must_not_reveal" not in body
    assert "forbidden_entities" not in body
    assert CH1_LINE not in body
    # 秘密的**名字**同理：它是 `NodeRef.name`，跟着裸 id 一起躺在工具返回里。
    assert "血脉秘密" not in body
    # 那个还没登场的人（`forbidden_entities` 里那一条）——说破它就是剧透。
    assert "未来大能" not in body


def test_the_wire_stays_clean_even_when_the_model_asks_for_the_manuscript_three_times(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**同一段正文读三遍**，事件流上仍然只有「读完了第 1 章的正文，好了」。

    这条和上面那条的差别是**它把「查了几次」这个数推到 3**：泄漏面是按次数长的，
    而一条「第一次不漏、第三次漏」的实现（比如某天有人给 `tool_finished` 加一个
    「这次读了多少字，顺便把开头几句摆出来」）会在上面那条单次的断言下绿着。
    """
    pid = book["pid"]
    model = Scripted(
        wants(
            ("chapter_text", json.dumps({"chapter": 1})),
            ("chapter_text", json.dumps({"chapter": 2})),
            ("chapter_text", json.dumps({"chapter": 1})),
        ),
        says("读完了。"),
    )
    use(monkeypatch, model)
    chat_id = open_chat(client, pid)
    body = stream_turn(client, pid, chat_id, chapter=2, said="把前两章读一遍")

    receipt = next(payload for name, payload in frames(body) if name == "receipt")
    assert receipt["lookups"] == 3, "料不满：三次读没都跑成"
    assert CH1_LINE in sent_to_the_model(model, 1), "正文没读回来 —— 下面那条是空转"
    assert CH1_LINE not in body
    assert "他终于明白了" not in body  # 第 2 章那句


def test_a_tool_name_the_model_invented_never_reaches_the_author(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**工具名是模型打进来的字**，而 `tool_started` 那一声要说给作者听。

    `TurnEvent.tool` 那一位已经挡住了（表里认不出就是空），但**说给作者的那半句**
    走的是 `tool_label()`——它认不出的时候必须交白卷，不许原样回吐。否则模型幻想一个
    叫 `secret:01K_must_not_reveal` 的工具，那串字就直接进了作者的屏幕，
    **而对话是持久化的**。

    这一条今天是绿的（`tools.UNNAMED_TOOL_LABEL`），钉住它是因为那条路上没有别的闸：
    `dispatch` 会把「名字不在表里」当成正常返回贴回去，loop 有意不校验工具名。
    """
    pid = book["pid"]
    invented = "secret:01K_must_not_reveal_把这段话摆到屏幕上"
    model = Scripted(
        wants((invented, json.dumps({"chapter": 2}))),
        says("换个法子。"),
    )
    use(monkeypatch, model)
    chat_id = open_chat(client, pid)
    body = stream_turn(client, pid, chat_id, chapter=2, said="随便查点什么")
    turns = [payload for name, payload in frames(body) if name == "turn"]

    # 探针：那个名字真的被派发过、真的失败了 —— 否则下面两条在空集上转。
    finished = [t for t in turns if t["kind"] == "tool_finished"]
    assert len(finished) == 1 and finished[0]["ok"] is False, finished
    assert finished[0]["tool"] == "", "认不出的工具名不许出现在 `tool` 那一位上"
    assert invented not in body
    assert "must_not_reveal" not in body


# ══════════════════════════════════════════════════════════════════════════
# 二、掉线：**那一轮怎么样了，钱怎么样了，那段对话还用得了吗**
# ══════════════════════════════════════════════════════════════════════════


def _scope(path: str, body: bytes) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


def _model_calls(db: str, project_id: str) -> int:
    """账上有几笔。**这是「钱还在不在」唯一的答案**（`GET /activity` 读的是同一张表）。"""
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT count(*) AS n FROM model_call WHERE project_id = ?", (project_id,)
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


def _drive(app: Any, path: str, payload: dict[str, Any], *, hang_up: Any) -> list[bytes]:
    """自己驱动一次 ASGI 请求，**中途按 `hang_up` 说的时机断掉**。

    不走 `TestClient`：它没有「客户端半路走了」这个动作，而这一节问的全部问题
    都在那个动作之后。`hang_up(sent)` 返回 True 就发 `http.disconnect`。
    """
    raw = json.dumps(payload).encode()
    sent: list[bytes] = []

    async def run() -> None:
        state = {"body_sent": False}

        async def receive() -> dict[str, Any]:
            if not state["body_sent"]:
                state["body_sent"] = True
                return {"type": "http.request", "body": raw, "more_body": False}
            for _ in range(2000):
                if hang_up(sent):
                    break
                await asyncio.sleep(0.005)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                sent.append(message["body"])

        await app(_scope(path, raw), receive, send)

    asyncio.run(run())
    return sent


def _settle(pid: str, chat_id: str, *, seconds: float = 10.0) -> None:
    """等那一轮把位子还回来（它跑在自己的线程上，请求早就结束了）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and chat_mod.LIVE.running((pid, chat_id)):
        time.sleep(0.02)


class _Lease:
    """一次「借连接」的记录：借的是谁、什么时候还的、还的那一刻那一轮跑完了没有。"""

    def __init__(self) -> None:
        self.taken = 0
        self.closed_while_turn_alive: list[bool] = []


def _watch_leases(monkeypatch: pytest.MonkeyPatch) -> tuple[_Lease, threading.Event]:
    """盯住**这条路由为那一轮借的那条连接**，以及那一轮跑完的那一刻。

    立旗立在 `go()` 返回之后，不是立在 `LIVE.end` 那一行：`go` 的 `finally` 还完位子
    之后**还要再读一次会话**（`chat.py` 里那句 `self._chat_store.get(...)`），
    而那正是 2026-08-12 实测里和 `conn.close()` 撞在一起的那一句。
    """
    lease = _Lease()
    turn_done = threading.Event()
    original_conn = chat_mod.get_conn
    original_go = chat_mod._TurnRun.go

    def borrowed() -> Any:
        lease.taken += 1
        inner = original_conn()
        conn = next(inner)
        try:
            yield conn
        finally:
            lease.closed_while_turn_alive.append(not turn_done.is_set())
            inner.close()

    def watched(self: Any, on_event: Any = None) -> Any:
        try:
            return original_go(self, on_event)
        finally:
            turn_done.set()

    monkeypatch.setattr(chat_mod, "get_conn", borrowed)
    monkeypatch.setattr(chat_mod._TurnRun, "go", watched)
    return lease, turn_done


def test_the_turn_holds_a_connection_of_its_own_and_returns_it_last(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这一刀最贵的那条缝**：那一轮活得比请求长，而它握着的连接跟着请求死。

    `api/deps.py::get_conn` 的纪律是「一请求一连接，**请求结束即关**」。以前它一直是
    安全的——每条路由都在请求内跑完。这一刀第一次让一段代码**故意活得比请求长**
    （「一个掉线的浏览器不该把作者已经付过钱的那一轮弄崩」，那句话本身是对的），
    却仍然让它握着请求那条连接。

    于是浏览器一走，`StreamingResponse` 收场 → FastAPI 拆依赖 → `conn.close()`，
    而那一轮还在同一条 sqlite3 连接上落库、记账、收尾读一次会话。
    **2026-08-12 实测这不是一个异常，是 `Fatal Python error: Segmentation fault`**
    ——两条栈分别停在 `agent/store.py::get` 和 `api/deps.py` 那句 `close()`。
    也就是：**关掉一个标签页，可以把作者的整个工作台打死**（桌面壳里就是「窗口没了」），
    而这一轮的钱已经花了。

    ── 这条断言为什么长这样 ────────────────────────────────────────────────

    直接去触发那个 segfault**不能当断言用**：它不报错，它把 pytest 打死。所以这儿量的
    是那条缝的**判据**——「这一轮跑 SQL 用的那条连接，是不是等它跑完了才还的」。
    两条：借过一条自己的（不是请求那条），且还的时候那一轮已经收场。
    修法（`stream_chat`）：路由自己 `next(get_conn())` 拿一条，由跑那一轮的线程
    在同一个 `finally` 里连位子一起还。
    """
    pid = book["pid"]
    lease, turn_done = _watch_leases(monkeypatch)
    use(
        monkeypatch,
        Scripted(
            wants(("scene_constraints", json.dumps({"chapter": 2}))),
            says("跑完了。"),
        ),
    )
    chat_id = open_chat(client, pid)
    body = stream_turn(client, pid, chat_id, chapter=2, said="查一下")

    # 探针：这一轮真的整条跑完了（否则下面两条在空集上转）。
    assert turn_done.wait(5)
    assert [name for name, _ in frames(body)][-1] == "receipt"

    assert lease.taken == 1, (
        "这条路由没给那一轮借一条自己的连接 —— 它用的是请求那条，"
        "而那条请求一结束就 close()，那时那一轮还在用它"
    )
    assert lease.closed_while_turn_alive == [False], (
        "那一轮自己的连接在它跑完之前就被还掉了 —— "
        "实测形态是整个进程 segfault（见这条测试的 docstring）"
    )


def test_the_turn_can_still_write_after_the_response_is_already_over(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """响应**先收场**，那一轮后收场：它写下去的东西必须还在。

    这是上面那条的产品形态。构造成「没有人读这条流」——真实来法是作者按下发送、
    第一个字节还没出去就把标签页关掉，那时 `StreamingResponse` 一次都不迭代那个生成器。
    响应当场结束、依赖当场拆完，而那一轮才刚开始跑。

    ⚠️ **这条测试在 2026-08-12 修之前不会红，它会 segfault**（复现方法就是这一条：
    把 `stream_chat` 借的那条连接换回 `Depends(get_conn)`）。所以它是**回归网**，
    不是那条缝的证据——证据在上面那条。
    """
    pid = book["pid"]
    _, turn_done = _watch_leases(monkeypatch)
    open_gate = threading.Event()

    class Lingers:
        """一轮里最长的那一段（真实里是起草那几十秒）。**响应早就没了。**"""

        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return wants(("scene_constraints", json.dumps({"chapter": 2})))
            open_gate.wait(5)
            return says("响应早就结束了，这句话还是要落库。")

    model = Lingers()
    use(monkeypatch, model)
    chat_id = open_chat(client, pid)

    original = chat_mod._turn_frames

    def nobody_reads(run: Any, lease: Any = None) -> Any:
        original(run, lease)  # 线程在这儿起（修法的另一半）
        return iter(())       # **一个 `next()` 都不给它** —— 响应当场收场

    monkeypatch.setattr(chat_mod, "_turn_frames", nobody_reads)
    response = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn/events",
        json={"chapter": 2, "said": "跑一个然后我就关掉"},
    )
    assert response.status_code == 200 and response.text == ""

    open_gate.set()
    assert turn_done.wait(10), "那一轮没跑完 —— 响应一收场它就没了"
    _settle(pid, chat_id)
    detail = client.get(f"/api/projects/{pid}/chats/{chat_id}").json()
    assert [m["text"] for m in detail["messages"]][-1] == "响应早就结束了，这句话还是要落库。"
    assert model.calls == 2


def test_a_dead_stream_does_not_take_the_turn_or_the_money_with_it(
    book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """浏览器在**流的中途**关掉：那一轮照跑、照落库、照记账，位子照还。

    这是这一刀选 SSE 而不是把「停」搬到 socket 上的第一条理由的前提——
    「socket 断了这一轮还在跑、还在花钱」。**那句话必须被量过**，否则它只是一句设计意图。
    """
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    pid = book["pid"]
    model = Scripted(
        wants(("scene_constraints", json.dumps({"chapter": 2}))),
        wants(("chapter_text", json.dumps({"chapter": 1}))),
        says("断了也把话说完。"),
    )
    use(monkeypatch, model)
    with TestClient(app) as boot:
        chat_id = open_chat(boot, pid)
    before = _model_calls(book["db"], pid)

    # 第一声「查完了」一出去就挂断
    _drive(
        app,
        f"/api/projects/{pid}/chats/{chat_id}/turn/events",
        {"chapter": 2, "said": "查一下"},
        hang_up=lambda sent: any(b"tool_finished" in chunk for chunk in sent),
    )
    _settle(pid, chat_id)

    assert not chat_mod.LIVE.running((pid, chat_id)), "位子没还回来"
    assert model.calls, "模型一次都没被叫到 —— 下面那几条在空集上转"
    # **那一轮跑到底了**：最后那句话在库里（作者刷新一次就看得见它）。
    with TestClient(app) as c:
        detail = c.get(f"/api/projects/{pid}/chats/{chat_id}").json()
    assert [m["text"] for m in detail["messages"]][-1] == "断了也把话说完。"
    # `pending_calls` 是空的 —— 这一轮不是「断在半路」，是**跑完了没人看**。
    assert detail["session"]["pending_lookups"] == 0
    # **钱留在账上。** 掉线不许让已经花掉的调用从日志页上消失。
    assert _model_calls(book["db"], pid) > before


def test_a_browser_that_leaves_before_the_first_byte_must_not_brick_the_conversation(
    book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作者按下发送、**第一个字节还没出去**就把标签页关了。

    ── 2026-08-12 实测的那个坏结局（这条测试写出来的时候它是红的）───────────────

    `_TurnRun.__init__` 在**构造的时候**就占了位（`LIVE.begin`）并把作者那句话落了库，
    而真正跑那一轮的线程要等到**生成器被第一次 `next()`** 才起。这两件事之间隔着
    整个 ASGI 层：客户端在这中间走掉的话，`StreamingResponse` 一次都不迭代那个生成器
    ⇒ 线程从没起过 ⇒ `_TurnRun.go` 的 `finally` 从没跑过 ⇒ **那个位子在这个进程的
    余生里都占着**。

    作者看到的形态（三步都实测过）：

    1. 再说一句 → **409「这段对话正在跑上一轮，等它停下来，或者按「停」」**；
    2. 按「停」→ **200「按你的意思停下了」**——一句彻头彻尾的假话，位子一动没动；
    3. 想删掉它 → **409「这段对话正在跑，先按「停」再删」**。

    也就是：界面请他做的那个动作**恰好是唯一不管用的那个**，而那段对话再也回不来了
    （除非重启进程）。这跟 `_Running` 自己那段 docstring 拒绝的东西是同一件事——
    「一个存在库里的 `status='RUNNING'` 会在进程崩掉之后永远卡在那儿」——只是这一次
    它卡在进程内，而卡住它的不是崩溃，是**关掉一个标签页**。

    修法：线程在 `_turn_frames` **被调用时**就起，不等第一次迭代。
    """
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    pid = book["pid"]
    model = Scripted(says("没人看也要把这一轮跑完。"))
    use(monkeypatch, model)
    with TestClient(app) as boot:
        chat_id = open_chat(boot, pid)

    # **一个字节都没等**：请求体给完就断。真服务器上这就是「作者按了发送就关掉页面」，
    # 而 `_TurnRun.__init__` 那几次落库给了它足够的窗口。
    _drive(
        app,
        f"/api/projects/{pid}/chats/{chat_id}/turn/events",
        {"chapter": 2, "said": "跑一个然后我就关掉"},
        hang_up=lambda sent: True,
    )
    _settle(pid, chat_id, seconds=5.0)

    with TestClient(app) as c:
        # 探针一：那句话真的落库了 —— 说明这一次**确实**走进了 `_TurnRun.__init__`，
        # 位子确实被占过。没有这一条，下面那几条会在「请求早就 404 了」的情形下假绿。
        detail = c.get(f"/api/projects/{pid}/chats/{chat_id}").json()
        assert "跑一个然后我就关掉" in [m["text"] for m in detail["messages"]]

        assert not chat_mod.LIVE.running((pid, chat_id)), (
            "位子没还回来 —— 这段对话从此永远回答「这段对话正在跑上一轮」"
        )
        # 探针二：那一轮**真的跑了**（掉线不许拿走它，同上一条测试的承诺）。
        assert model.calls, "线程从没起过 —— 那一轮在没人看的那一刻就消失了"

        # 作者能不能接着用这段对话：这三条是他手上全部的动作。
        again = c.post(
            f"/api/projects/{pid}/chats/{chat_id}/turn",
            json={"chapter": 2, "said": "我回来了"},
        )
        assert again.status_code == 200, f"再说一句被拒了：{again.text}"
        assert c.delete(f"/api/projects/{pid}/chats/{chat_id}").status_code == 200


def test_the_turn_runs_even_if_nobody_ever_reads_the_stream(
    book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**自守卫**：把上面那条修法的判据单独钉一次 —— 生成器一次都不迭代，那一轮照跑。

    上面那条走的是真 ASGI，它红的时候有好几种解释（Starlette 换了实现、断开的时机漂了）。
    这一条只碰这一层自己：拿到 `_turn_frames(...)` 之后**立刻扔掉**，
    然后问「那一轮跑了吗、位子还了吗」。**它红 = 线程又变回了懒起。**
    """
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    pid = book["pid"]
    done = threading.Event()
    model = Scripted(says("跑完了。"))
    use(monkeypatch, model)
    with TestClient(app) as boot:
        chat_id = open_chat(boot, pid)

    made: list[Any] = []
    original = chat_mod._turn_frames

    def catch(run: Any, lease: Any = None) -> Any:
        stream = original(run, lease)
        made.append(stream)
        done.set()
        # **一个 `next()` 都不给它** —— 返回一个空迭代器，`StreamingResponse` 收到的
        # 是「这一轮什么都没发」，而那一轮的死活不该跟着它。
        return iter(())

    monkeypatch.setattr(chat_mod, "_turn_frames", catch)
    with TestClient(app) as c:
        response = c.post(
            f"/api/projects/{pid}/chats/{chat_id}/turn/events",
            json={"chapter": 2, "said": "没人看这一轮"},
        )
    assert response.status_code == 200 and response.text == ""
    assert done.is_set() and made, "`_turn_frames` 根本没被叫到 —— 这条测试在测别的东西"

    _settle(pid, chat_id, seconds=5.0)
    assert not chat_mod.LIVE.running((pid, chat_id)), "没人读那条流，位子就永远还不回来"
    assert model.calls, "没人读那条流，那一轮就一次模型调用都没发生"


def test_the_stop_button_never_says_it_stopped_something_that_was_not_running(
    client: TestClient, book: dict[str, str], configured: None
) -> None:
    """**自守卫探针**：没在跑的时候按停，说的必须是「本来就没在跑」那一句。

    它钉的是上面那条的诊断链：位子泄漏之所以致命，是因为「停」在那种状态下会
    回一句 `stopped=true`「按你的意思停下了」——**一句假话**，而作者会照它理解。
    这条断言保证「没在跑」这一档的措辞是对的，于是那条测试红的时候，
    「停」那一句为什么会撒谎就只剩一个解释：位子被占着而没有人在跑。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    response = client.post(f"/api/projects/{pid}/chats/{chat_id}/stop", json={})
    assert response.status_code == 200
    assert response.json() == {
        "chat_id": chat_id,
        "stopped": False,
        "message": "这段对话这会儿没在跑，不用停。",
    }


# ══════════════════════════════════════════════════════════════════════════
# 三、这条管子上还剩什么没被钉住
# ══════════════════════════════════════════════════════════════════════════


def test_the_frame_names_on_the_wire_are_a_closed_set(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一整轮下来，帧名只有那三个。**第四种帧要同时改浏览器那个解码器**，
    而它认不出的一律丢掉——也就是说，多出来的一种帧的症状是「界面少显示一行」，
    不是「报错」。所以这条断言必须在后端这一侧。"""
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(("scene_constraints", json.dumps({"chapter": 2}))),
            says("好了。"),
        ),
    )
    chat_id = open_chat(client, pid)
    seen = frames(stream_turn(client, pid, chat_id, chapter=2, said="查一下"))
    assert len(seen) >= 4
    assert set(name for name, _ in seen) <= {"turn", "receipt", "failed"}
    assert [name for name, _ in seen][-1] == "receipt"


def test_every_frame_is_one_line_of_data_so_the_decoder_cannot_be_fooled(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**载荷里不许有真的换行。** 帧的边界是空行，而一段带换行的 JSON 会把一帧劈成
    两块——浏览器那边解出来的是半个 JSON，症状是「一稿写到一半停住了，
    而屏幕上没有任何东西说它停了」。

    今天这条前提由 `model_dump_json()` 自己守着（JSON 把换行转义成 `\\n`），
    **但那是一句没有断言的前提**：哪天有人为了好读给帧加一个缩进，它当场就破。
    所以喂一段**带真换行的模型输出**跑一轮，逐帧数 `data:` 有几行。
    """
    pid = book["pid"]
    use(monkeypatch, Scripted(says("第一行\n第二行\n\n第四行")))
    chat_id = open_chat(client, pid)
    body = stream_turn(client, pid, chat_id, chapter=2, said="说点带换行的")

    for block in body.split("\n\n"):
        if not block.strip():
            continue
        lines = [line for line in block.split("\n") if line]
        assert sum(1 for line in lines if line.startswith("data: ")) == 1, block[:120]
    # 探针：那段带换行的字真的走完了这一轮（否则上面在数空块）。
    receipt = next(payload for name, payload in frames(body) if name == "receipt")
    assert receipt["reply"] == "第一行\n第二行\n\n第四行"


def test_a_turn_that_stops_to_ask_carries_the_question_on_both_the_wire_and_the_receipt(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """它停下来问了一句：**事件流上有一份，回执上也有一份，两份逐字相同。**

    两份都要，理由不一样：事件那一份让问题在这一轮**还没结束**的时候就摆上屏；
    回执那一份是持久的（作者三个月后回来，事件早就没了）。**两份不许漂**——
    漂了的形态是刷新一次页面，选项从三个变成两个。
    """
    pid = book["pid"]
    question = {"question": "这一场你想让萧决知道那件事吗？", "options": ["让他知道", "先瞒着"]}
    use(
        monkeypatch,
        Scripted(
            wants(("ask_author", json.dumps(question, ensure_ascii=False))),
            says("不该走到这一步。"),
        ),
    )
    chat_id = open_chat(client, pid)
    seen = frames(stream_turn(client, pid, chat_id, chapter=2, said="这一场怎么写"))

    asked = [p for name, p in seen if name == "turn" and p["kind"] == "asked_author"]
    assert len(asked) == 1, f"没问出来 —— 这条断言在空集上转：{[p['kind'] for _, p in seen]}"
    receipt = next(p for name, p in seen if name == "receipt")
    assert receipt["reason"] == "asked_author"
    assert receipt["asked"] == asked[0]["asked"] == question
    # **它问完就收场了**：后面不许再有别的动作（ADR 0024 的红字：不是模型收手，是代码收掉）。
    kinds = [p["kind"] for name, p in seen if name == "turn"]
    assert kinds[-1] == "turn_stopped"
    assert "draft_started" not in kinds


def test_the_authors_own_words_are_the_only_thing_the_engine_never_rewrites(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**引擎一个字都不加**：问句和选项原样出去（ADR 0024 那两条断言的第一条）。

    这一条量的是「够不着」而不是「不许」：`ask_author` 的内容 100% 是模型的字，
    所以喂一段**引擎绝不可能自己写出来的**内容进去，看它出来还是不是同一段。
    带上一对 `**` 是有意的——回执那句话里的 `**` 会被浏览器画成重音，
    而问句这一份**不该**被任何人重写。
    """
    pid = book["pid"]
    weird = {"question": "  要不要把 **那件事** 说破？  ", "options": ["说破", "说破", "先不说"]}
    use(
        monkeypatch,
        Scripted(
            wants(("ask_author", json.dumps(weird, ensure_ascii=False))),
            says("到不了这儿。"),
        ),
    )
    chat_id = open_chat(client, pid)
    seen = frames(stream_turn(client, pid, chat_id, chapter=2, said="问我一句"))
    receipt = next(p for name, p in seen if name == "receipt")
    assert receipt["reason"] == "asked_author", "料不满：它没问出来"
    # 前后空格没被 strip、重复的选项没被去重、`**` 没被吃掉、顺序没被排过。
    assert receipt["asked"] == weird


def test_the_receipt_frame_carries_no_message_the_projection_would_have_hidden(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回执那一帧里的 `messages` 走的是**同一个** `_visible`，所以工具返回那几条
    一样出不去。**这条不是重复上面的搜毒**：那一条量的是「事件上没有」，
    这一条量的是「最后那一帧上没有」——两处各有一份序列化，漏一处就够了。
    """
    pid = book["pid"]
    model = Scripted(
        wants(("chapter_text", json.dumps({"chapter": 1})), text="我去读一下。"),
        says("读完了。"),
    )
    use(monkeypatch, model)
    chat_id = open_chat(client, pid)
    seen = frames(stream_turn(client, pid, chat_id, chapter=2, said="读第 1 章"))
    receipt = next(p for name, p in seen if name == "receipt")

    assert CH1_LINE in sent_to_the_model(model, 1), "料不满：正文没读回来"
    speakers = [m["speaker"] for m in receipt["messages"]]
    assert set(speakers) <= {"author", "assistant"}
    assert all(CH1_LINE not in m["text"] for m in receipt["messages"])
    # 探针：那几条真的有内容（一份空 `messages` 会让上面两条永远绿）。
    assert receipt["messages"], "回执上一条消息都没有 —— 上面两条在空集上转"


def test_the_provider_wire_shape_is_untouched_by_the_stream(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**同一个剧本，两条路由发给模型的东西逐字节相同。**

    M2 判分链的那条硬约束是「不传 `tools` 时 provider 的 wire shape 逐字节不变」；
    这一层对应的形态是「**接不接事件流，模型收到的那份 payload 不许有半点差别**」。
    `agent/loop.py::EventFn` 把这句话写成了纪律，`_turn_frames` 是它今天唯一的消费者
    ——所以它必须在**这一层**被量一次，而不是只在引擎那一层。
    """
    pid = book["pid"]
    script = (
        wants(("scene_constraints", json.dumps({"chapter": 2})), text="先查一下。"),
        says("好了。"),
    )
    blocking = Scripted(*script)
    use(monkeypatch, blocking)
    chat_id = open_chat(client, pid)
    plain = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 2, "said": "查一下"},
    )
    assert plain.status_code == 200, plain.text

    streaming = Scripted(*script)
    use(monkeypatch, streaming)
    other = open_chat(client, pid)
    stream_turn(client, pid, other, chapter=2, said="查一下")

    assert len(blocking.calls) == len(streaming.calls) == 2, "料不满：两边步数就不一样"
    # 会话 id 不进 payload，所以两份 message 数组应当逐字节相同。
    assert json.dumps(blocking.calls, ensure_ascii=False) == json.dumps(
        streaming.calls, ensure_ascii=False
    ), "接上事件流之后，发给模型的那份 payload 变了 —— 「不传就逐字节不变」当场破"


def test_a_slow_reader_never_slows_the_turn_down(
    book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**慢的消费者不许拖慢这一轮**（`EventFn` 交代给适配器的第一件活：缓冲）。

    判据是因果不是挂钟：让第二次模型调用在被叫到的那一刻数一下「读走了几片」——
    队列在的时候它可以是 0（那一轮跑在自己的线程上，压根不等读者），
    而一个直接把 `on_event` 接到 `send` 上的实现在那一刻必然已经被读者拖住了。
    """
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    pid = book["pid"]
    read: list[bytes] = []

    class Counts:
        def __init__(self) -> None:
            self.calls = 0
            self.read_when_second_call_started = -1

        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return wants(("scene_constraints", json.dumps({"chapter": 2})))
            self.read_when_second_call_started = len(read)
            return says("好了。")

    model = Counts()
    use(monkeypatch, model)
    with TestClient(app) as boot:
        chat_id = open_chat(boot, pid)

    async def run() -> None:
        raw = json.dumps({"chapter": 2, "said": "查一下"}).encode()
        path = f"/api/projects/{pid}/chats/{chat_id}/turn/events"
        state = {"body_sent": False}

        async def receive() -> dict[str, Any]:
            if not state["body_sent"]:
                state["body_sent"] = True
                return {"type": "http.request", "body": raw, "more_body": False}
            await asyncio.Event().wait()
            raise AssertionError("到不了")

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                # **一个读得很慢的浏览器。** 引擎不许因此变慢。
                await asyncio.sleep(0.05)
                read.append(message["body"])

        await app(_scope(path, raw), receive, send)

    asyncio.run(run())
    assert model.calls == 2, "料不满：这一轮没跑到第二次调用"
    assert model.read_when_second_call_started >= 0
    assert model.read_when_second_call_started <= 2, (
        "第二次模型调用之前，这一轮已经等读者读走了 "
        f"{model.read_when_second_call_started} 片 —— 慢的浏览器正在拖慢作者的一轮"
    )
