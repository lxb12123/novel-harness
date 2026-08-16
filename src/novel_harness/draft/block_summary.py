"""对话块压缩的稳定 prompt 构造（docs_dev 快照第五节）。

把**最旧一段作者的话**压成一句给模型自己看的话。它不是事实、不进 canon——
它替掉的原文永远在 canonical / 界面里，错了可以按编号取回核对。所以它要求的
是「别编」而不是「保全」：漏了可以展开，编了会误导。
"""

from __future__ import annotations

from typing import Final, Literal, TypedDict

from .length import DEFAULT_LENGTH_POLICY, DraftLanguage, LengthSpec


BLOCK_SUMMARY_VERSION: Final = "conversation-block-summary-v1"
BLOCK_SUMMARY_MAX_UNITS: Final = 80
"""一条块摘要的字数上限。**和 `agent/blocks.py` 那个常量是同一件事的两半**：
prompt 写死了「不超过 N 字」，渲染层按同一个 N 截断——两个数必须一起改。"""

BLOCK_SUMMARY_LENGTH: Final = DEFAULT_LENGTH_POLICY.validate_spec(
    LengthSpec(
        language=DraftLanguage.ZH,
        min_units=1,
        target_units=40,
        max_units=BLOCK_SUMMARY_MAX_UNITS,
    )
)
"""块摘要调用的长度档。**和 prompt 放在同一个文件里**（同 `summarize.py` 那条）：
两处必须一起改，分开放就会各自漂。"""


class BlockSummaryMessage(TypedDict):
    role: Literal["system", "user"]
    content: str


_SYSTEM_PROMPT: Final = f"""你是中文长篇小说的写作助手，正在整理自己的一段旧对话。
Prompt version: {BLOCK_SUMMARY_VERSION}.

把下面这段对话压成一句不超过 {BLOCK_SUMMARY_MAX_UNITS} 个中文字的话，要求：
- 只概括**作者**说过什么、定过什么（要求、方向、约束、给过的事实）；
- 不要编造作者没说过的话，不要加入你自己的判断或计划；
- 只输出那句概括本身，不要标题、不要引号、不要解释。

---

"""


def block_summary_messages(text: str) -> list[BlockSummaryMessage]:
    """返回确定性的块摘要消息序列（块原文逐字节作为文本包含在内）。"""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
