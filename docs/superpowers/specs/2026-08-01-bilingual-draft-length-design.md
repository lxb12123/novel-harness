# Bilingual draft length and M2 output-profile design

## Goal

Replace the writer's hard-coded Chinese `600–1000 字` prompt and product-wide
`max_tokens=4096` assumption with a language-aware length contract that:

- lets product users request useful Chinese or English output lengths;
- keeps raw token budgeting out of the ordinary user interface;
- preserves one OpenAI-compatible provider boundary across GPT, Claude, DeepSeek,
  OpenRouter, and compatible local endpoints;
- gives the M2 kill-gate a separately frozen Chinese `2000–3000 字` profile; and
- records enough resolved configuration and per-attempt evidence to audit ADR 0009.

The design does not mark M2 complete. M2 remains incomplete until the preregistered
real-model run is complete, its JSONL is inspected, and ADR 0009 records the verdict.

## Decisions already approved

1. The first product release supports length control for Chinese and English only.
2. Product limits default to 20,000 Chinese characters and 12,000 English words.
3. M2 remains a Chinese-only experiment; English is not mixed into its 225 samples.
4. M2 final drafts target 2,000–3,000 Chinese characters.
5. An under-length draft receives at most one deterministic continuation. If the
   combined result is still invalid, the whole experimental run is `INVALID`; the
   sample is never silently dropped or replaced.
6. The provider continues to use the OpenAI Python client. OpenRouter is optional,
   not a product dependency or required endpoint.
7. Reasoning effort is sent only when the selected model/endpoint is known to support
   it. Product defaults to reasoning off; the M2 profile requests `high`.
8. The frozen protocol body is not rewritten. A separately committed amendment 5
   supersedes the old output-length and provider assumptions before the first run.

## Alternatives considered

### A. One language-aware `LengthSpec` (chosen)

The UI and API express language and human length units. A shared core turns those
units into prompt instructions, validation, and a capability-checked token budget.
This keeps the product understandable while preserving one implementation path for
M2 and production.

### B. Separate Chinese and English writer paths

Independent prompts and configurations would be quick initially, but their retry,
budget, logging, and provider behavior would drift. It would also weaken the M2 rule
that the gate must exercise the production writer.

### C. Expose tokens as the universal length unit

Tokens are convenient for providers but are not a stable or useful promise to an
author. The same token count produces different visible lengths across languages and
models, so this would not satisfy user-controlled article length.

## Core length model

Add a focused `draft/length.py` module. It owns length-domain types and pure functions;
it does not import the graph, provider SDK, evaluation scorer, or UI.

### `DraftLanguage`

The first version accepts exactly:

- `zh` — Chinese output measured in `characters`;
- `en` — English output measured in `words`.

Language is explicit in the request. The application does not guess from the prompt,
project contents, locale, model name, or endpoint.

### `LengthSpec`

`LengthSpec` is frozen and contains:

- `language`;
- `min_units`;
- `target_units`;
- `max_units`.

It enforces `1 <= min_units <= target_units <= max_units` and validates the maximum
against the active product policy. The unit is derived from `language`; callers cannot
claim `zh` while passing `words`.

Product defaults are:

| Language | Default range | Default target | Default hard maximum |
|---|---:|---:|---:|
| Chinese | 2,000–3,000 characters | 2,500 | 20,000 characters |
| English | 1,200–1,800 words | 1,500 | 12,000 words |

An open-source deployer may override product defaults and maxima through documented
configuration. Those overrides never mutate the M2 experimental constant.

### Deterministic counting

Chinese length is the count of non-whitespace Unicode code points. Chinese characters,
Latin characters, numbers, and punctuation each count as one; spaces, tabs, and line
breaks do not. This matches the repository's existing deterministic non-whitespace
length convention and avoids a tokenizer dependency.

English length uses a deterministic Unicode word matcher. A word begins and ends with
a Unicode letter or number and may contain internal ASCII/curly apostrophes or hyphens.
Thus `don't`, `state-of-the-art`, and `GPT-5` each count as one word. Standalone
punctuation and whitespace do not count.

`measure(text, spec)` returns a frozen measurement containing the unit, actual count,
and one of `under`, `within`, or `over`. Counting never calls an LLM or language
detector.

## Product request and response contract

The future `/draft` request accepts language plus an explicit range/target. Presets can
populate those values, while an advanced custom control may edit them directly. The UI
shows `字` for `zh` and `words` for `en`; it does not expose `max_tokens`.

The draft result contains:

- complete text;
- requested `LengthSpec`;
- actual unit count and length status;
- number of provider calls;
- aggregate token usage when the provider reports it;
- each attempt's finish reason; and
- whether the final response appears truncated.

Product generation normally performs one model call. If the first draft is below
`min_units`, it may make exactly one continuation call. Therefore one user action uses
one call normally and at most two calls. The UI states this before generation and shows
the actual call count afterward.

If the product result remains under-length, exceeds the requested maximum, or is
truncated, the complete text is returned with a clear status. Product code does not
silently throw away paid content or cut it at an arbitrary character boundary.

The HTTP route remains the existing 501 stub until M2 returns PASS. Engine, request
types, documentation, and disabled UI assets may land before that verdict; the route
must not pretend the product has been released.

## Prompt assembly

`assemble()` receives a required `LengthSpec`. Language and length instructions live in
the shared base prompt, before the X0/X1/X2 form-specific section. The same object is
passed to all arms, preserving the invariant that only constraint presentation differs.

The Chinese and English house-style templates state the requested language and exact
range in their own language. They share the same behavioral requirements: write the
scene once, finish coherently, do not summarize the prompt, and do not discuss the
length instruction.

The M2 runner uses a module-level frozen experimental profile:

```text
language=zh
min_units=2000
target_units=2500
max_units=3000
```

This profile is not read from product environment variables. `previous_tail=800`
continues to describe input context and is not enlarged with the output range.

## Generation orchestration and continuation

Add a small production writer/orchestration module used by both the M2 runner and the
future `/draft` route. It accepts already assembled messages, a frozen `LengthSpec`, a
frozen resolved call plan, and an injectable client. It returns the final draft plus all
attempt records.

The flow is deterministic:

1. Make the initial completion call.
2. Measure the returned visible text with the selected language rule.
3. If the text is at least `min_units`, stop; never retry based on leak content, style,
   quality, or arm identity.
4. If it is below `min_units`, append the original response as an assistant message and
   append one fixed language-specific continuation instruction. The instruction says to
   continue directly, without restarting, recapping, or commenting on the request.
5. Make one continuation call using the same provider/model/reasoning settings.
6. Concatenate the two visible texts in order and measure the final draft.

There is no third call, sample replacement, or hidden rewrite. The continuation
decision depends only on the deterministic visible-length count. Initial and
continuation text remain separately available in the audit record.

## Provider capabilities and resolved call plan

The OpenAI Python client remains the sole SDK boundary. Provider behavior is isolated
behind deterministic capability resolution and wire adapters rather than model-name
conditionals spread through writer code.

### `ProviderCapabilities`

The frozen capability description records:

- maximum context tokens, if known;
- maximum output/completion tokens, if known;
- supported reasoning controls and levels;
- the provider's reasoning wire dialect;
- whether reasoning shares the completion-token pool;
- any documented reasoning reserve ratio used for planning;
- streaming support and usage-report behavior; and
- the capability source and registry/adapter version.

Resolution priority is:

1. explicit operator capability override;
2. an exact endpoint/model registry entry;
3. standardized endpoint metadata when it actually publishes the fields;
4. `unknown`.

The application does not issue a paid inference request merely to probe support. An
unknown capability may proceed only when reasoning is `off`; any requested non-off
level fails rather than being silently omitted. If M2 requests `high` and support is not
established, preflight fails before creating a run or spending tokens.

### Reasoning transport

The user-facing neutral setting is `off`, `low`, `medium`, or `high`. An adapter maps it
to the selected compatible endpoint, for example a top-level OpenAI-compatible field or
an `extra_body` object. Unsupported fields are omitted rather than sprayed at every
provider. The resolved neutral setting, wire dialect, mapping version, and actual wire
shape excluding secrets are recorded.

Product defaults to `off`. The M2 profile requests `high`, preserving the agreed
high-reasoning experimental treatment while remaining SDK-neutral. Changing M2 to an
endpoint/model that cannot honor `high` requires a new pre-run protocol decision; it is
not a silent fallback.

### Length-to-token planning

Token limits are an internal capacity plan, not the user's length control. A versioned
pure planner first reserves visible-output capacity:

```text
zh_visible = ceil(max_units * 2.0) + 1024
en_visible = ceil(max_units * 2.0) + 1024
```

The factor is deliberately generous because tokenization varies. If reasoning shares
the completion pool, total capacity is:

```text
total = ceil(visible / (1 - reserve_ratio))
```

The reserve ratio comes from the resolved capability/adapter, not a universal claim
about every model. A documented 80% `high` reserve gives the M2 profile roughly 35,120
tokens, so the resolved experimental budget may round upward to 40,000 tokens when the
selected model supports it.

The planner never silently clamps to a smaller model maximum. If visible output plus
reasoning cannot fit the model's maximum output, or the estimated prompt plus completion
cannot fit the context window, preflight raises an actionable error suggesting a shorter
request, lower/off reasoning, or a more capable model.

`NH_LLM_MAX_TOKENS=4096` is no longer the product-wide default length ceiling. Advanced
operator overrides describe endpoint capability or an explicitly frozen experimental
call plan; they do not change what the user asked to write.

### Streaming

Resolved budgets above 16,000 tokens use the compatible client's streaming path. The
provider layer aggregates text, usage, model identity, and finish reason into the same
result shape used by non-streaming calls. Runner and scorer continue to receive one
complete attempt at a time. User-facing SSE streaming is not required while `/draft`
remains a 501 stub.

## M2 experimental validity and call accounting

The original `25 traps × 3 arms × 3 repeats` defines 225 final evaluation cells, not a
promise that transport call count must always equal 225.

A successful base run contains 225 valid final drafts. Each cell starts with one model
call and may use one length-only continuation, so a completed run uses 225–450 calls.
The theoretical 450 occurs only if every initial response is under 2,000 characters and
every continuation succeeds.

For M2, a final cell is invalid when any of these is true:

- combined Chinese length is below 2,000;
- combined Chinese length is above 3,000;
- the last attempt ends with a provider length-limit finish reason; or
- the attempt text or deterministic length measurement cannot be persisted.

Provider usage fields may legitimately be unavailable; they are recorded as `null` and
do not by themselves invalidate a run.

The runner writes the attempt and an explicit `length_invalid` record, flushes it, and
stops the run to avoid spending more money on a known-invalid instrument. The partial
JSONL remains immutable. It does not delete the cell, draw a replacement, or feed the
partial data to the preregistered scorer.

Longer output creates more opportunities to hit a tell than the former approximately
800-character output. Amendment 5 must state this material protocol change. Because the
same frozen profile and continuation policy apply to all three arms, the treatment is
symmetric, but the design does not assert in advance that absolute rates or deltas are
unchanged. Existing statistical thresholds remain untouched.

## Evidence recording

The JSONL header adds structured fields for:

- protocol version including amendment 5;
- full M2 `LengthSpec` and counting-rule version;
- continuation policy and maximum attempts;
- requested and effective reasoning;
- capability source, model limits, adapter version, and wire dialect;
- visible-output estimate, total resolved token budget, and stream mode; and
- the existing provider configuration with the API key removed.

Each attempt record contains:

- trap, arm, repeat, and attempt number;
- the exact messages sent;
- raw returned text;
- measured unit/count/status;
- model, usage, and finish reason; and
- whether another attempt was deterministically required.

A final-cell record contains the concatenated text, final measurement, attempt count,
and leak result. The leak detector scores the entire concatenated final text, never just
the last segment. No API key, authorization header, or secret environment value is
recorded.

When the product route is eventually enabled, the same effective language, range,
budget, reasoning, attempt, usage, and finish metadata is persisted in the existing
`model_call.params_json` audit field. Historical call parameters cannot be reconstructed
later, so product logging cannot be deferred past route activation.

## Protocol and documentation changes

Before any new real-model call:

1. Create `docs/EVAL_PROTOCOL_AMENDMENT_5.md`.
2. Add a fifth link only in the informational header above `EVAL_PROTOCOL.md` section 1;
   sections 1–8 remain byte-for-byte equal to commit `0393088`.
3. State that amendments 1–4 predate both `synth/` and the first run, while amendment 5
   is later than `synth/` but still earlier than every `runs/*.jsonl` and inference call.
4. Freeze the Chinese M2 profile, count rule, one-continuation policy, 225 final-cell /
   450 maximum-call accounting, validity rule, reasoning requirement, and evidence
   fields.
5. State explicitly that the 800-character `previous_tail` is input context and remains
   unchanged.
6. State that English support is a product contract outside the inferential scope of
   this Chinese experiment.
7. Update `PROTOCOL_VERSION` to name amendment 5.
8. Add ADR 0011 to extend ADR 0010's writer boundary with `LengthSpec`, capability
   resolution, and continuation while preserving ADR 0010's history.

Active documentation must supersede PLAN's “approximately 800-character output” and
Claude-native/provider-specific wording, document the generic compatible-client policy,
and update references from four to five amendments. Historical snapshots, amendments
1–4, frozen protocol sections 1–8, old migrations, and completed design/plan artifacts
remain untouched.

`PLAN.md`'s “previous scene tail 800 characters” is not the old output limit. It must not
be globally replaced. If current code does not actually enforce the input-tail bound,
that separate mismatch is fixed and tested without enlarging the bound.

## Error handling

- Unknown language, invalid ranges, and product-policy overflow fail before prompt
  assembly.
- Unsupported requested reasoning and insufficient model capacity fail before run-file
  creation or model calls.
- Provider/network failures preserve already flushed JSONL evidence and remain distinct
  from protocol `INVALID`.
- A length-invalid M2 cell emits a terminal audit record and stops without scoring.
- Product over/under-length output is returned with status rather than discarded.
- Existing run-file exclusive creation remains the first evidence-preservation guard;
  no overwrite, append, or resume mode is added.

## Test strategy

Implementation follows test-driven development. Required regression coverage includes:

### Length domain

- Chinese whitespace, punctuation, Latin text, digits, and supplementary Unicode code
  points;
- English apostrophes, hyphens, numbers, punctuation, and Unicode letters;
- range ordering and exact 20,000-character / 12,000-word boundaries;
- invalid language and deployer override behavior; and
- the M2 constant remaining unchanged under product environment overrides.

### Prompt and orchestration

- Chinese and English instructions contain the exact requested range;
- all three arms receive the same frozen `LengthSpec` and shared base text;
- a within-range initial response makes exactly one call;
- an under-length response makes exactly one fixed continuation;
- no third attempt or leak/content-based retry is possible;
- concatenation and final measurement are deterministic; and
- product over/under results are returned without truncation.

### Provider and capacity

- capability override/registry/unknown precedence;
- neutral `off` produces provider-effective off: omit only where omission is documented
  to disable thinking, otherwise send that dialect's explicit `none`/`disabled` value;
- M2 high reasoning rejected when unsupported;
- visible and shared-reasoning budget formulas, rounding, and model-limit failures;
- non-streaming and streaming aggregation produce the same result contract;
- exact OpenAI-client serialization via mock transport, not only a permissive fake; and
- no provider-specific field leaks into an incompatible endpoint.

### Runner and evidence

- header contains the complete length/capability/reasoning/call plan;
- attempt records contain exact messages, text, measurements, usage, and finish reason;
- leak scoring uses the concatenated final text;
- a successful dry run has the expected 225-cell shape;
- short/long/truncated final output writes `length_invalid`, stops, and never reaches
  scoring or sample replacement;
- every arm/repeat shares the same resolved initial call plan; and
- existing exclusive-create and partial-flush evidence guards remain green.

### Documentation guards

- the frozen protocol body from section 1 still matches `0393088` byte-for-byte;
- amendment 5 and ADR 0011 exist before a real run;
- current references say five amendments without rewriting historical statements; and
- M2 remains marked incomplete until JSONL inspection and ADR 0009.

## Delivery order

1. Commit this approved design document by itself.
2. Commit amendment 5, ADR 0011, PLAN supersession, and protocol-reference updates while
   proving that no run exists.
3. Implement length, capability, provider, orchestration, runner, UI-contract, tests,
   and current-documentation changes.
4. Run the complete offline verification suite and inspect the exact diff.
5. Configure and commit a non-secret endpoint/model/capability profile that freezes the
   effective M2 budget before inference. Never commit an API key.
6. Re-run synthetic selfcheck and preflight validation, then execute the real M2 run.
7. Inspect every JSONL structural invariant and the resulting decision inputs.
8. Write ADR 0009 from the committed evidence and update M2 status according to the
   actual preregistered verdict.

## Out of scope

- Languages other than Chinese and English
- Automatic language detection
- Replacing the OpenAI-compatible client with native provider SDKs
- Mixing English samples into the Chinese M2 experiment
- Changing M2 traps, repeats, statistical thresholds, or tell-based scoring
- Silently clamping user length, truncating paid text, or replacing invalid samples
- Enabling the public `/draft` route before M2 PASS
- Claiming that Chinese M2 evidence validates English generation quality
