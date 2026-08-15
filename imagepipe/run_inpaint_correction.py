"""
Phase B: inpainting-based correction — targets exactly what Phase A
(whole-image rejection sampling) hit a ceiling on. Instead of re-rolling
the ENTIRE image hoping a new seed satisfies every attribute at once,
generate once with the full prompt, then surgically inpaint-fix just the
attributes that failed their check, on the SAME image, leaving everything
already-correct untouched.

Uses the ORIGINAL, non-canonicalized profile text ("dark green traveling
cloak over leather armor") — Phase A found canonicalization ("olive
traveling cloak, brown leather armor") made cloak_color measurably worse
(90% -> 25-30%), likely by breaking the "cloak OVER armor" compositional
link, so this deliberately doesn't repeat that mistake.

Only tests hair_color, cloak_color, eye_color — scar_mark has no mask
region defined (attribute_check.py never established it reliably renders
at all, independent of masking, so it isn't a fair inpainting test case
yet).

Baseline to beat (today's original incremental-build run, whole-image
rejection sampling, no fixes): hair 100%, cloak 90% (avg 2.6 tries),
eye_color ~5.5%.

Usage:
    python run_inpaint_correction.py --chains 20 --max-inpaint-retries 5 --steps 20
"""
import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from visual_profile import EXAMPLE_PROFILE, NEGATIVE_PROMPT, build_prompt
from attribute_check import ATTRIBUTE_CHECKS

RESULTS_DIR = Path(__file__).parent / "results"
SAMPLES_DIR = Path(__file__).parent / "sample_images"

TESTED_ATTRIBUTES = ["hair_color", "cloak_color", "eye_color"]


def run_correction_experiment(num_chains: int, max_inpaint_retries: int, gen_steps: int, save_samples: bool):
    import sd_pipeline
    import sd_inpaint
    import attribute_check as ac

    checks = [c for c in ATTRIBUTE_CHECKS if c["name"] in TESTED_ATTRIBUTES]
    full_prompt = build_prompt(EXAMPLE_PROFILE, "short")
    print(f"initial prompt: {full_prompt}")

    # ── Phase 1: generate one initial image per chain ──────────────
    print(f"generating {num_chains} initial images...")
    pipe = sd_pipeline.load_pipeline()
    images = {}
    for c in range(num_chains):
        images[c] = sd_pipeline.generate(pipe, full_prompt, NEGATIVE_PROMPT, seed=c * 1000, steps=gen_steps)
    sd_pipeline.unload_pipeline(pipe)

    # ── Phase 2: check all attributes on the initial images ─────────
    print("checking initial images...")
    processor_v, model_v = ac.load_vqa()
    chains = []
    for c in range(num_chains):
        results = ac.check_attributes(processor_v, model_v, images[c], checks=checks)
        chains.append({
            "id": c,
            "image": images[c],
            "initial_answers": {k: v["answer"] for k, v in results.items()},
            "needs_fix": [name for name, r in results.items() if not r["match"]],
            "fixed": [],
            "still_broken": [],
            "inpaint_tries": {},
        })
    ac.unload_vqa(processor_v, model_v)
    initial_pass = sum(1 for c in chains if not c["needs_fix"])
    print(f"  {initial_pass}/{num_chains} passed all 3 checks with zero fixes needed")

    # ── Phase 3: inpaint-correct whatever failed, one attribute at a time ──
    check_by_name = {c["name"]: c for c in checks}
    for attr_name in TESTED_ATTRIBUTES:
        needing = [c for c in chains if attr_name in c["needs_fix"]]
        if not needing:
            continue
        print(f"\ninpaint-correcting {attr_name} on {len(needing)} chains...")
        # Deliberately NOT the full multi-attribute prompt — including the
        # other attributes here would reintroduce the exact binding
        # confusion inpainting is meant to isolate away from. Just the
        # target attribute plus generic style/framing context so the
        # patch blends stylistically with the rest of the (untouched) image.
        attr_prompt = EXAMPLE_PROFILE[check_by_name[attr_name]["field"]] + ", digital painting, portrait"

        for retry_round in range(max_inpaint_retries):
            pending = [c for c in needing if attr_name not in c["fixed"] and attr_name not in c["still_broken"]]
            if not pending:
                break
            print(f"  round {retry_round + 1}/{max_inpaint_retries}: inpainting {len(pending)} images...")
            pipe = sd_inpaint.load_pipeline()
            inpainted = {}
            for c in pending:
                seed = c["id"] * 1000 + 500 + retry_round
                c["inpaint_tries"][attr_name] = retry_round + 1
                inpainted[c["id"]] = sd_inpaint.inpaint(
                    pipe, c["image"], attr_name, attr_prompt, NEGATIVE_PROMPT, seed=seed, steps=gen_steps,
                )
            sd_inpaint.unload_pipeline(pipe)

            processor_v, model_v = ac.load_vqa()
            for c in pending:
                result = ac.check_one(processor_v, model_v, inpainted[c["id"]], check_by_name[attr_name])
                if result["match"]:
                    c["image"] = inpainted[c["id"]]
                    c["fixed"].append(attr_name)
                else:
                    c["image"] = inpainted[c["id"]]  # keep the attempt either way, matches real usage
            ac.unload_vqa(processor_v, model_v)
            fixed_this_round = sum(1 for c in pending if attr_name in c["fixed"])
            print(f"    {fixed_this_round}/{len(pending)} fixed this round")

        for c in needing:
            if attr_name not in c["fixed"]:
                c["still_broken"].append(attr_name)

    if save_samples:
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        for c in chains[:10]:
            c["image"].save(SAMPLES_DIR / f"inpaint_chain{c['id']}.png")

    return chains, checks


def summarize(chains, checks, num_chains):
    per_attr = {}
    for check in checks:
        name = check["name"]
        started_broken = sum(1 for c in chains if name in c["needs_fix"])
        now_correct = sum(1 for c in chains if name not in c["needs_fix"] or name in c["fixed"])
        fixed_via_inpaint = sum(1 for c in chains if name in c["fixed"])
        tries = [c["inpaint_tries"][name] for c in chains if name in c["inpaint_tries"] and name in c["fixed"]]
        per_attr[name] = {
            "started_correct": num_chains - started_broken,
            "started_broken": started_broken,
            "fixed_via_inpaint": fixed_via_inpaint,
            "still_broken_after_inpaint": started_broken - fixed_via_inpaint,
            "final_success_rate": round(now_correct / num_chains, 3),
            "avg_inpaint_tries_on_success": round(statistics.mean(tries), 2) if tries else None,
        }
    return per_attr


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chains", type=int, default=20)
    p.add_argument("--max-inpaint-retries", type=int, default=5)
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--no-samples", action="store_true")
    args = p.parse_args()

    chains, checks = run_correction_experiment(args.chains, args.max_inpaint_retries, args.steps,
                                                save_samples=not args.no_samples)
    summary = summarize(chains, checks, args.chains)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"inpaint_{stamp}.json"
    serializable_chains = [{k: v for k, v in c.items() if k != "image"} for c in chains]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "chains": serializable_chains}, f, indent=2)

    print(f"\n=== SUMMARY ===\nwrote {out_path}")
    for name, s in summary.items():
        print(f"  {name:<12} started_correct={s['started_correct']:>2}/{args.chains}  "
              f"fixed_via_inpaint={s['fixed_via_inpaint']:>2}/{s['started_broken']:>2}  "
              f"final_rate={s['final_success_rate']*100:.0f}%  avg_tries={s['avg_inpaint_tries_on_success']}")


if __name__ == "__main__":
    main()
