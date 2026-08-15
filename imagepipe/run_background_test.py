"""
B1-B4 scene-complexity tests — does adding a real background, more
characters, or a background crowd degrade generation reliability further
than today's single-character-plain-background baseline?

Simpler methodology than run_incremental_build.py's per-attribute
incremental lock: here the full combined prompt (all characters +
background + crowd) is generated in one shot per attempt, and retried
with a new seed up to --max-retries times until every check passes at
once. Locking one attribute at a time across 2-3 characters would mean
8-13 sequential steps per chain — impractically expensive for what's
meant to be a scoping pass, not the deep-dive run_incremental_build.py
already did for the single-character case.

Expectation, stated up front: success rates should be lower than today's
baseline, and that's an informative result, not a bug — multi-character
attribute binding is a well-documented harder case for SD1.5 than the
single-subject case already found to be unreliable.

Usage:
    python run_background_test.py --tier B1 --chains 20 --max-retries 5
    python run_background_test.py --all --chains 20   # run B1..B4 in sequence
"""
import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from visual_profile import NEGATIVE_PROMPT
from multi_subject_profiles import TIERS, build_scene_prompt, build_checks

RESULTS_DIR = Path(__file__).parent / "results"
SAMPLES_DIR = Path(__file__).parent / "sample_images"


def run_tier(tier: str, num_chains: int, max_retries: int, gen_steps: int, save_samples: bool):
    import sd_pipeline
    import attribute_check as ac

    prompt = build_scene_prompt(tier)
    checks = build_checks(tier)
    print(f"\n=== {tier} ===\nprompt: {prompt}")
    print(f"checks: {[c['name'] for c in checks]}")

    chains = [{"id": c, "seed_cursor": c * 1000, "tries": 0, "resolved": False, "answers": None} for c in range(num_chains)]
    pending = list(chains)

    for retry_round in range(max_retries):
        if not pending:
            break
        print(f"  round {retry_round + 1}/{max_retries}: generating {len(pending)} candidates...")
        pipe = sd_pipeline.load_pipeline()
        candidates = {}
        for c in pending:
            seed = c["seed_cursor"]
            c["seed_cursor"] += 1
            c["tries"] += 1
            img = sd_pipeline.generate(pipe, prompt, NEGATIVE_PROMPT, seed=seed, steps=gen_steps)
            candidates[c["id"]] = (img, seed)
        sd_pipeline.unload_pipeline(pipe)

        processor_v, model_v = ac.load_vqa()
        still_pending = []
        for c in pending:
            img, seed = candidates[c["id"]]
            results = ac.check_attributes(processor_v, model_v, img, checks=checks)
            all_pass = all(r["match"] for r in results.values())
            if all_pass:
                c["resolved"] = True
                c["locked_seed"] = seed
                c["answers"] = {k: v["answer"] for k, v in results.items()}
                if save_samples:
                    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
                    img.save(SAMPLES_DIR / f"{tier}_chain{c['id']}.png")
            else:
                c["last_answers"] = {k: v["answer"] for k, v in results.items()}
                still_pending.append(c)
        ac.unload_vqa(processor_v, model_v)
        print(f"    {len(pending) - len(still_pending)}/{len(pending)} passed this round")
        pending = still_pending

    success_count = sum(1 for c in chains if c["resolved"])
    tries_on_success = [c["tries"] for c in chains if c["resolved"]]
    return {
        "tier": tier,
        "prompt": prompt,
        "num_chains": num_chains,
        "max_retries": max_retries,
        "success_count": success_count,
        "success_rate": round(success_count / num_chains, 3),
        "avg_tries_on_success": round(statistics.mean(tries_on_success), 2) if tries_on_success else None,
        "chains": chains,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tier", choices=list(TIERS), default="B1")
    p.add_argument("--all", action="store_true", help="run B1..B4 in sequence")
    p.add_argument("--chains", type=int, default=20)
    p.add_argument("--max-retries", type=int, default=5)
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--no-samples", action="store_true")
    args = p.parse_args()

    tiers = list(TIERS) if args.all else [args.tier]
    results = {}
    for t in tiers:
        results[t] = run_tier(t, args.chains, args.max_retries, args.steps, save_samples=not args.no_samples)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"background_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\n=== SUMMARY ===\nwrote {out_path}")
    for t, r in results.items():
        print(f"  {t}: success={r['success_count']}/{r['num_chains']} ({r['success_rate']*100:.1f}%) "
              f"avg_tries={r['avg_tries_on_success']}")


if __name__ == "__main__":
    main()
