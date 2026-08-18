# Turn-quality judge (Section 2 of NEXT_PHASE_PLAN.md)

Offline, non-blocking quality scorer — distinct from `guard_response`, which
is the hard, binary, production-blocking constraint enforcer that runs on
every real turn. This runs golden scenarios through the real engine
(`engine_init` -> `engine_step`, full init) and scores every generated turn
with a separate judge model (`openai/gpt-oss-120b` via Groq — a different
model family from the engine's own generation models, which is what matters
for avoiding same-model self-grading bias).

## Rubric

Five independent 1-5 axes per turn — deliberately not blended into one
score, so a regression shows up on the specific axis it broke:
`continuity_fidelity`, `sovereignty_adherence`, `voice_consistency`,
`pacing`, `safety_adherence`.

## Golden scenarios

Five scenarios, each targeting one specific edge case (see
`golden_scenarios.py` for the full script of each):
`collective_npcs`, `ambiguous_pronouns`, `npc_sovereignty_stress`,
`continuity_drift` (16 turns, crosses `MAX_RAW_TURNS`=14 to test post-
compression recall), `content_filter_adherence`.

## Usage

```
cd <project root>          # .env must be resolvable from CWD
python judge/run_eval.py --only collective_npcs   # smoke-test the harness on one short scenario first
python judge/run_eval.py                          # full suite — real engine inits (15-20 calls each)
                                                    # + turns + judge calls; run sequentially, expect
                                                    # this to take a while under the current Groq TPM limit
```

Writes a timestamped JSON report to `docs/eval-runs/<UTC-timestamp>.json`
with per-scenario and overall per-axis means, plus every captured turn and
its individual scores/rationale.

## What this is NOT

- Not fed back into the production guard pipeline — offline quality trend,
  not an online safety gate.
- Not a substitute for `guard_response` — that's fail-closed and binary by
  design; this is graded and advisory.

## Regression floor

**Stale as of the Groq model migration — do not compare a fresh run against
this table.** `llama-3.1-8b-instant`, the engine_model this floor was
measured against, was decommissioned by Groq (see `ai_service.py`'s
`ENGINE_MODELS` comment); the current default is `openai/gpt-oss-20b`.
Any run from now on necessarily uses the new model, and different models
can have systematically different baseline quality on these axes for
reasons that have nothing to do with a real regression in engine.py/
inference.py — comparing against a different model's floor risks reading
model-attributable variance as a false regression (or a false pass). A
fresh baseline run against the current default model is needed before this
table means anything again; until then, treat scores as informative on
their own, not as pass/fail against these specific numbers.

Set from the first baseline run: `docs/eval-runs/20260805T200444Z.json`
(engine_model=`llama-3.1-8b-instant`, judge_model=`openai/gpt-oss-120b`).
Overall per-axis means from that run — a future run scoring below these on
any axis is a regression on that axis:

| Axis | Baseline mean | Floor |
|---|---|---|
| continuity_fidelity | 4.24 | 4.24 |
| sovereignty_adherence | 4.86 | 4.86 |
| voice_consistency | 3.97 | 3.97 |
| pacing | 4.21 | 4.21 |
| safety_adherence | 4.97 | 4.97 |

These are the raw baseline means, not means-minus-noise-tolerance — there's
only one run so far, so there's no real data yet on run-to-run judge
variance to build a tolerance band from. Revisit once a second baseline run
exists: if scores swing meaningfully on a re-run with no code changes, that
variance (not zero) becomes the real floor margin.

**Two findings from this baseline are already worth acting on, independent
of the floor:**
- `voice_consistency` is the weakest axis by a clear margin (3.97 vs. 4.2-5.0
  elsewhere) — recurred across multiple unrelated scenarios, not a fluke of
  one scenario. Worth investigating why character voice drifts specifically,
  before touching prompts elsewhere.
- `npc_sovereignty_stress` and `ambiguous_pronouns` are the two lowest-
  scoring scenarios on `continuity_fidelity` (2.33 and 2.67) — both are
  exactly the edge cases they were designed to stress. This is the harness
  doing its job: it found real, scenario-specific weak points, not just
  produced a flat wall of 5s.

A real bug was also found and fixed via this baseline run, unrelated to the
floor itself: `check_sovereignty` (engine/inference.py) failed OPEN on a
parse/call exception (`return {'clean': True, ...}`), contradicting the
fail-closed design of the rest of the guard system. A malformed validator
response during this run hit that exact path live. Fixed to fail closed;
covered by tests/test_inference.py::TestCheckSovereignty.
