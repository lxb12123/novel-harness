"""稳定 prompt 构造：章节滚动总结。"""

from __future__ import annotations

from typing import Final, Literal, TypedDict

__all__ = [
    "SUMMARY_MAX_CHARS",
    "SUMMARY_VERSION",
    "SummaryMessage",
    "build_summary_messages",
]


SUMMARY_VERSION: Final = "chapter-summary-v1"
SUMMARY_MAX_CHARS: Final = 120


_SYSTEM_PROMPT: Final = f"""你是中文长篇小说的后台背景总结器。
Prompt version: {SUMMARY_VERSION}.

把下面这一章压缩成一段不超过 {SUMMARY_MAX_CHARS} 个中文字符的摘要。要求：
- 保留人物本名、地点、关键情节节拍和状态变化，不引入本章没有的信息；
- 只陈述本章内容，不要评价、不要预测未来、不要使用 Markdown；
- 只输出摘要正文本身，不要任何标题、前缀、引号或解释。

---

"""


class SummaryMessage(TypedDict):
    role: Literal["system", "user"]
    content: str


def build_summary_messages(chapter_text: str) -> list[SummaryMessage]:
    """返回确定性的总结消息序列，章节原文逐字节作为文本包含在内。"""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": chapter_text},
    ]
