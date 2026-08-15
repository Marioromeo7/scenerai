"""
Tier B load-test runner — Section 3 of NEXT_PHASE_PLAN.md.

Two modes:
  baseline  Run a handful of clients (1-5) once, sequentially, to establish
            real per-step p50 latency. Per the plan: "don't guess these —
            run a low-concurrency baseline first and set thresholds relative
            to that baseline (e.g. 3x baseline p50)." Writes thresholds.json.

  ramp      Ramp virtual-client concurrency over time at ~90/10 player/creator
            mix, using thresholds.json for latency_breach detection, and
            aggregate into a failure-rate-vs-concurrency curve per category
            (hard_error / latency_breach / silent_bad_state) plus the first
            concurrency tier where each category crosses its failure-rate
            threshold. This is the actual breakpoint-finding run — bigger and
            costlier (real Groq calls at scale) than baseline; run it
            separately once thresholds exist.

Usage:
    pip install httpx
    python run.py baseline --base-url http://localhost:9000 --n 5
    python run.py ramp --base-url http://localhost:9000 --start 5 --step 5 \\
        --max 50 --step-duration 20 --thresholds thresholds.json --out report.json
"""
import argparse
import asyncio
import json
import statistics
import sys
import time

from scenarai_client import player_flow, creator_flow

DEFAULT_THRESHOLDS_PATH = "thresholds.json"
FAILURE_RATE_ALERT = 0.05  # a category "crosses" a concurrency tier at >5% of clients hitting it


def _default_thresholds():
    # Generous placeholders only used if baseline hasn't been run yet —
    # the plan is explicit these shouldn't be trusted as real numbers.
    return {
        "register": 2000, "create_persona": 2000, "browse_public": 1000,
        "create_session": 2000, "session_status_poll": 3000,
        "turn": 45000, "get_history": 1000, "end_session": 2000,
        "create_scenario": 5000, "publish": 2000, "prefab_status_poll": 5000,
        "create_preview_session": 2000,
    }


async def run_baseline(base_url, n):
    print(f"[baseline] running {n} sequential player-flow clients against {base_url}")
    thresholds = _default_thresholds()  # baseline run itself doesn't need real thresholds yet
    results = []
    for i in range(n):
        r = await player_flow(base_url, client_id=i, concurrency_at_start=1, thresholds=thresholds)
        results.append(r)
        status = "OK" if r.succeeded else f"FAIL[{r.failure_category}@{r.failed_step}]"
        print(f"  client {i}: {status}  {json.dumps(r.latencies_ms)}")

    per_step = {}
    for r in results:
        for step, ms in r.latencies_ms.items():
            per_step.setdefault(step, []).append(ms)

    recommended = {}
    print("\n[baseline] per-step latency (ms):")
    for step, vals in sorted(per_step.items()):
        p50 = statistics.median(vals)
        p90 = vals[int(len(vals) * 0.9)] if len(vals) > 1 else vals[0]
        recommended[step] = round(p50 * 3, 1)  # plan's rule of thumb: 3x baseline p50
        print(f"  {step:28s} p50={p50:8.1f}  p90={p90:8.1f}  n={len(vals)}  -> threshold={recommended[step]}")

    failed = [r for r in results if not r.succeeded]
    if failed:
        print(f"\n[baseline] WARNING: {len(failed)}/{n} baseline clients failed — thresholds below are")
        print("           incomplete. Fix the failure before trusting this baseline.")
        for r in failed:
            print(f"    client {r.client_id}: {r.failure_category} @ {r.failed_step} — {r.error_detail}")

    with open(DEFAULT_THRESHOLDS_PATH, "w") as f:
        json.dump(recommended, f, indent=2)
    print(f"\n[baseline] wrote {DEFAULT_THRESHOLDS_PATH} ({len(recommended)} steps)")
    return recommended


async def _run_one(flow_name, base_url, client_id, concurrency, thresholds):
    if flow_name == "player":
        return await player_flow(base_url, client_id, concurrency, thresholds)
    return await creator_flow(base_url, client_id, concurrency, thresholds)


async def run_ramp(base_url, start, step, max_concurrency, step_duration, thresholds):
    print(f"[ramp] {base_url}  {start}->{max_concurrency} step {step}, {step_duration}s per tier, 90/10 player/creator")
    all_results = []
    client_id = 0
    concurrency = start
    tier_reports = []

    while concurrency <= max_concurrency:
        print(f"\n[ramp] tier: concurrency={concurrency}")
        tasks = []
        for i in range(concurrency):
            flow_name = "creator" if (client_id % 10 == 9) else "player"  # ~90/10 mix
            tasks.append(_run_one(flow_name, base_url, client_id, concurrency, thresholds))
            client_id += 1
        tier_start = time.monotonic()
        tier_results = await asyncio.gather(*tasks, return_exceptions=False)
        tier_elapsed = time.monotonic() - tier_start
        all_results.extend(tier_results)

        # latency_breach is observed, not fatal (see ClientResult.record_breach) —
        # tracked as its own rate, separate from the two true failure categories.
        by_category = {"hard_error": 0, "latency_breach": 0, "silent_bad_state": 0}
        for r in tier_results:
            if r.failure_category:
                by_category[r.failure_category] += 1
            if r.latency_breaches:
                by_category["latency_breach"] += 1
        n = len(tier_results)
        rates = {k: v / n for k, v in by_category.items()}
        tier_reports.append({"concurrency": concurrency, "n": n, "elapsed_s": round(tier_elapsed, 1),
                              "counts": by_category, "rates": rates})
        print(f"  done in {tier_elapsed:.1f}s: {by_category}  (rates: "
              f"{ {k: round(v, 2) for k, v in rates.items()} })")

        concurrency += step

    return all_results, tier_reports


def build_report(tier_reports):
    first_crossing = {}
    for cat in ("hard_error", "latency_breach", "silent_bad_state"):
        for t in tier_reports:
            if t["rates"][cat] > FAILURE_RATE_ALERT:
                first_crossing[cat] = t["concurrency"]
                break
        else:
            first_crossing[cat] = None
    return {"tiers": tier_reports, "first_concurrency_crossing_5pct": first_crossing}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)

    b = sub.add_parser("baseline")
    b.add_argument("--base-url", default="http://localhost:9000")
    b.add_argument("--n", type=int, default=5)

    r = sub.add_parser("ramp")
    r.add_argument("--base-url", default="http://localhost:9000")
    r.add_argument("--start", type=int, default=5)
    r.add_argument("--step", type=int, default=5)
    r.add_argument("--max", type=int, default=50, dest="max_concurrency")
    r.add_argument("--step-duration", type=float, default=0)  # informational only; gather() is duration-agnostic
    r.add_argument("--thresholds", default=DEFAULT_THRESHOLDS_PATH)
    r.add_argument("--out", default="report.json")

    args = p.parse_args()

    if args.mode == "baseline":
        asyncio.run(run_baseline(args.base_url, args.n))
        return

    try:
        with open(args.thresholds) as f:
            thresholds = json.load(f)
    except FileNotFoundError:
        print(f"[ramp] {args.thresholds} not found — run `baseline` first. Using generous placeholder thresholds.")
        thresholds = _default_thresholds()

    all_results, tier_reports = asyncio.run(
        run_ramp(args.base_url, args.start, args.step, args.max_concurrency, args.step_duration, thresholds)
    )
    report = build_report(tier_reports)
    report["clients"] = [r.to_dict() for r in all_results]
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n[ramp] wrote {args.out}")
    print("[ramp] first concurrency tier crossing 5% failure rate, per category:")
    for cat, tier in report["first_concurrency_crossing_5pct"].items():
        print(f"  {cat:20s} {tier if tier is not None else 'not reached in this run'}")


if __name__ == "__main__":
    sys.exit(main() or 0)
