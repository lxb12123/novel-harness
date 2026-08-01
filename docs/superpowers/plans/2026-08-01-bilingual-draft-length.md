# Bilingual Draft Length Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Chinese/English user-facing length controls, capability-aware OpenAI-compatible model calls, and a preregistered Chinese 2,000–3,000-character M2 experiment with auditable one-continuation handling.

**Architecture:** A pure `draft/length.py` owns human units, a pure `draft/capabilities.py` resolves model capacity and provider-neutral call plans, `draft/provider.py` serializes those plans through the OpenAI Python client, and `draft/generate.py` owns the one-continuation product pipeline. `eval/runner.py` consumes those production components under a fixed M2 profile and records every attempt before scoring the final concatenated draft.

**Tech Stack:** Python 3.12, Pydantic 2, OpenAI Python SDK Chat Completions compatibility layer, pytest, FastAPI, React 18, TypeScript, Vitest, Markdown ADR/preregistration assets.

---

## File map

New focused modules:

- `src/novel_harness/draft/length.py` — language enum, frozen length request/measurement/policy, deterministic Chinese and English counters.
- `src/novel_harness/draft/capabilities.py` — reasoning enum/dialects, endpoint/model capabilities, versioned length-to-token planner, resolved call plan.
- `src/novel_harness/draft/generate.py` — initial call, one fixed continuation, attempt/final result models; no graph or leak imports.
- `src/novel_harness/eval/evidence.py` — strict JSONL loader/inspector that reconstructs the verdict input and recomputes leak results.
- `tests/test_draft_length.py` — unit/count/range/policy boundaries.
- `tests/test_draft_capabilities.py` — capability resolution, reasoning support, budget and model-limit behavior.
- `tests/test_draft_generate.py` — one-call/two-call orchestration and no hidden retries.
- `tests/test_run_evidence.py` — JSONL shape, ordering, secret scan, leak recomputation, and verdict reconstruction.
- `tests/test_protocol_preregistration.py` — frozen-body and five-amendment document guards.
- `frontend/src/components/DraftLengthControls.tsx` — disabled-before-PASS bilingual length controls.
- `frontend/src/components/DraftLengthControls.test.tsx` — language/unit/limit/persistence behavior.
- `docs/EVAL_PROTOCOL_AMENDMENT_5.md` — pre-run experimental output/provider amendment.
- `docs/adr/0011-bilingual-draft-length.md` — product and Writer-boundary extension of ADR 0010.

Modified modules:

- `src/novel_harness/draft/assemble.py` — required `LengthSpec`, bilingual base prompt, shared length instruction, 800-code-point input-tail bound.
- `src/novel_harness/draft/provider.py` — connection config only, resolved-plan wire kwargs, reasoning adapters, streaming aggregation.
- `src/novel_harness/draft/__init__.py` — safe exports without shadowing submodules.
- `src/novel_harness/eval/runner.py` — fixed M2 profile, attempt/final JSONL, length-invalid fail-fast, protocol version.
- `src/novel_harness/cli.py` — capability/call-plan preflight and honest cell/transport-call output.
- `src/novel_harness/api/app.py` — optional future draft request schema while preserving the 501 response.
- `frontend/src/api/types.ts` and `frontend/src/components/ChapterPrepPage.tsx` — future request shape and visible gray controls.
- `README.md`, `CLAUDE.md`, `docs/PLAN.md`, `docs/ARCHITECTURE.md`, `docs/UI_ARCHITECTURE.md`, `docs/adr/README.md`, `src/novel_harness/eval/__init__.py`, `synth/booklet.toml` — current truth and five-amendment references.

Historical amendments 1–4, ADR 0010's existing text, `docs_dev/`, old migrations, and completed specs/plans are not rewritten.

### Task 1: Preregister amendment 5 and ADR 0011

**Files:**
- Create: `tests/test_protocol_preregistration.py`
- Create: `docs/EVAL_PROTOCOL_AMENDMENT_5.md`
- Create: `docs/adr/0011-bilingual-draft-length.md`
- Modify: `docs/EVAL_PROTOCOL.md:1-21`
- Modify: `docs/adr/README.md:14-20`
- Modify: `docs/PLAN.md:287-312,539,554,568`
- Modify: `README.md:82-92`
- Modify: `CLAUDE.md:126-142`
- Modify: `docs/ARCHITECTURE.md:400-428`
- Modify: `src/novel_harness/eval/__init__.py:1-10`
- Modify: `src/novel_harness/cli.py:925-934`
- Modify: `synth/booklet.toml:33-38`

- [ ] **Step 1: Write the frozen-body and amendment-index tests**

Create tests with this core shape:

```python
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/EVAL_PROTOCOL.md"
MARKER = b"## 1. 被证伪的命题\n"


def _body(data: bytes) -> bytes:
    return data[data.index(MARKER):]


def test_frozen_protocol_body_still_matches_0393088() -> None:
    baseline = subprocess.check_output(
        ["git", "show", "0393088:docs/EVAL_PROTOCOL.md"], cwd=ROOT
    )
    assert _body(PROTOCOL.read_bytes()) == _body(baseline)


def test_protocol_indexes_exactly_five_amendments() -> None:
    header = PROTOCOL.read_text(encoding="utf-8").split("## 1.", 1)[0]
    for number in range(1, 6):
        assert f"EVAL_PROTOCOL_AMENDMENT_{number}.md" in header
    assert (ROOT / "docs/EVAL_PROTOCOL_AMENDMENT_5.md").exists()
    assert (ROOT / "docs/adr/0011-bilingual-draft-length.md").exists()
```

Add a current-reference parametrization for README, CLAUDE, ARCHITECTURE, eval package docs, CLI docs, and `synth/booklet.toml`. Keep outcome-dependent M2 status and ADR 0009 existence out of permanent tests: those facts intentionally change after the real run.

- [ ] **Step 2: Run the new tests and confirm the expected failure**

Run: `uv run pytest tests/test_protocol_preregistration.py -q`

Expected: frozen-body test passes; amendment-5/ADR/current-reference tests fail because those assets do not exist yet.

- [ ] **Step 3: Write amendment 5 without changing the frozen body**

The amendment must state all of these concrete rules:

```text
M2 language: zh only
LengthSpec: min=2000, target=2500, max=3000
Chinese counter: non-whitespace Unicode code points
previous_tail: at most 800 input code points; not an output target
successful base run: 225 final cells
continuation: only when first result <2000; exactly one; never content/leak based
transport calls: 225–450 for a successful completed run
terminal INVALID: final <2000, final >3000, or last finish_reason=length
sample handling: no deletion, replacement, third attempt, scoring, append, or resume
M2 reasoning request: provider-neutral high; unsupported/unknown fails before run creation
provider boundary: OpenAI Python client; endpoint/model/key select provider; OpenRouter optional
English product contract: outside the inferential scope of the 225 Chinese cells
unchanged: traps, arms, repeats, tell detector, score thresholds, decision table
```

The timeline must say amendments 1–4 predate `synth/`, while amendment 5 postdates `synth/` but predates every inference call and `runs/*.jsonl`.

- [ ] **Step 4: Add ADR 0011 and current-document supersession notes**

ADR 0011 records explicit `zh/en`, the exact counters, default/product maxima, one product continuation, capability precedence, no silent clamp, stream-above-16k policy, `/draft` remaining 501, and that ADR 0010's history is preserved. In PLAN, supersede only output `约 800 字`; retain and label `previous_tail=800` as input. Replace the Claude-native-only provider wording with the compatible-client plus capability-adapter decision.

- [ ] **Step 5: Verify preregistration ordering and frozen bytes**

Run:

```bash
test ! -e runs
test -z "$(git log --all --format=%H -- 'runs/*.jsonl')"
uv run pytest tests/test_protocol_preregistration.py tests/test_doc_numbers.py -q
git diff --check
```

Expected: all tests pass, both `test` commands exit 0, and `docs/EVAL_PROTOCOL.md` differs only before `## 1.`.

- [ ] **Step 6: Commit the preregistration assets**

```bash
git add tests/test_protocol_preregistration.py docs/EVAL_PROTOCOL.md \
  docs/EVAL_PROTOCOL_AMENDMENT_5.md docs/adr/0011-bilingual-draft-length.md \
  docs/adr/README.md docs/PLAN.md README.md CLAUDE.md docs/ARCHITECTURE.md \
  src/novel_harness/eval/__init__.py src/novel_harness/cli.py synth/booklet.toml
git commit -m "docs: preregister bilingual M2 output profile"
```

Do not update `runner.PROTOCOL_VERSION` in this commit: the old runner does not yet implement amendment 5 and must not falsely label an old-format run as compliant.

### Task 2: Implement the pure bilingual length domain

**Files:**
- Create: `tests/test_draft_length.py`
- Create: `src/novel_harness/draft/length.py`
- Modify: `src/novel_harness/draft/__init__.py`

- [ ] **Step 1: Write failing counter, range, and policy tests**

Cover these exact cases:

```python
def test_chinese_counts_non_whitespace_code_points() -> None:
    assert count_units("你 好，\nGPT-5！", DraftLanguage.ZH) == 9


@pytest.mark.parametrize(
    ("text", "expected"),
    [("don't stop", 2), ("state-of-the-art GPT-5", 2), ("café 2026!", 2)],
)
def test_english_words(text: str, expected: int) -> None:
    assert count_units(text, DraftLanguage.EN) == expected


def test_length_spec_orders_its_bounds() -> None:
    with pytest.raises(ValidationError, match="min_units"):
        LengthSpec(language="zh", min_units=3000, target_units=2500, max_units=2000)


def test_default_policy_limits_are_exact() -> None:
    assert DEFAULT_LENGTH_POLICY.zh_max_chars == 20_000
    assert DEFAULT_LENGTH_POLICY.en_max_words == 12_000


def test_deployer_can_override_product_defaults_and_limits(monkeypatch) -> None:
    monkeypatch.setenv("NH_DRAFT_EN_DEFAULT_TARGET_WORDS", "2400")
    monkeypatch.setenv("NH_DRAFT_EN_HARD_MAX_WORDS", "15000")
    policy = LengthPolicy.from_env()
    assert policy.en_default.target_units == 2400
    assert policy.en_max_words == 15_000


def test_m2_profile_ignores_product_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("NH_DRAFT_ZH_HARD_MAX_CHARS", "500")
    assert M2_LENGTH_SPEC.model_dump() == {
        "language": "zh", "min_units": 2000,
        "target_units": 2500, "max_units": 3000,
    }
```

- [ ] **Step 2: Run the tests and verify import failures**

Run: `uv run pytest tests/test_draft_length.py -q`

Expected: FAIL because `draft.length` does not exist.

- [ ] **Step 3: Implement the exact domain types and pure functions**

Use these public names and shapes:

```python
class DraftLanguage(StrEnum):
    ZH = "zh"
    EN = "en"

class LengthStatus(StrEnum):
    UNDER = "under"
    WITHIN = "within"
    OVER = "over"

class LengthSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    language: DraftLanguage
    min_units: int = Field(ge=1)
    target_units: int = Field(ge=1)
    max_units: int = Field(ge=1)

class LengthMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)
    language: DraftLanguage
    unit: Literal["characters", "words"]
    actual_units: int
    status: LengthStatus

class LengthPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    zh_default: LengthSpec = LengthSpec(
        language=DraftLanguage.ZH, min_units=2000, target_units=2500, max_units=3000
    )
    en_default: LengthSpec = LengthSpec(
        language=DraftLanguage.EN, min_units=1200, target_units=1500, max_units=1800
    )
    zh_max_chars: int = 20_000
    en_max_words: int = 12_000

    def validate_spec(self, spec: LengthSpec) -> None:
        limit = self.zh_max_chars if spec.language is DraftLanguage.ZH else self.en_max_words
        if spec.max_units > limit:
            raise ValueError(f"max_units {spec.max_units} exceeds {spec.language.value} limit {limit}")

M2_LENGTH_SPEC = LengthSpec(
    language=DraftLanguage.ZH,
    min_units=2000,
    target_units=2500,
    max_units=3000,
)
COUNTING_RULE_VERSION = "nh-length-v1"
```

Implement `LengthPolicy.from_env()` using exactly `NH_DRAFT_ZH_DEFAULT_MIN_CHARS`, `NH_DRAFT_ZH_DEFAULT_TARGET_CHARS`, `NH_DRAFT_ZH_DEFAULT_MAX_CHARS`, `NH_DRAFT_ZH_HARD_MAX_CHARS`, and the corresponding four `NH_DRAFT_EN_*_WORDS` names. Validate the resulting defaults against the resulting hard maxima and reject malformed values atomically. Implement English counting with `unicodedata.category(ch)[0] in {"L", "N"}` and allow apostrophe/hyphen only between word characters. Do not add a language-detection or tokenizer dependency.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_draft_length.py -q`

Expected: all new tests pass.

- [ ] **Step 5: Commit the length domain**

```bash
git add src/novel_harness/draft/length.py src/novel_harness/draft/__init__.py tests/test_draft_length.py
git commit -m "feat: add bilingual draft length domain"
```

### Task 3: Make prompt assembly language- and length-aware

**Files:**
- Modify: `tests/test_draft_assemble.py`
- Modify: `src/novel_harness/draft/assemble.py`
- Modify: `tests/test_synth_artifact.py`

- [ ] **Step 1: Update helpers and write failing bilingual/base-invariant tests**

Change every `assemble()` test call to pass `length=M2_LENGTH_SPEC`. Add tests that assert:

```python
zh = LengthSpec(language="zh", min_units=2000, target_units=2500, max_units=3000)
en = LengthSpec(language="en", min_units=1200, target_units=1500, max_units=1800)

assert "2000–3000 字" in assemble(ctx, form=PromptForm.X0, goal=GOAL, length=zh)[0]["content"]
assert "1200–1800 words" in assemble(ctx, form=PromptForm.X0, goal=GOAL, length=en)[0]["content"]
assert inspect.signature(assemble).parameters["length"].default is inspect.Parameter.empty
```

Add a 900-code-point `previous_tail` test that asserts only its final 800 code points enter every arm. Keep the existing byte-exact X0/X1/X2 prefix assertions.

- [ ] **Step 2: Run assembly tests and see the signature/prompt failures**

Run: `uv run pytest tests/test_draft_assemble.py tests/test_synth_artifact.py -q`

Expected: FAIL because `assemble()` has no `length` parameter and still hard-codes `600–1000 字`.

- [ ] **Step 3: Implement bilingual system prompts in the shared base**

Use this required signature:

```python
def assemble(
    ctx: ResolvedConstraints,
    *,
    form: PromptForm,
    goal: str,
    length: LengthSpec,
    previous_tail: str = "",
    house_style: str | None = None,
) -> list[dict[str, str]]:
```

Move style-only Chinese text out of the old length-bearing constant, add an English equivalent, and append a language-specific length instruction inside the one shared base. Use `previous_tail[-800:]` after stripping outer whitespace. Never add length text inside an arm-specific graph section.

- [ ] **Step 4: Run assembly, boundary, and confound tests**

Run:

```bash
uv run pytest tests/test_draft_assemble.py tests/test_draft_boundary.py \
  tests/test_confound_lint.py tests/test_synth_artifact.py -q
```

Expected: PASS; X1/X2 still satisfy the frozen ±15% input-prompt lint.

- [ ] **Step 5: Commit prompt changes**

```bash
git add src/novel_harness/draft/assemble.py tests/test_draft_assemble.py tests/test_synth_artifact.py
git commit -m "feat: render bilingual draft length prompts"
```

### Task 4: Resolve provider capabilities and token budgets

**Files:**
- Create: `tests/test_draft_capabilities.py`
- Create: `src/novel_harness/draft/capabilities.py`
- Modify: `src/novel_harness/draft/provider.py`
- Modify: `tests/test_draft_provider.py`

- [ ] **Step 1: Write failing capability and budget tests**

Test exact official/known routes plus override precedence:

```python
def test_openrouter_opus_high_uses_shared_80_percent_reserve():
    caps = resolve_capabilities("https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8")
    plan = plan_call(
        M2_LENGTH_SPEC,
        ReasoningEffort.HIGH,
        caps,
        request_token_budget=40_000,
    )
    assert plan.visible_token_budget == 7024
    assert plan.required_token_budget == 35_120
    assert plan.request_token_budget == 40_000
    assert plan.reasoning_dialect == ReasoningDialect.OPENROUTER

def test_deepseek_v4_uses_its_official_chat_shape():
    caps = resolve_capabilities("https://api.deepseek.com", "deepseek-v4-pro")
    assert caps.max_context_tokens == 1_000_000
    assert caps.max_output_tokens == 384_000
    assert caps.max_tokens_field == "max_tokens"
    assert ReasoningEffort.HIGH in caps.reasoning_levels

def test_unknown_model_rejects_requested_reasoning():
    caps = resolve_capabilities("http://localhost:11434/v1", "custom-model")
    with pytest.raises(CapabilityError, match="reasoning"):
        plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, caps)

def test_model_limit_is_not_silently_clamped():
    caps = ProviderCapabilities(
        base_url="http://localhost:11434/v1",
        model="small-test-model",
        source="operator-test",
        max_context_tokens=32_000,
        max_output_tokens=4096,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({ReasoningEffort.OFF}),
        reasoning_dialect=ReasoningDialect.NONE,
        reasoning_shares_output=False,
        supports_streaming=True,
        supports_stream_usage=False,
    )
    with pytest.raises(CapabilityError, match="4096"):
        plan_call(M2_LENGTH_SPEC, ReasoningEffort.OFF, caps)
```

- [ ] **Step 2: Run and verify missing capability module**

Run: `uv run pytest tests/test_draft_capabilities.py -q`

Expected: FAIL on import.

- [ ] **Step 3: Implement the frozen capability and call-plan types**

Expose these types:

```python
class ReasoningEffort(StrEnum):
    OFF = "off"; LOW = "low"; MEDIUM = "medium"; HIGH = "high"

class ReasoningDialect(StrEnum):
    NONE = "none"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    DEEPSEEK = "deepseek"
    ANTHROPIC_COMPAT = "anthropic_compat"

class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)
    base_url: str
    model: str
    source: str
    adapter_version: str = "nh-provider-v1"
    max_context_tokens: int | None
    max_output_tokens: int | None
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"]
    reasoning_levels: frozenset[ReasoningEffort]
    reasoning_dialect: ReasoningDialect
    reasoning_shares_output: bool
    reserve_ratio_high: float | None = None
    supports_streaming: bool | None = None
    supports_stream_usage: bool | None = None

class ResolvedCallPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: str
    visible_token_budget: int
    required_token_budget: int
    request_token_budget: int
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"]
    reasoning_requested: ReasoningEffort
    reasoning_effective: ReasoningEffort
    reasoning_dialect: ReasoningDialect
    stream: bool
    capability: ProviderCapabilities
```

Use the versioned visible formula `ceil(max_units * 2.0) + 1024`. For a shared 80% high reserve, divide by `0.2` and round upward to the next 10,000-token boundary, yielding 40,000 for M2. Stream when the resolved budget exceeds 16,000. Explicit operator overrides must beat exact registry entries; unknown plus non-off reasoning must fail.

`plan_call()` accepts an optional explicit `request_token_budget`; it must be at least the computed `required_token_budget` and no greater than the model capability. The M2 profile freezes 40,000 explicitly. Tests cover override → exact registry → endpoint metadata → unknown precedence, endpoint/model exact matching, and rejection when streaming support is false or unknown above the 16,000 threshold.

Registry entries must cover current OpenAI GPT-5-family Chat Completions, official DeepSeek V4, direct Anthropic-compatible Claude Opus 4.8, and OpenRouter's Opus 4.8 route. Keep the registry version in the recorded capability.

- [ ] **Step 4: Remove product-wide `max_tokens=4096` from connection config**

`ProviderConfig` keeps connection/sampling fields only:

```python
class ProviderConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: str
    base_url: str
    api_key: str = ""
    temperature: float | None = None
    timeout: float = 600.0
```

Require both model and base URL from environment; do not default the open-source product to Claude/OpenRouter. Parse optional capability overrides separately (`NH_LLM_MAX_CONTEXT_TOKENS`, `NH_LLM_MAX_OUTPUT_TOKENS`, `NH_LLM_DIALECT`) and `NH_LLM_REASONING_EFFORT`, without exposing them as the ordinary user's article-length controls.

- [ ] **Step 5: Run capability and provider-config tests**

Run: `uv run pytest tests/test_draft_capabilities.py tests/test_draft_provider.py -q`

Expected: PASS; no assertion expects a 4096 default.

- [ ] **Step 6: Commit capability planning**

```bash
git add src/novel_harness/draft/capabilities.py src/novel_harness/draft/provider.py \
  tests/test_draft_capabilities.py tests/test_draft_provider.py
git commit -m "feat: plan capability-aware draft budgets"
```

### Task 5: Serialize reasoning adapters and aggregate streaming completions

**Files:**
- Modify: `tests/test_draft_provider.py`
- Modify: `src/novel_harness/draft/provider.py`

- [ ] **Step 1: Write failing exact-wire tests**

Assert exact kwargs for each dialect:

```python
OPENAI: max_completion_tokens=plan.request_token_budget, reasoning_effort="high"
OPENROUTER: max_tokens=plan.request_token_budget,
            extra_body={"reasoning": {"effort": "high", "exclude": True}}
DEEPSEEK: max_tokens=plan.request_token_budget, reasoning_effort="high",
          extra_body={"thinking": {"type": "enabled"}}
ANTHROPIC_COMPAT: max_tokens=plan.request_token_budget,
                  extra_body={"thinking": {"type": "adaptive"},
                              "output_config": {"effort": "high"}}
OFF: no reasoning_effort and no reasoning/thinking object
```

Add an actual OpenAI SDK + `httpx.MockTransport` test proving `extra_body` reaches JSON. Add fake streaming chunks where reasoning deltas are ignored, visible content concatenates, final usage is captured, and `finish_reason="stop"` survives.

- [ ] **Step 2: Run the focused tests and observe wire/stream failures**

Run: `uv run pytest tests/test_draft_provider.py -q`

Expected: FAIL because `complete()` does not accept a call plan or stream.

- [ ] **Step 3: Require a resolved plan at the transport boundary**

Use this signature:

```python
def complete(
    messages: Sequence[dict[str, Any]],
    *,
    config: ProviderConfig,
    plan: ResolvedCallPlan,
    client: Any = None,
) -> CompletionResult:
```

Build kwargs through one `_wire_kwargs(config, plan, messages)` function. Reject `plan.model != config.model`. In stream mode request usage only when the capability supports it, concatenate only `delta.content`, and preserve null usage values honestly. Continue to wrap transport failures in `ProviderError`.

- [ ] **Step 4: Verify official-client serialization and both transport paths**

Run: `uv run pytest tests/test_draft_provider.py tests/test_draft_capabilities.py -q`

Expected: PASS.

- [ ] **Step 5: Commit transport adapters**

```bash
git add src/novel_harness/draft/provider.py tests/test_draft_provider.py
git commit -m "feat: map reasoning across compatible providers"
```

### Task 6: Add the shared one-continuation draft generator

**Files:**
- Create: `tests/test_draft_generate.py`
- Create: `src/novel_harness/draft/generate.py`
- Modify: `src/novel_harness/draft/__init__.py`

- [ ] **Step 1: Write failing orchestration tests**

Cover:

```python
within-range initial -> one complete() call, one attempt
under-length initial -> exactly two calls and fixed continuation messages
under-length final -> status under, never a third call
over-length initial -> one call, full text retained
leak-like content -> does not influence retry decision
Chinese and English -> different fixed continuation sentence, same algorithm
usage totals -> sum only non-null reported values
```

The test must assert the second request contains the original messages, the first text as an assistant message, and exactly one fixed user continuation instruction.

- [ ] **Step 2: Run and verify missing generator module**

Run: `uv run pytest tests/test_draft_generate.py -q`

Expected: FAIL on import.

- [ ] **Step 3: Implement frozen attempt/final result types and orchestration**

Use these shapes:

```python
class DraftAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)
    number: int
    messages: tuple[dict[str, Any], ...]
    result: CompletionResult
    measurement: LengthMeasurement

class DraftResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    length: LengthMeasurement
    attempts: tuple[DraftAttempt, ...]
    truncated: bool

def generate_draft(
    messages: Sequence[dict[str, Any]],
    *,
    length: LengthSpec,
    config: ProviderConfig,
    plan: ResolvedCallPlan,
    client: Any = None,
) -> DraftResult:
    """Generate once and perform exactly one length-only continuation when under minimum."""
```

Do not import `eval.leak`, graph, store, trap, or arm types. Retry only when the first measurement is `UNDER`; the final `truncated` flag is true when the last finish reason is `length`.

- [ ] **Step 4: Run generator and boundary tests**

Run: `uv run pytest tests/test_draft_generate.py tests/test_draft_boundary.py -q`

Expected: PASS and the Writer tell boundary remains intact.

- [ ] **Step 5: Commit orchestration**

```bash
git add src/novel_harness/draft/generate.py src/novel_harness/draft/__init__.py \
  tests/test_draft_generate.py
git commit -m "feat: continue under-length drafts once"
```

### Task 7: Integrate M2 runner evidence and fail-fast validity

**Files:**
- Modify: `tests/test_runner.py`
- Modify: `src/novel_harness/eval/runner.py`
- Modify: `src/novel_harness/eval/__init__.py`
- Create: `tests/test_run_evidence.py`
- Create: `src/novel_harness/eval/evidence.py`

- [ ] **Step 1: Make existing fake outputs M2-length valid**

Add a helper that pads existing clean/leaking fixtures to exactly 2,100 non-whitespace Chinese characters while retaining the tell when present. Replace the old config `max_tokens=256` with a resolved fake call plan. This keeps existing runner tests about leakage and order from accidentally testing the new invalid-length branch.

- [ ] **Step 2: Write failing header/attempt/final/invalid tests**

Assert the header contains:

```python
head["length_profile"] == M2_LENGTH_SPEC.model_dump(mode="json")
head["counting_rule"] == COUNTING_RULE_VERSION
head["continuation"] == {"max_attempts": 2, "trigger": "under_min_only"}
head["call_plan"]["reasoning_effective"] == "high"
```

Assert every provider call emits `generation_attempt`, every valid cell emits one `generation` final record, leak scoring receives concatenated text, and API keys never land. Add three fail-fast cases: final short, final over 3,000, final `finish_reason=length`. Each must emit `length_invalid`, preserve bytes, stop further calls, and never invoke the scorer for that cell.

In `tests/test_run_evidence.py`, construct a complete small fixture and assert the inspector rejects duplicate/missing/out-of-order cells, attempt numbers outside 1–2, mismatched stored counts, any serialized secret-shaped key, altered recorded leak results, a terminal `length_invalid`, and a protocol/header mismatch. Assert a valid fixture reconstructs the exact `GateInput` and `GateDecision` produced in memory.

- [ ] **Step 3: Run focused tests and see runner failures**

Run: `uv run pytest tests/test_runner.py -q`

Expected: FAIL because the runner still calls `complete()` directly and records one old-format generation row.

- [ ] **Step 4: Integrate the fixed production pipeline and update protocol version atomically**

Set:

```python
PROTOCOL_VERSION = (
    "EVAL_PROTOCOL.md@0393088 + 修正案 1/2/3/4/5 + ADR 0010/0011"
)
```

Require a frozen `ResolvedCallPlan` in `run_gate()`, validate `model` equality and `reasoning_effective is HIGH` before opening `out_path`, pass `M2_LENGTH_SPEC` to every `assemble()`, and call `generate_draft()` for each cell. Emit all attempts before scoring. Score only `DraftResult.text` after final length validation.

Create `LengthInvalidError(ValueError)` carrying trap/arm/repeat/measurement. On invalid final output, emit and flush `length_invalid`, then raise immediately. Do not add resume/overwrite/sample-replacement behavior.

Implement `inspect_run(path, *, store, project_id, traps) -> RunInspection` in `eval/evidence.py`. It must parse line-by-line, require exactly one matching header, enforce trap/arm/repeat/attempt ordering and 225 cells for the base profile, recompute every deterministic length and leak result from the stored final text plus the canonical graph constraints, reconstruct `GateInput`, call `decide()`, and return structured counts/length/continuation summaries used by ADR 0009. It performs a recursive forbidden-key scan for `api_key`, `authorization`, and bearer-token fields without rejecting the legitimate boolean `api_key_set`.

- [ ] **Step 5: Run runner, score, synth, and evidence-guard tests**

Run:

```bash
uv run pytest tests/test_runner.py tests/test_eval_score.py tests/test_synth_artifact.py \
  tests/test_draft_boundary.py tests/test_run_evidence.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit runner integration**

```bash
git add src/novel_harness/eval/runner.py src/novel_harness/eval/evidence.py \
  src/novel_harness/eval/__init__.py tests/test_runner.py tests/test_run_evidence.py
git commit -m "feat: record M2 length attempts and validity"
```

### Task 8: Add CLI preflight and a non-secret M2 provider profile

**Files:**
- Create: `src/novel_harness/draft/profile.py`
- Create: `tests/test_draft_profile.py`
- Create: `synth/m2-provider.example.json`
- Modify: `src/novel_harness/cli.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/test_run_evidence.py`

- [ ] **Step 1: Write failing profile and CLI tests**

The checked-in example profile shape is:

```json
{
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-v4-pro",
  "reasoning_effort": "high",
  "request_token_budget": 40000,
  "capabilities": {
    "max_context_tokens": 1000000,
    "max_output_tokens": 384000,
    "max_tokens_field": "max_tokens",
    "reasoning_levels": ["off", "high"],
    "reasoning_dialect": "deepseek",
    "reasoning_shares_output": true,
    "reserve_ratio_high": null,
    "supports_streaming": true,
    "supports_stream_usage": true,
    "source": "operator-confirmed-official-docs",
    "source_urls": [
      "https://api-docs.deepseek.com/guides/thinking_mode",
      "https://api-docs.deepseek.com/quick_start/pricing/"
    ]
  }
}
```

It contains no key. Tests assert profile parsing plus `NH_LLM_API_KEY` injection, missing key handling for remote endpoints, unsupported high reasoning rejection before run creation, and no key in repr/model dumps/JSONL. The final actual profile may choose another compatible endpoint/model, but it must use the same schema and cite the capability sources for that exact route. CLI tests also cover `nh gate --preflight` making zero client calls/zero run files and `nh gate-inspect` returning the same verdict as the in-memory scorer.

- [ ] **Step 2: Run focused tests and verify missing loader/CLI option**

Run: `uv run pytest tests/test_draft_profile.py tests/test_runner.py -q`

Expected: FAIL because profile loading and `nh gate --provider-profile` do not exist.

- [ ] **Step 3: Implement profile loading and call-plan preflight**

Add `--provider-profile Path` to `nh gate`, defaulting to `synth/m2-provider.json`, plus `--preflight`. Load connection/capability values from JSON, inject only `NH_LLM_API_KEY`, resolve the fixed high M2 plan, and finish all validation before `run_gate()` creates the evidence file. `--preflight` performs every local artifact/configuration/capacity check, prints the sanitized resolved plan, creates no run file, and makes no completion request. Add `nh gate-inspect --run/--db/--project/--ground-truth` as the deterministic read-only wrapper around `inspect_run()`. Preserve environment-only configuration for ordinary future product calls.

- [ ] **Step 4: Update CLI accounting and errors**

Success output says `225 final cells` and prints actual transport attempt count from JSONL. `LengthInvalidError` prints `INVALID` plus trap/arm/repeat, actual length, and preserved partial path. Provider/capability errors remain distinct and do not claim a scientific verdict.

- [ ] **Step 5: Verify CLI tests**

Run: `uv run pytest tests/test_draft_profile.py tests/test_runner.py -q`

Expected: PASS.

- [ ] **Step 6: Commit profile and CLI integration**

```bash
git add src/novel_harness/draft/profile.py tests/test_draft_profile.py \
  synth/m2-provider.example.json src/novel_harness/cli.py tests/test_runner.py \
  tests/test_run_evidence.py
git commit -m "feat: preflight reproducible M2 provider profiles"
```

### Task 9: Add the bilingual product controls without enabling `/draft`

**Files:**
- Modify: `src/novel_harness/api/app.py`
- Modify: `tests/test_api.py`
- Modify: `frontend/src/api/types.ts`
- Create: `frontend/src/components/DraftLengthControls.tsx`
- Create: `frontend/src/components/DraftLengthControls.test.tsx`
- Modify: `frontend/src/components/ChapterPrepPage.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing backend schema and frontend interaction tests**

Backend: OpenAPI for the draft stub contains an optional request body with `language`, `min_units`, `target_units`, and `max_units`, while POST with or without a body still returns the exact 501 milestone response.

Frontend: default Chinese values are 2000/2500/3000; switching to English yields 1200/1500/1800 and `words`; custom values persist in localStorage; the generate button remains disabled and says M2 must PASS; 20,001 Chinese or 12,001 English is rejected in the control.

- [ ] **Step 2: Run tests and observe missing contract/component failures**

Run:

```bash
uv run pytest tests/test_api.py -q
(cd frontend && npm test -- --run DraftLengthControls)
```

Expected: new tests fail.

- [ ] **Step 3: Add the future request schema while preserving 501**

Use `LengthSpec` as an optional body on `draft_stub`; do not add project/store dependencies and do not replace the stub body. This publishes the future contract without activating paid generation.

- [ ] **Step 4: Implement visible gray controls**

Render the language selector, min/target/max inputs, unit label, “不足时最多自动续写一次” note, and disabled generate button on `ChapterPrepPage`. Keep the values local; do not add a draft mutation hook while the route is 501.

- [ ] **Step 5: Run backend/frontend tests and build**

Run:

```bash
uv run pytest tests/test_api.py -q
(cd frontend && npm test)
(cd frontend && npm run build)
```

Expected: PASS.

- [ ] **Step 6: Commit gray product assets**

```bash
git add src/novel_harness/api/app.py tests/test_api.py frontend/src/api/types.ts \
  frontend/src/components/DraftLengthControls.tsx \
  frontend/src/components/DraftLengthControls.test.tsx \
  frontend/src/components/ChapterPrepPage.tsx frontend/src/styles.css
git commit -m "feat: add bilingual draft length controls"
```

### Task 10: Synchronize current documentation and audit metadata

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/UI_ARCHITECTURE.md`
- Modify: `docs/PLAN.md`
- Modify: `docs/adr/README.md`
- Modify: `src/novel_harness/draft/__init__.py`
- Modify: `src/novel_harness/eval/__init__.py`

- [ ] **Step 1: Update current truth only**

Document the two product languages, human units/maxima, fixed M2 Chinese profile, 225 final cells versus 225–450 calls, generic compatible client, capability-gated reasoning, streaming aggregation, immutable JSONL attempts, and `/draft` still 501. State explicitly that English behavior has regression tests but no English experiment.

Do not edit amendments 1–4, ADR 0010's historical body, `docs_dev`, migrations, completed specs/plans, or dated historical snapshots.

- [ ] **Step 2: Refresh authoritative counts mechanically**

Run:

```bash
uv run pytest --collect-only -q
(cd frontend && npm test -- --reporter=dot)
wc -l src/novel_harness/draft/length.py src/novel_harness/draft/capabilities.py \
  src/novel_harness/draft/provider.py src/novel_harness/draft/generate.py \
  src/novel_harness/eval/runner.py
```

Put changing counts only in ARCHITECTURE's authoritative current-status section; elsewhere link to it.

- [ ] **Step 3: Run documentation guards**

Run: `uv run pytest tests/test_doc_numbers.py tests/test_protocol_preregistration.py -q`

Expected: PASS.

- [ ] **Step 4: Commit documentation sync**

```bash
git add README.md CLAUDE.md docs/ARCHITECTURE.md docs/UI_ARCHITECTURE.md docs/PLAN.md \
  docs/adr/README.md src/novel_harness/draft/__init__.py src/novel_harness/eval/__init__.py
git commit -m "docs: synchronize bilingual writer architecture"
```

### Task 11: Full offline verification and implementation-asset commit audit

**Files:**
- Modify only files required by failures found in this task.

- [ ] **Step 1: Run complete Python verification**

```bash
uv run pytest -q
uv run ruff check .
uv build
```

Expected: all tests pass, Ruff exits 0, source and wheel build successfully.

- [ ] **Step 2: Run complete frontend verification**

```bash
(cd frontend && npm test)
(cd frontend && npm run build)
```

Expected: all Vitest tests pass and TypeScript/Vite build succeeds.

- [ ] **Step 3: Run synthetic and protocol checks**

```bash
M2_VERIFY_DIR="$(mktemp -d)"
uv run python -m synth.build --db "$M2_VERIFY_DIR/gate.db" \
  --out "$M2_VERIFY_DIR/ground_truth.json"
uv run python -m synth.leak_selfcheck \
  --ground-truth "$M2_VERIFY_DIR/ground_truth.json"
test ! -e runs
test -z "$(git log --all --format=%H -- 'runs/*.jsonl')"
```

Expected: build/selfcheck green and no real run exists.

- [ ] **Step 4: Audit staged/committed assets and secrets**

```bash
git status --short
git log --oneline -12
git grep -n "sk-" -- ':!docs/superpowers/plans/*' ':!tests/*'
git diff --check
```

Confirm generated `synth/gate.db`, `synth/ground_truth.json`, chapter copies, frontend build output, API keys, and run results are not committed. Commit only narrowly scoped fixes if verification exposed any.

### Task 12: Freeze the actual endpoint profile, run M2, inspect JSONL, and write ADR 0009

**Files:**
- Create from approved values: `synth/m2-provider.json`
- Create at runtime: `runs/<UTC timestamp>.jsonl`
- Create after valid completed run: `docs/adr/0009-m2-kill-gate-verdict.md`
- Modify after verdict: `docs/adr/README.md`, `README.md`, `docs/ARCHITECTURE.md`, `CLAUDE.md`

- [ ] **Step 1: Resolve endpoint authority without exposing the key**

Check only presence:

```bash
test -n "${NH_LLM_API_KEY:-}" && echo "NH_LLM_API_KEY=set" || echo "NH_LLM_API_KEY=missing"
```

Use the chosen `base_url` and `model` to write `synth/m2-provider.json`; copy documented capability values and source URLs, resolve high reasoning and the exact output budget, and commit this non-secret file before inference. Never print or commit the key.

- [ ] **Step 2: Run zero-cost preflight**

```bash
uv run nh gate --help
if test ! -e synth/gate.db && test ! -e synth/ground_truth.json; then
  uv run python -m synth.build
fi
test -e synth/gate.db
test -e synth/ground_truth.json
uv run python -m synth.leak_selfcheck
test ! -e runs
git status --short
```

Then run the defined zero-cost preflight so capability, 40k-class budget, streaming, frozen protocol version, and output path validate without a completion request:

```bash
M2_GATE_PROJECT_ID="$(uv run python -c 'import json; print(json.load(open("synth/ground_truth.json", encoding="utf-8"))["project_id"])')"
M2_GATE_RUN_PATH="runs/m2-base-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
uv run nh gate --db synth/gate.db --project "$M2_GATE_PROJECT_ID" \
  --ground-truth synth/ground_truth.json \
  --provider-profile synth/m2-provider.json --out "$M2_GATE_RUN_PATH" --preflight
test ! -e runs
```

- [ ] **Step 3: Execute the real gate once**

```bash
uv run nh gate --db synth/gate.db --project "$M2_GATE_PROJECT_ID" \
  --ground-truth synth/ground_truth.json \
  --provider-profile synth/m2-provider.json --out "$M2_GATE_RUN_PATH"
```

Do not add overwrite, append, resume, or sample-replacement flags. If a terminal length-invalid record appears, preserve the partial file and stop under amendment 5.

- [ ] **Step 4: Inspect JSONL before reading the verdict as evidence**

Run the committed deterministic checker:

```bash
uv run nh gate-inspect --run "$M2_GATE_RUN_PATH" --db synth/gate.db \
  --project "$M2_GATE_PROJECT_ID" --ground-truth synth/ground_truth.json
```

It validates: one header; exact protocol version; no key; 25 trap IDs; 3 arms; 3 repeats; every final cell has 1–2 attempts; every final Chinese count is 2,000–3,000; no terminal `length_invalid`; exact message/attempt/final ordering; final leak recomputation matches recorded leak; and the expected total cell count is 225. Save its structured summary for ADR 0009; do not hand-edit calculated counts.

- [ ] **Step 5: Write ADR 0009 from actual data only**

Record PASS/KILL/INVALID/INCONCLUSIVE, per-kind counts, McNemar inputs/p-values, discordant pairs, sign stability, length distribution, continuation count, transport-call count, model/endpoint/profile commit, protocol/amendment commits, and the exact decision-table branch. Cite protocol plus amendments 1–5 and ADRs 0010/0011.

- [ ] **Step 6: Mark M2 according to the actual verdict and commit evidence**

Only PASS permits enabling the product `/draft` implementation in a later commit. KILL keeps 501. INVALID preserves evidence and requires instrument repair under a new pre-run decision. INCONCLUSIVE follows the frozen escalation rule. Never label M2 complete merely because 225 cells were attempted.

Run final verification, then commit ADR/evidence/status updates without any API key.
