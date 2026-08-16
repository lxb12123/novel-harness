"""对话块：作者的话按块压缩（设计见 docs_dev 快照第五节）。

剪枝处理「可再生」（工具结果、中间推理），**作者的话是唯一只增不减、永不剪的**，
也是 `CONTEXT_FULL` 的唯一来源。压缩处理它：装不下时把**最旧**的块压成一条摘要，
canonical 里留下一行标记（`〖已压缩对话块 #N〗…`），原文一字不动；投影把已压缩块
的成员换成「更早的对话」摘要块，模型需要确切原话时按编号取回整块。

── 块是什么 ──────────────────────────────────────────────────────────────

**一块 = `BLOCK_SIZE` 句作者的话 + 那期间模型的话和工具往返**（以作者的话为边界
切分，所以一轮工具往返不会跨块）。规则是 SYSTEM 消息，**不算块成员**——它小、
重要、有自己的机制，不该被埋进摘要；这是有意区别于「块 = 整段对话」的简化。

── 标记是结构判据，不是表 ──────────────────────────────────────────────

`is_block_summary` 认的是固定前缀（同 `rules.is_rule` 的先例）：块号从标记里解析，
加第二种标记的那天它自动被认出来，不用改表。标记消息本身是 SYSTEM，而 `_visible`
只放 user / 非空 assistant——所以它不会漏进作者的界面。
"""

from __future__ import annotations

from typing import Final

from .loop import AgentMessage, Conversation, Role


BLOCK_SIZE: Final = 8
"""每个块里作者的话（user 消息）条数。**它是分块的唯一判据**，换章不是（作者的话
不绑章号，章号没法切它）。"""

BLOCK_MARK: Final = "〖已压缩对话块 #"
"""canonical 里压缩标记的固定前缀。内容 = 前缀 + 块号 + `〗` + 一句摘要。"""

BLOCK_SUMMARY_MAX_UNITS: Final = 80
"""一条块摘要最多多少字。**它是成本的闸**：摘要每一轮都重发（它替掉了整块原文），
而作者是按 token 付钱的那个人。超长截断，不是拒绝——摘要不是作者的话，丢了原样
还能在 canonical / 界面里看。"""

CONVERSATION_SUMMARY_HEADER: Final = "【更早的对话】（机器摘要，原文仍在界面里）："
"""投影里摘要块的标题行。措辞写清两件事：这是机器压缩的、原文没有丢。"""


def block_number_of(conversation: Conversation, message_index: int) -> int:
    """第 `message_index` 条作者的话属于第几块（1-based）。

    `message_index` 必须是 USER 消息的下标；传错是调用方的事（同 `rules` 的坐标）。
    """
    authors = [i for i, m in enumerate(conversation.messages) if m.role is Role.USER]
    position = authors.index(message_index)
    return position // BLOCK_SIZE + 1


def marker_block_number(message: AgentMessage) -> int | None:
    """从标记消息的内容里解析块号。认不出返回 `None`（不猜）。"""
    if not is_block_summary(message):
        return None
    tail = message.content[len(BLOCK_MARK) :]
    number, _, _ = tail.partition("〗")
    try:
        return int(number)
    except ValueError:
        return None


def is_block_summary(message: AgentMessage) -> bool:
    """这条 canonical 消息是不是一条块压缩标记。**判据是结构**（同 `rules.is_rule`）。"""
    return message.role is Role.SYSTEM and message.content.startswith(BLOCK_MARK)


def summary_message(number: int, summary: str) -> AgentMessage:
    """一条块压缩标记。**它替掉的块原文一个字都不动**——canonical 只增不改。"""
    return AgentMessage(
        role=Role.SYSTEM, content=f"{BLOCK_MARK}{number}〗{summary}"
    )


def block_span(conversation: Conversation, number: int) -> tuple[int, int]:
    """第 `number` 块在 `conversation.messages` 里的 `[start, end)`。

    起点 = 本块第一句作者的话，终点 = 下一块第一句作者的话（没有就到底）。
    块不存在时返回 `(0, 0)`（空块，`block_text` 因此是空串）。
    """
    authors = [i for i, m in enumerate(conversation.messages) if m.role is Role.USER]
    start_pos = (number - 1) * BLOCK_SIZE
    if start_pos >= len(authors):
        return (0, 0)
    start = authors[start_pos]
    if start_pos + BLOCK_SIZE < len(authors):
        end = authors[start_pos + BLOCK_SIZE]
    else:
        end = len(conversation.messages)
    return (start, end)


def block_text(conversation: Conversation, number: int) -> str:
    """第 `number` 块渲染成文本：摘要器的输入，也是按号取回时拿到的原文。"""
    start, end = block_span(conversation, number)
    lines: list[str] = []
    for message in conversation.messages[start:end]:
        if message.role is Role.USER:
            lines.append(f"作者：{message.content}")
        elif message.role is Role.ASSISTANT and message.content.strip():
            lines.append(f"助手：{message.content}")
        elif message.role is Role.TOOL:
            lines.append(f"工具：{message.content}")
    return "\n".join(lines)


def marked_blocks(conversation: Conversation) -> dict[int, str]:
    """canonical 里已压缩的块号 → 摘要（按块号排序）。"""
    out: dict[int, str] = {}
    for message in conversation.messages:
        if not is_block_summary(message):
            continue
        number = marker_block_number(message)
        if number is None:
            continue
        _, _, summary = message.content.partition("〗")
        out[number] = summary
    return {number: out[number] for number in sorted(out)}


def uncompressed_numbers(conversation: Conversation) -> list[int]:
    """还有原文可压、还没压过的块号（升序）。"""
    authors = [i for i, m in enumerate(conversation.messages) if m.role is Role.USER]
    total = (len(authors) + BLOCK_SIZE - 1) // BLOCK_SIZE
    marked = set(marked_blocks(conversation))
    return [number for number in range(1, total + 1) if number not in marked]


def oldest_uncompressed_number(conversation: Conversation) -> int | None:
    """最旧那个还没压的块号；没有可压的返回 `None`。"""
    numbers = uncompressed_numbers(conversation)
    return numbers[0] if numbers else None

