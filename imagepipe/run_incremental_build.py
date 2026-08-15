"""
Incremental attribute-locked generation — follow-up to the session-
consistency pilot, which showed SD1.5 doesn't reliably satisfy every
attribute in a multi-attribute prompt at once (attribute binding is a
known weakness of this model class, see conversation/SESSION_ADVICE.txt).

Instead of asking for everything in one shot, build the prompt up one
attribute at a time. At each step: generate a candidate with the current
seed, VQA-check it against EVERY attribute locked in so far (not just the
newest one — the point is to test whether "same seed, more detail" holds
up cumulatively, not just for the latest addition). If any check fails,
try the next seed. Once a seed satisfies everything so far, lock it in
and move to the next attribute.

This directly measures: (a) how many retries it typically takes to get
each attribute right, (b) whether adding a new attribute breaks ones
already locked in (regression), (c) how far a chain typically gets before
it stalls (some combination the model just can't satisfy within budget).

Runs many independent "chains" in parallel (batched by round, not
literally concurrent) so results are a distribution, not one lucky/unlucky
run. Phase-alternates SD1.5 and BLIP-VQA residency per round (~8s reload
overhead each swap) rather than trying to hold both in the 4GB VRAM
budget at once — see sd_pipeline.py's docstring for why that doesn't fit.

Phase A additions (see conversation): a pose-visibility constraint (fixes
the "eyes closed/turned away, VQA answered anyway" failure mode found in
the session-consistency pilot), crop-zoom verification for eye_color
(now baked into attribute_check.py's ATTRIBUTE_CHECKS, applies
automatically), and Groq-based canonicalization of the clothing field
(fixes the "dark green cloak" -> compound-color attribute-binding
failure, see canonicalize_profile.py). Both new prompt-side fixes are
flag-controlled so a direct before/after comparison against today's
baseline numbers is possible, and so either can be isolated later if the
combined result needs breaking down.

Usage:
    python run_incremental_build.py --chains 20 --max-retries 5 --steps 20
    python run_incremental_build.py --chains 20 --no-pose-constraint --no-canonicalize   # baseline replay
"""
import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from visual_profile import EXAMPLE_PROFILE, NEGATIVE_PROMPT
from attribute_check import ATTRIBUTE_CHECKS

RESULTS_DIR = Path(__file__).parent / "results"
SAMPLES_DIR = Path(__file__).parent / "sample_images"

POSE_CONSTRAINT = "eyes open, looking directly at the viewer, face fully visible, hair not covering the face"

_canonicalized_profile_cache = None


def get_profile(use_canonicalization: bool) -> dict:
    """Canonicalizes the clothing field via Groq exactly once per process
    (not once per generation — matches the prefab-style "pay the LLM cost
    once, reuse many times" pattern already used elsewhere in the engine)."""
    global _canonicalized_profile_cache
    if not use_canonicalization:
        return EXAMPLE_PROFILE
    if _canonicalized_profile_cache is None:
        from canonicalize_profile import canonicalize_profile
        print("canonicalizing profile via Groq (one-time)...")
        _canonicalized_profile_cache = canonicalize_profile(EXAMPLE_PROFILE, ["clothing"])
        print(f"  clothing: {EXAMPLE_PROFILE['clothing']!r} -> {_canonicalized_profile_cache['clothing']!r}")
    return _canonicalized_profile_cache


def build_incremental_prompt(step: int, profile: dict, use_pose_constraint: bool,
                              art_style_fallback: str = "digital painting") -> str:
    """Same boilerplate convention as visual_profile.build_prompt, but only
    including attributes up to and including `step` (0-indexed into
    ATTRIBUTE_CHECKS)."""
    parts = [profile[ATTRIBUTE_CHECKS[i]["field"]] for i in range(step + 1)]
    parts.append(art_style_fallback)
    if use_pose_constraint:
        parts.append(POSE_CONSTRAINT)
    parts.append("portrait, single character, plain background")
    return ", ".join(parts)


def run_incremental_experiment(num_chains: int, max_retries: int, gen_steps: int, save_samples: bool,
                                use_pose_constraint: bool = True, use_canonicalize: bool = True):
    import sd_pipeline
    import attribute_check as ac

    profile = get_profile(use_canonicalize)
    num_attributes = len(ATTRIBUTE_CHECKS)
    # Per-chain state: seed cursor (next untried seed), locked seed for
    # current step, whether still active, and per-step history.
    chains = [{"id": c, "seed_cursor": c * 1000, "active": True, "steps": []} for c in range(num_chains)]
    next_global_seed_offset = num_chains * 1000  # avoid collisions across chains' retry pools

    for step in range(num_attributes):
        prompt = build_incremental_prompt(step, profile, use_pose_constraint)
        checks_so_far = ATTRIBUTE_CHECKS[:step + 1]
        print(f"\n=== step {step + 1}/{num_attributes}: {ATTRIBUTE_CHECKS[step]['name']} ===")
        print(f"prompt: {prompt}")

        pending = [c for c in chains if c["active"]]
        for retry_round in range(max_retries):
            if not pending:
                break
            print(f"  round {retry_round + 1}/{max_retries}: generating {len(pending)} candidates...")
            pipe = sd_pipeline.load_pipeline()
            candidates = {}  # chain_id -> (image, seed_used)
            for c in pending:
                seed = c["seed_cursor"]
                c["seed_cursor"] += 1
                img = sd_pipeline.generate(pipe, prompt, NEGATIVE_PROMPT, seed=seed, steps=gen_steps)
                candidates[c["id"]] = (img, seed)
            sd_pipeline.unload_pipeline(pipe)

            processor_v, model_v = ac.load_vqa()
            still_pending = []
            for c in pending:
                img, seed = candidates[c["id"]]
                results = ac.check_attributes(processor_v, model_v, img, checks=checks_so_far)
                all_pass = all(r["match"] for r in results.values())
                if all_pass:
                    c["steps"].append({
                        "attribute": ATTRIBUTE_CHECKS[step]["name"],
                        "locked_seed": seed,
                        "tries": retry_round + 1,
                        "answers": {k: v["answer"] for k, v in results.items()},
                    })
                    if save_samples and step == num_attributes - 1:
                        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
                        img.save(SAMPLES_DIR / f"incremental_chain{c['id']}_final.png")
                else:
                    still_pending.append(c)
            ac.unload_vqa(processor_v, model_v)
            print(f"    {len(pending) - len(still_pending)}/{len(pending)} passed this round")
            pending = still_pending

        # anything still pending after max_retries stalled at this step
        for c in pending:
            c["active"] = False
            c["stalled_at"] = ATTRIBUTE_CHECKS[step]["name"]

    return chains


def summarize(chains, num_attributes):
    stalled_at_step = [len(c["steps"]) for c in chains]  # how many attributes each chain successfully locked
    full_success = sum(1 for n in stalled_at_step if n == num_attributes)

    per_step_tries = {i: [] for i in range(num_attributes)}
    for c in chains:
        for i, s in enumerate(c["steps"]):
            per_step_tries[i].append(s["tries"])

    step_summary = []
    for i in range(num_attributes):
        tries = per_step_tries[i]
        step_summary.append({
            "attribute": ATTRIBUTE_CHECKS[i]["name"],
            "chains_that_passed": len(tries),
            "avg_tries": round(statistics.mean(tries), 2) if tries else None,
            "max_tries": max(tries) if tries else None,
        })

    return {
        "num_chains": len(chains),
        "num_attributes": num_attributes,
        "full_success_count": full_success,
        "full_success_rate": round(full_success / len(chains), 3),
        "distribution_attributes_reached": {
            str(n): stalled_at_step.count(n) for n in range(num_attributes + 1)
        },
        "per_step": step_summary,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chains", type=int, default=20, help="number of independent incremental-build attempts")
    p.add_argument("--max-retries", type=int, default=5, help="max seed retries per attribute before a chain stalls")
    p.add_argument("--steps", type=int, default=20, help="diffusion steps per generation")
    p.add_argument("--no-samples", action="store_true")
    p.add_argument("--no-pose-constraint", action="store_true", help="disable the eyes-open/face-visible prompt addition")
    p.add_argument("--no-canonicalize", action="store_true", help="disable Groq-based clothing-field canonicalization")
    args = p.parse_args()

    chains = run_incremental_experiment(
        args.chains, args.max_retries, args.steps, save_samples=not args.no_samples,
        use_pose_constraint=not args.no_pose_constraint, use_canonicalize=not args.no_canonicalize,
    )
    summary = summarize(chains, len(ATTRIBUTE_CHECKS))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"incremental_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "chains": chains}, f, indent=2)

    print(f"\n=== SUMMARY ===\nwrote {out_path}")
    print(f"full success: {summary['full_success_count']}/{summary['num_chains']} "
          f"({summary['full_success_rate'] * 100:.1f}%)")
    print(f"attributes-reached distribution: {summary['distribution_attributes_reached']}")
    for s in summary["per_step"]:
        print(f"  {s['attribute']:<14} passed={s['chains_that_passed']:>3}  avg_tries={s['avg_tries']}  max_tries={s['max_tries']}")


if __name__ == "__main__":
    main()
