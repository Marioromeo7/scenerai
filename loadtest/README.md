# Tier B load test (Section 3 of NEXT_PHASE_PLAN.md)

Headless HTTP client that drives the player and creator flows directly
(no Flutter/emulator layer — that's Tier A, not built yet). Finds the
concurrency level where the backend breaks and which of three failure
categories it breaks with.

## Important caveat: rate limiting is per source IP

`slowapi`'s `Limiter(key_func=get_remote_address)` in `backend/main.py` keys
on source IP, not per-user. Every virtual client this harness spawns shares
this machine's IP, so `register` (5/min), `login` (10/min), `create_session`
(20/min), and `turn` (60/min) limits apply to the **whole test run**, not
per client. At even modest concurrency you will see `hard_error` (HTTP 429)
long before any real backend capacity limit — that's an artifact of the
harness sharing one IP, not a genuine breakpoint. Real Tier A (distinct
emulator IPs) doesn't have this confound. Read `hard_error` results from
this Tier B harness with that in mind, or temporarily raise the relevant
`@limiter.limit(...)` values in `main.py` for a test run.

## Usage

```
pip install -r requirements.txt

# 1. Baseline — run first, always. Establishes real per-step p50 latency
#    and writes thresholds.json (3x p50 per step, per the plan's rule of thumb).
python run.py baseline --base-url http://localhost:9000 --n 5

# 2. Ramp — the actual breakpoint search. Bigger and costlier (real Groq
#    calls at scale) than baseline; a separate, later step.
python run.py ramp --base-url http://localhost:9000 \
    --start 5 --step 5 --max 50 --thresholds thresholds.json --out report.json
```

`report.json` contains per-tier failure rates for `hard_error`,
`latency_breach`, and `silent_bad_state`, plus the first concurrency tier
where each category crosses a 5% failure rate, plus every individual
client's structured result record (matches the schema in the plan's
Section 3.4).
