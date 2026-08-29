"""长连接那一条（ADR 0024 第二刀）—— **把已经在手里的事件流接出去**。

第一刀（`50d4356`）让 `run_turn` 边跑边发事件了，但**没有任何东西在听**。
这份文件量的是那根线，以及它**没有**顺手绕过去的两道闸：

1. **两条路由不可能漂**：同一个剧本，`POST …/turn` 和 `POST …/turn/events`
   最后吐出来的 `TurnReceipt` 必须逐字段相同。它们本来就是同一个 `_TurnRun`，
   这条断言钉的是「以后也别把它们拆成两份实现」。
2. **边界一在这条管子上仍然成立**（ADR 0019 / 0024 都把它列成最贵的那一条）：
   喂一本带 `props.twist` 的书跑完整一轮，**把每一帧拼起来搜毒**——
   工具查到了什么一个字都不许出现在流上。第一刀已经在类型层堵了
   （`TurnEvent` 上没有一个字段装得下 `ToolOutcome.content`），这儿量的是
   **适配器没有从别处把它捞回来**。
3. **头发出去之前必须还能说 4xx**：模型没配好 / 这段对话正在跑 / 这段对话不在，
   三种都得是真状态码。发出去之后才被拒的那一档只剩一帧 `failed`，
   而**那句中文不许弄丢**。
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.api.chat as chat_mod
from novel_harness.agent.loop import TurnEventKind
from novel_harness.draft.provider import CompletionResult, ToolCall

from test_chat_api import Scripted, open_chat, says, use, wants


@pytest.fixture(autouse=True)
def _isolate_running_turns() -> Any:
    chat_mod.LIVE.clear()
    yield
    chat_mod.LIVE.clear()


@pytest.fixture
def configured(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 BYOK 指到一个**登记过**的端点（同 `test_chat_api.py`，本仓的做法是各写一份
    小夹具而不是跨文件 import ——import 过来会被同名参数遮住，ruff 当场判 F811）。"""
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


# ══════════════════════════════════════════════════════════════════════════
# 解帧 —— **测试这一侧只认 SSE 的规矩，不认后端那个 `_frame`**
# ══════════════════════════════════════════════════════════════════════════


def frames(body: str) -> list[tuple[str, Any]]:
    """把一条 `text/event-stream` 拆成 `(帧名, 载荷)`。

    **有意手写而不是 import `chat._frame`**：拿被测者自己的编码器去解自己的输出，
    编码错了两边一起错，这条断言就永远绿着。
    """
    out: list[tuple[str, Any]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name = ""
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):  # 注释帧（keep-alive），规范要求忽略
                continue
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data.append(line[len("data: ") :])
        out.append((name, json.loads("\n".join(data))))
    return out


def stream_turn(client: TestClient, pid: str, chat_id: str, **body: Any) -> Any:
    response = client.post(f"/api/projects/{pid}/chats/{chat_id}/turn/events", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return response


def kinds(seen: list[tuple[str, Any]]) -> list[str]:
    return [payload["kind"] for name, payload in seen if name == "turn"]


# ══════════════════════════════════════════════════════════════════════════
# 一、线通不通
# ══════════════════════════════════════════════════════════════════════════


def test_the_turn_says_what_it_is_doing_while_it_does_it(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一轮跑下来，**每一步的边界上都有一帧**，最后一帧是回执。

    这是这一刀的全部主张：今天那条阻塞路由跑三分钟只回一个东西，
    而这一条在同样三分钟里说了它在干什么。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(("scene_constraints", json.dumps({"chapter": 2})), text="我先查一下第 2 章。"),
            says("第 2 章这一场，血脉那条先别说破。"),
        ),
    )
    chat_id = open_chat(client, pid)
    seen = frames(stream_turn(client, pid, chat_id, chapter=2, said="能说破吗？").text)

    assert kinds(seen) == [
        TurnEventKind.REPLY_TEXT,
        TurnEventKind.TOOL_STARTED,
        TurnEventKind.TOOL_FINISHED,
        TurnEventKind.REPLY_TEXT,
        TurnEventKind.TURN_STOPPED,
    ]
    assert seen[-1][0] == "receipt"
    receipt = seen[-1][1]
    assert receipt["reason"] == "done"
    assert receipt["lookups"] == 1
    # **中间那些帧不是日志**：每一帧要么带一句说给作者听的中文，要么带模型自己的字。
    for name, payload in seen:
        if name != "turn":
            continue
        assert payload["said_to_author"] or payload["text"], payload


def test_the_frames_really_leave_the_server_before_the_turn_is_over(
    book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这一刀的主张，量在真的 ASGI 出口上。**

    上面那几条读的是跑完之后的整个 body —— 那种读法对一个「攒到最后一起发」的实现
    **一样是绿的**，而那正是今天这个黑箱。

    ⚠️ **`TestClient` 攒**（实测：一个每 0.4 秒吐一片的生成器，三片全在 1.2 秒时一起
    到手）。所以这一条不走 `TestClient`，直接拿一个自己的 `send` 驱动 ASGI 应用，
    记下**每一片 body 是什么时候离开服务端的**。

    判据不是挂钟，是**因果**：让第二次模型调用在被叫到的那一刻回头数一下
    「已经发出去几片了」——攒到最后发的实现在那一刻数出来的是 0，而且永远等不到，
    于是这条断言在坏实现上必然红、在对的实现上几毫秒就绿。
    """
    import asyncio

    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    pid = book["pid"]
    sent: list[bytes] = []

    def already_out() -> bytes:
        return b"".join(sent)

    class Waits:
        """第二次调用之前先等「查完了那一声真的出去了」。**等不到就是攒着**。"""

        def __init__(self) -> None:
            self.calls = 0
            self.saw_before_second_call = b""

        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return wants(("scene_constraints", json.dumps({"chapter": 2})))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and b"tool_finished" not in already_out():
                time.sleep(0.005)
            self.saw_before_second_call = already_out()
            return says("好了。")

    model = Waits()
    use(monkeypatch, model)

    async def drive(path: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        scope = {
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

        delivered = asyncio.Event()

        async def receive() -> dict[str, Any]:
            """请求体给一次，**然后永远挂着**（真服务器就是这样：下一条消息是断开）。

            返回一个「还有请求体」的假消息在这儿是致命的：`StreamingResponse` 那条
            监听断开的支路会在一个不 await 任何东西的死循环里空转，把事件循环饿死
            ——症状是整条测试挂住，而不是报错。
            """
            if delivered.is_set():
                await asyncio.Event().wait()
            delivered.set()
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                sent.append(message["body"])

        await app(scope, receive, send)

    with TestClient(app) as boot:  # lifespan：库 / schema 就位（同 `client` 夹具）
        chat_id = open_chat(boot, pid)
    asyncio.run(drive(f"/api/projects/{pid}/chats/{chat_id}/turn/events", {"chapter": 2, "said": "查一下"}))

    assert b"tool_started" in model.saw_before_second_call, (
        "这一轮跑到第二次模型调用时，一帧都还没离开服务端 —— "
        "那就是攒到最后一起发，也就是这一刀要治的那个黑箱"
    )
    # 回执**不在**那时候（它按定义是最后一帧）——反过来钉一次，免得上面那条
    # 在「其实全都发完了」的实现上也绿。
    assert b"event: receipt" not in model.saw_before_second_call
    assert b"event: receipt" in already_out()


def test_the_two_routes_cannot_drift_because_they_are_one_run(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个剧本，两条路由的回执**逐字段相同**（会话那几个随时间走的字段除外）。

    这条断言不是防御性编程：两条路由讲的是同一件事，一旦哪天有人在其中一条上补了个
    字段、另一条忘了，作者会发现「用长连接跑和用别的跑，结果不一样」——
    而那一种 bug 没有任何东西会报错。
    """
    pid = book["pid"]
    script = [
        wants(("scene_constraints", json.dumps({"chapter": 2}))),
        says("血脉那条先别说破。"),
    ]

    use(monkeypatch, Scripted(*script))
    blocking_chat = open_chat(client, pid)
    blocking = client.post(
        f"/api/projects/{pid}/chats/{blocking_chat}/turn",
        json={"chapter": 2, "said": "能说破吗？"},
    ).json()

    use(monkeypatch, Scripted(*script))
    streaming_chat = open_chat(client, pid)
    streamed = frames(
        stream_turn(client, pid, streaming_chat, chapter=2, said="能说破吗？").text
    )[-1][1]

    # 会话本身两段不同（id / 时间戳），比的是这一轮**说了什么、做了什么**。
    for field in ("chapter", "reason", "message", "reply", "messages", "steps", "lookups",
                  "context", "asked", "drafts"):
        assert blocking[field] == streamed[field], field


def test_the_receipt_is_the_last_frame_so_nobody_needs_a_second_endpoint(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最后一帧的载荷和不流式那条路由的出参**是同一个模型**。

    订阅这条流的界面因此不需要为「跑完之后」再打一次请求——多打一次的形态是
    「回执到手之前屏幕上有一段没有任何东西的空白」。
    """
    pid = book["pid"]
    use(monkeypatch, Scripted(says("好。")))
    chat_id = open_chat(client, pid)
    seen = frames(stream_turn(client, pid, chat_id, chapter=1, said="喂").text)
    assert [name for name, _ in seen][-1] == "receipt"
    assert set(seen[-1][1]) == set(chat_mod.TurnReceipt.model_fields)


# ══════════════════════════════════════════════════════════════════════════
# 二、边界一 —— **这条管子上不许有工具查到了什么**
# ══════════════════════════════════════════════════════════════════════════


def test_no_tool_return_ever_reaches_the_wire(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """喂一本秘密里写着 `twist` 的书跑完整一轮，**把整条流拼起来搜毒**。

    ADR 0024 自己把这一条列成最贵的代价：一轮的返回一直是**投影过的**
    （工具查到了什么根本不上屏，只有一个「查了几次」的数），而事件流会把中间过程摊开。
    第一刀在类型层堵了这道闸；这儿量的是**适配器没有从别处把它捞回来**——
    一旦推上去过，那段话就在作者的持久化对话里了，改代码删不掉。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(
                ("scene_constraints", json.dumps({"chapter": 2})),
                ("chapter_text", json.dumps({"chapter": 1})),
            ),
            says("知道了。"),
        ),
    )
    chat_id = open_chat(client, pid)
    body = stream_turn(client, pid, chat_id, chapter=2, said="查一下").text

    # 工具返回是 `model_dump_json()` 出来的内部模型：裸 id + snake_case 字段名。
    assert "secret:" not in body
    assert "must_not_reveal" not in body
    assert "forbidden_entities" not in body
    # `chapter_text` 那一条的返回里是第 1 章的正文原文——它属于「读回来的东西」，
    # 同样不许出现在流上（流上只有「读完了第 1 章的正文，好了」）。
    assert "青云城主府" not in body


def test_the_machine_codes_on_the_wire_are_never_the_words_the_author_reads(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**帧上有机器码，但每一帧同时带着那句中文。**

    线上有 `kind` / `tool` 是对的（界面要分派），坏的是界面把它们摆上屏。
    这儿钉的是后端这一侧的义务：**措辞跟着数据一起来**，
    免得下一个人在浏览器里写第二张「码 → 中文」的表（那张表这个仓库删过一次）。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(wants(("scene_constraints", json.dumps({"chapter": 2}))), says("好。")),
    )
    chat_id = open_chat(client, pid)
    seen = frames(stream_turn(client, pid, chat_id, chapter=2, said="查一下").text)

    started = next(p for n, p in seen if n == "turn" and p["kind"] == "tool_started")
    assert started["tool"] == "scene_constraints"
    assert started["said_to_author"].startswith("正在")
    assert "scene_constraints" not in started["said_to_author"]


# ══════════════════════════════════════════════════════════════════════════
# 三、状态码 —— **第一个字节发出去之后就没有了**
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("chat", "body", "status"),
    [
        ("real", {"said": "写"}, 422),  # 章号必填（模块 docstring 第一条）
        ("gone", {"chapter": 2, "said": "写"}, 404),
    ],
)
def test_the_refusals_are_still_real_status_codes_not_an_empty_stream(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
    chat: str,
    body: dict[str, Any],
    status: int,
) -> None:
    """拒绝必须发生在第一个字节之前。

    **一个 200 的空流是这个仓库最怕的那种失败形态**：屏幕上看起来正常，
    只是什么都没发生，而作者没有任何线索知道该去改设置还是该刷新。
    """
    pid = book["pid"]
    use(monkeypatch, Scripted(says("好。")))
    chat_id = open_chat(client, pid) if chat == "real" else "chat_session:nope"
    response = client.post(f"/api/projects/{pid}/chats/{chat_id}/turn/events", json=body)
    assert response.status_code == status, response.text
    assert not response.headers["content-type"].startswith("text/event-stream")


def test_a_second_window_gets_409_not_a_second_stream(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """这段对话正在跑的时候，第二条流拿到的是 409 + `chat_busy` + `params.action="send"`。"""
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    chat_mod.LIVE.begin((pid, chat_id))
    try:
        use(monkeypatch, Scripted(says("好。")))
        refused = client.post(
            f"/api/projects/{pid}/chats/{chat_id}/turn/events",
            json={"chapter": 1, "said": "喂"},
        )
    finally:
        chat_mod.LIVE.end((pid, chat_id))
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["error"] == "chat_busy"
    assert detail["params"]["action"] == "send"


def test_the_model_being_unconfigured_is_422_before_the_stream_opens(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """**没有 `configured`**：钥匙没填时这条路由和另一条一样 422，不开流。"""
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    refused = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn/events", json={"chapter": 1, "said": "喂"}
    )
    assert refused.status_code == 422, refused.text


def test_a_refusal_after_the_headers_keeps_its_sentence(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """头发出去之后才被拒 —— 只剩一帧 `failed`，**但那句中文还在**。

    唯一真实来源是乐观并发闸（这段对话在别的窗口里刚往前走了一步）。这儿用一次
    直接抛 `HTTPException` 的落库来构造它：换掉的是「怎么撞上」，不是「撞上之后怎么说」。
    """
    pid = book["pid"]
    use(monkeypatch, Scripted(says("好。")))
    chat_id = open_chat(client, pid)

    from fastapi import HTTPException

    def refuse(*args: Any, **kwargs: Any) -> int:
        raise HTTPException(
            status_code=409,
            detail={"error": "chat_conflict", "message": "这段对话在别的窗口里刚往前走了一步。"},
        )

    seen = frames(stream_turn(client, pid, chat_id, chapter=1, said="喂").text)
    assert seen[-1][0] == "receipt"  # 先证明这条剧本本来是能跑通的

    chat_id2 = open_chat(client, pid)
    monkeypatch.setattr(chat_mod, "_append", refuse)
    late = client.post(
        f"/api/projects/{pid}/chats/{chat_id2}/turn/events", json={"chapter": 1, "said": "喂"}
    )
    # **作者那句话的落库就在构造里**，所以这一次拒绝还够得着 409。
    assert late.status_code == 409, late.text


def test_a_crash_in_the_middle_is_not_swallowed(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跑到一半真的炸了 —— **不许吞**。

    吞掉的形态是：作者看到一条空流，维护者一行 traceback 都没有。
    带回生成器那条线再抛，uvicorn 才印得出它炸在哪儿。
    """
    pid = book["pid"]

    class Explodes:
        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> Any:
            raise RuntimeError("这是一次真的 bug，不是一次供应商故障")

    use(monkeypatch, Explodes())
    chat_id = open_chat(client, pid)
    with pytest.raises(RuntimeError):
        client.post(
            f"/api/projects/{pid}/chats/{chat_id}/turn/events", json={"chapter": 1, "said": "喂"}
        )
    # **位子还回去了**：不然这段对话在进程活着的余生里都是「正在跑上一轮」。
    assert not chat_mod.LIVE.running((pid, chat_id))


# ══════════════════════════════════════════════════════════════════════════
# 四、「停」仍然是另一个请求
# ══════════════════════════════════════════════════════════════════════════


def test_stop_is_still_a_separate_request_so_a_dead_stream_cannot_take_it_away(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """这条流开着的时候，`POST …/stop` 照旧够得着它。

    **这就是不选 WebSocket 的那条理由的验收**：把「停」搬到同一条 socket 上，
    socket 断掉的那一刻作者就没有插手的地方了，而那一轮还在跑、还在花钱。
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as Flag

    pid = book["pid"]
    inside = Flag()
    release = Flag()

    class Slow:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                inside.set()
                release.wait(5)
                assert cancel.stopped, "「停」没送到这一轮手里"
            return CompletionResult(
                text="停下了。", model="deepseek-v4-flash", finish_reason="stop"
            )

    use(monkeypatch, Slow())
    chat_id = open_chat(client, pid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(
            lambda: client.post(
                f"/api/projects/{pid}/chats/{chat_id}/turn/events",
                json={"chapter": 1, "said": "写一段"},
            )
        )
        assert inside.wait(5)
        stopped = client.post(f"/api/projects/{pid}/chats/{chat_id}/stop")
        release.set()
        response = running.result(timeout=15)

    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["stopped"] is True
    assert frames(response.text)[-1][0] == "receipt"


# ══════════════════════════════════════════════════════════════════════════
# 五、逐字看得见的是**稿子**，不是回话
# ══════════════════════════════════════════════════════════════════════════


def test_the_question_card_survives_on_the_receipt_not_only_on_the_wire(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """它停下来问了一句 —— **问句和选项在回执上也有一份**。

    事件是「跑的过程」，而作者可能在这一轮结束之后才打开那段对话（换台机器、刷新页面、
    三个月后回来）。那时唯一还说得出「它当时问了你什么」的就是这个字段。
    第一刀报的两处产品面余债，这是其中一处。
    """
    pid = book["pid"]
    asked = json.dumps(
        {
            "question": "这一场你想让萧决知道那件事吗？",
            "options": ["让他知道", "先瞒着", "让他半信半疑"],
        },
        ensure_ascii=False,
    )
    use(
        monkeypatch,
        Scripted(
            CompletionResult(
                text="我拿不准这一场的方向。",
                model="deepseek-v4-flash",
                finish_reason="tool_calls",
                tool_calls=(ToolCall(id="ask-1", name="ask_author", arguments=asked),),
            ),
            says("不该跑到这儿——这一轮在上面就该结束了。"),
        ),
    )
    chat_id = open_chat(client, pid)
    seen = frames(stream_turn(client, pid, chat_id, chapter=2, said="这一场怎么写？").text)

    on_wire = next(p for n, p in seen if n == "turn" and p["kind"] == "asked_author")
    assert on_wire["asked"]["question"] == "这一场你想让萧决知道那件事吗？"
    receipt = seen[-1][1]
    assert receipt["reason"] == "asked_author"
    assert receipt["asked"]["question"] == "这一场你想让萧决知道那件事吗？"
    assert receipt["asked"]["options"] == ["让他知道", "先瞒着", "让他半信半疑"]
    # **引擎一个字都没加**：问句和选项逐字是模型交上来的那一份。
    assert set(receipt["asked"]) == {"question", "options"}
