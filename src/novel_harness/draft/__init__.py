"""起草层 —— 把引擎算出的约束拼成指令、调模型写这一场 prose。

`draft/` 是 `panel/`/`checks/` 的同级能力层:消费只读 StoryGraph 产出的约束,**永不 import
sqlite3、永不 connect、永不碰图表 SQL**(它不在 test_arch_guard 的任何白名单里,三道守卫
一建目录就自动罩住它)。

三块:
- `provider.py` —— 统一的模型出口(OpenAI 兼容协议,开源闭源同一套)。**这是 kill-gate 三臂
  和产品起草共用的那一份调用**,所以「参数在臂间/与生产一致」是结构性的,不是靠自觉。
- `context.py` —— 为一个场景取出合法约束集,并**把「cast 已解析」编码进类型**
  (`ResolvedConstraints`)。它还捎带认知矩阵:X1/X2 从同一个对象渲染,
  于是 EVAL_PROTOCOL §2 的反混淆铁律是类型保证而不是 runner 纪律。
- `assemble.py` —— 约束 + 简报 → prompt 的 X0/X1/X2 三种拼法(= kill-gate 三臂)。
  边界(它能看见 `ctx` 上的哪几类字段)由 [ADR 0010](../../../docs/adr/0010-writer-boundary.md)
  定死:**只有标签,永不 tell**。第 4 道守卫明说它拦不住「完整 PLANNED 进 prompt」,
  那一格只有那份 ADR 和 review 守得住。

**`secret_surfaces` 不在这份导出里,而且永远不会在。** 它是秘密的内容 tell,
`tests/test_draft_boundary.py` 的 `WRITER_BANNED` 把它钉死在墙的另一侧——
`assemble.py` 顺手调它一次就会让 X1/X2 命中自己写进 prompt 的词,Δ 翻负,
裁决表读出一个**假的 KILL**,而全程没有任何东西会红。

**`assemble` 这个名字故意不在下面的导出里**:它同时是模块名和函数名,包级绑定函数会把模块
彻底遮死(连 `import ..draft.assemble as mod` 都会拿到函数——3.7+ 先走 getattr)。
规矩:**与子模块同名的可调用对象一律不上包级**,要它就 `from .assemble import assemble`。
`eval/confound_lint` 那边写了同一条。
"""

from __future__ import annotations

from .assemble import DEFAULT_HOUSE_STYLE, PromptForm, graph_section
from .capabilities import (
    CallPlan,
    CapabilityError,
    ProviderCapabilities,
    ProviderRuntimeOptions,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    StructuredCallPlan,
    plan_call,
    plan_structured_call,
    resolve_capabilities,
)
from .context import ResolvedConstraints, resolve_constraints
from .generate import (
    DraftAttempt,
    DraftResult,
    continuation_instruction,
    generate_draft,
    validate_generation_plan,
)
from .length import (
    COUNTING_RULE_VERSION,
    DEFAULT_LENGTH_POLICY,
    M2_LENGTH_SPEC,
    DraftLanguage,
    LengthMeasurement,
    LengthPolicy,
    LengthSpec,
    LengthStatus,
    count_units,
    measure,
)
from .provider import (
    DEFAULT_TEMPERATURE,
    SAMPLING_STRICT_MODELS,
    CompletionResult,
    ProviderConfig,
    ProviderError,
    complete,
)

__all__ = [
    "DEFAULT_HOUSE_STYLE",
    "COUNTING_RULE_VERSION",
    "DEFAULT_LENGTH_POLICY",
    "DEFAULT_TEMPERATURE",
    "continuation_instruction",
    "CallPlan",
    "CapabilityError",
    "DraftLanguage",
    "DraftAttempt",
    "DraftResult",
    "LengthMeasurement",
    "LengthPolicy",
    "LengthSpec",
    "LengthStatus",
    "M2_LENGTH_SPEC",
    "SAMPLING_STRICT_MODELS",
    "CompletionResult",
    "PromptForm",
    "ProviderConfig",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderRuntimeOptions",
    "ReasoningDialect",
    "ReasoningEffort",
    "ResolvedCallPlan",
    "StructuredCallPlan",
    "ResolvedConstraints",
    "complete",
    "count_units",
    "graph_section",
    "generate_draft",
    "measure",
    "plan_call",
    "plan_structured_call",
    "resolve_constraints",
    "resolve_capabilities",
    "validate_generation_plan",
]
