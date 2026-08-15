"""
Judge harness runner — Section 2.2 of NEXT_PHASE_PLAN.md.

Replays each golden scenario against the REAL engine (engine_init ->
engine_step loop, full init — not the prefab fast-path, since this is
testing engine.py/inference.py quality directly), captures every turn, then
scores each turn against the rubric with a separate judge model
(openai/gpt-oss-120b, distinct from the engine's own generation models).

Runs everything sequentially — never concurrent — deliberately. A same-day
finding (see SESSION_ADVICE.txt section 11) is that this Groq key's 6000 TPM
budget collapses under concurrent contention; sequential execution avoids
that thundering-herd pattern and lets the existing graceful backoff
(engine/call.py, judge_model.py) actually work as intended.

Usage:
    python run_eval.py                          # one full golden suite run
    python run_eval.py --only collective_npcs    # one scenario, for smoke-testing the harness itself
    python run_eval.py --repeats 5               # repeat the full suite 5 times (different seeds — temp>0
                                                   # on both engine and judge calls means each rep genuinely
                                                   # differs) to measure real run-to-run noise, not guess at
                                                   # it. If noise hasn't settled by 5, keeps going one rep at
                                                   # a time up to --max-repeats before stopping regardless —
                                                   # an explicit stop condition, not an unbounded loop.
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from golden_scenarios import GOLDEN_SCENARIOS  # noqa: E402

from config import settings  # noqa: E402
from ai_service import engine_init, engine_step, DEFAULT_ENGINE_MODEL  # noqa: E402
from judge_model import score_turn, make_client, RUBRIC_AXES  # noqa: E402

EVAL_RUNS_DIR = Path(__file__).resolve().parents[1] / "docs" / "eval-runs"

# A rep-to-rep stdev above this on any axis (1-5 scale) counts as "still noisy"
# — chosen explicitly here, not measured, since this is the number that decides
# when to stop measuring noise in the first place. 0.3 is roughly "more than a
# third of a point of swing on a 5-point scale with no code change" — loose
# enough to tolerate real judge variance, tight enough to mean something.
NOISY_STDEV_THRESHOLD = 0.3


async def replay_scenario(scenario):
    """engine_init -> engine_step loop. Returns list of captured turn records."""
    print(f"  [init] {scenario['key']} — full init (15-20 LLM calls)...")
    t0 = time.monotonic()
    engine_state = await engine_init(
        char_name=scenario["char_name"],
        char_pronouns=scenario["char_pronouns"],
        char_title=scenario["char_title"],
        char_personality=scenario["char_personality"],
        greeting=scenario["greeting"],
        player_name=scenario["player_name"],
        player_pronouns=scenario["player_pronouns"],
        engine_model=DEFAULT_ENGINE_MODEL,
        content_filter=scenario["content_filter"],
    )
    print(f"  [init] done in {time.monotonic() - t0:.1f}s")

    captured = []
    for i, player_input in enumerate(scenario["turns"]):
        turn_number = i + 1
        print(f"  [turn {turn_number}/{len(scenario['turns'])}] {player_input[:60]}...")
        t0 = time.monotonic()
        result = await engine_step(engine_state, player_input, engine_model=DEFAULT_ENGINE_MODEL)
        elapsed = time.monotonic() - t0
        engine_state = result["engine_state"]
        pinned = engine_state.get("memory", {}).get("pinned", [])
        captured.append({
            "turn_number": turn_number,
            "player_input": player_input,
            "response": result["response"],
            "sovereign": result["sovereign"],
            "violations": result["violations"],
            "pinned_facts_at_turn": list(pinned),
            "elapsed_s": round(elapsed, 1),
        })
        print(f"    -> {elapsed:.1f}s, sovereign={result['sovereign']}, response: {result['response'][:100]}")
    return captured


def judge_turns(client, scenario, captured):
    """Sequential judge scoring — one call per turn, real backoff on rate limits."""
    scored = []
    for turn in captured:
        print(f"  [judge] scenario={scenario['key']} turn={turn['turn_number']}...")
        try:
            scores, rationale = score_turn(
                client,
                char_personality=scenario["char_personality"],
                pinned_facts=turn["pinned_facts_at_turn"],
                content_filter=scenario["content_filter"],
                player_input=turn["player_input"],
                response=turn["response"],
                turn_number=turn["turn_number"],
            )
        except Exception as e:
            print(f"    judge FAILED for this turn: {e}")
            scores, rationale = {axis: None for axis in RUBRIC_AXES}, f"judge call failed: {e}"
        scored.append({**turn, "scores": scores, "rationale": rationale})
        print(f"    scores: {scores}")
    return scored


def aggregate(all_scored_by_scenario):
    """Per-scenario and per-axis averages, ignoring null scores (failed judge calls)."""
    per_scenario = {}
    overall_by_axis = {axis: [] for axis in RUBRIC_AXES}
    for key, turns in all_scored_by_scenario.items():
        by_axis = {axis: [] for axis in RUBRIC_AXES}
        for t in turns:
            for axis in RUBRIC_AXES:
                v = t["scores"].get(axis)
                if v is not None:
                    by_axis[axis].append(v)
                    overall_by_axis[axis].append(v)
        per_scenario[key] = {
            axis: round(statistics.mean(vals), 2) if vals else None
            for axis, vals in by_axis.items()
        }
    overall = {
        axis: round(statistics.mean(vals), 2) if vals else None
        for axis, vals in overall_by_axis.items()
    }
    return per_scenario, overall


async def run_once(client, only_key, run_index=None):
    scenarios = [s for s in GOLDEN_SCENARIOS if only_key is None or s["key"] == only_key]
    if not scenarios:
        print(f"No scenario matches --only {only_key}")
        return None

    tag = f" (rep {run_index})" if run_index is not None else ""
    all_scored = {}
    for scenario in scenarios:
        print(f"\n=== scenario: {scenario['key']}{tag} — {scenario['description'][:80]} ===")
        captured = await replay_scenario(scenario)
        scored = judge_turns(client, scenario, captured)
        all_scored[scenario["key"]] = scored

    per_scenario, overall = aggregate(all_scored)

    EVAL_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = EVAL_RUNS_DIR / f"{stamp}.json"
    report = {
        "timestamp": stamp,
        "run_index": run_index,
        "engine_model": DEFAULT_ENGINE_MODEL,
        "judge_model": "openai/gpt-oss-120b",
        "scenarios_run": [s["key"] for s in scenarios],
        "per_scenario_axis_means": per_scenario,
        "overall_axis_means": overall,
        "turns": all_scored,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== RESULTS{tag} ===")
    print(f"wrote {out_path}")
    print("Per-scenario axis means:")
    for key, axes in per_scenario.items():
        print(f"  {key}: {axes}")
    print(f"Overall axis means: {overall}")
    return {"path": str(out_path), "overall_axis_means": overall, "per_scenario_axis_means": per_scenario}


def _noise_stats(overall_means_list):
    """Per-axis mean/stdev/min/max across repeated runs' overall_axis_means."""
    stats = {}
    for axis in RUBRIC_AXES:
        vals = [m[axis] for m in overall_means_list if m.get(axis) is not None]
        if len(vals) < 2:
            stats[axis] = {"mean": vals[0] if vals else None, "stdev": None, "min": None, "max": None, "n": len(vals)}
            continue
        stats[axis] = {
            "mean": round(statistics.mean(vals), 3),
            "stdev": round(statistics.stdev(vals), 3),
            "min": min(vals), "max": max(vals), "n": len(vals),
        }
    return stats


async def run_repeats_async(only_key, target, max_repeats):
    client = make_client(settings.groq_api_key)
    runs = []
    n = 0
    while n < max_repeats:
        n += 1
        print(f"\n{'#' * 70}\n# REP {n}/{max_repeats} (target {target})\n{'#' * 70}")
        result = await run_once(client, only_key, run_index=n)
        if result is None:
            return
        runs.append(result)

        if n < target:
            continue  # always do at least `target` reps before checking noise

        stats = _noise_stats([r["overall_axis_means"] for r in runs])
        noisy_axes = [axis for axis, s in stats.items() if s["stdev"] is not None and s["stdev"] > NOISY_STDEV_THRESHOLD]
        print(f"\n--- noise check after {n} reps ---")
        for axis, s in stats.items():
            flag = "  <- NOISY" if axis in noisy_axes else ""
            print(f"  {axis}: mean={s['mean']} stdev={s['stdev']} min={s['min']} max={s['max']}{flag}")

        if not noisy_axes:
            print(f"\nStop condition met: all axes below stdev {NOISY_STDEV_THRESHOLD} after {n} reps.")
            break
        if n >= max_repeats:
            print(f"\nStop condition: reached max_repeats={max_repeats} with {noisy_axes} still above "
                  f"stdev {NOISY_STDEV_THRESHOLD}. Reporting honestly as unresolved noise, not forcing a number.")
            break
        print(f"Still noisy on {noisy_axes} — running another rep ({n + 1}/{max_repeats}).")

    stats = _noise_stats([r["overall_axis_means"] for r in runs])
    summary = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "n_reps": len(runs),
        "target_reps": target,
        "max_repeats": max_repeats,
        "noisy_stdev_threshold": NOISY_STDEV_THRESHOLD,
        "per_axis_stats": stats,
        "run_reports": [r["path"] for r in runs],
    }
    summary_path = EVAL_RUNS_DIR / f"NOISE_SUMMARY_{summary['timestamp']}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\n=== NOISE SUMMARY ({len(runs)} reps) ===")
    print(f"wrote {summary_path}")
    for axis, s in stats.items():
        print(f"  {axis}: mean={s['mean']} stdev={s['stdev']} (n={s['n']})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", default=None, help="run a single scenario by key, for smoke-testing the harness")
    p.add_argument("--repeats", type=int, default=None,
                    help="repeat the full suite this many times (minimum) to measure run-to-run noise")
    p.add_argument("--max-repeats", type=int, default=None,
                    help="if noise hasn't settled by --repeats, keep going up to this many total reps before stopping regardless (default: 2x --repeats)")
    args = p.parse_args()

    if args.repeats:
        max_repeats = args.max_repeats or args.repeats * 2
        asyncio.run(run_repeats_async(args.only, args.repeats, max_repeats))
    else:
        client = make_client(settings.groq_api_key)
        asyncio.run(run_once(client, args.only))


if __name__ == "__main__":
    main()
