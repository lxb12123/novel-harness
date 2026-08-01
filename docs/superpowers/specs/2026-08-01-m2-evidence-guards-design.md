# M2 evidence guards design

## Goal

Close the two evidence-integrity gaps found before the first real M2 kill-gate run, without changing the preregistered experiment, thresholds, prompt forms, or scoring logic.

## Decisions

### Ground-truth freshness

`synth/leak_selfcheck.py` must treat `booklet.toml` as the source declaration and `ground_truth.json` as its derived artifact. For every trap ID, these source fields must match exactly in both files:

- `kind`
- `chapter`
- `cast` (including order)
- `target`
- `goal`
- `prior`
- `reference`

The existing ID-set, chapter-count, derived-boundary, and tell checks remain. A mismatch produces a condition-2 error naming the trap and field and instructing the maintainer to rebuild. This is an exact structural comparison; it introduces no semantic judgment.

### Run-file preservation

`run_gate()` must create `out_path` exclusively. If the target already exists, it must raise an actionable `ValueError` before calling `scene_view()` or the model, and it must leave the existing bytes unchanged. The check must be race-safe, so the file open itself uses exclusive creation rather than a separate `Path.exists()` precheck.

There is intentionally no overwrite flag, append mode, or resume mode. Those behaviors would complicate the meaning of a preregistered run and could merge records produced under different configurations.

## Data flow and errors

1. `load_booklet()` and `GroundTruth.model_validate_json()` validate both artifacts.
2. The selfcheck joins traps by ID and compares every source field before checking derived boundary/tell properties.
3. `run_gate()` validates config, repeats, trap IDs, and then exclusively creates the JSONL.
4. An existing path becomes a Chinese `ValueError`; the CLI already maps `ValueError` to a concise non-traceback failure.
5. Once the file is created, the existing write-and-flush behavior remains unchanged so partial paid work is preserved after a provider failure.

## Tests

- A stale ground truth whose `goal` differs from the booklet must fail condition 2. This field was chosen because the old check missed it while all other selfcheck conditions still passed.
- An existing JSONL containing sentinel evidence must make `run_gate()` fail, preserve the sentinel byte-for-byte, and make zero model calls.
- Existing well-formed booklet and full dry-run tests remain green.

## Documentation and preregistration order

Documentation must state the expanded freshness comparison and exclusive run-file creation, correct existing M2 status drift, and update the single authoritative pytest count after verification.

The amendment, ADR 0010, implementation assets, tests, and these guard fixes must be committed before any real model call. Only after that commit may the 225-generation run occur. ADR 0009 is written from the committed JSONL and the preregistered `decide()` result afterward.

## Out of scope

- Changing prompt content, thresholds, arm definitions, repeat counts, or the protocol version
- Adding run resume/append/force behavior
- Broad hardening of malformed hand-written ground-truth JSON beyond the two evidence guards

