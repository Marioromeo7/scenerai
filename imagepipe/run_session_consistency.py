"""
Session-consistency experiment — does a fixed character (identity fields
held fixed, plus one fixed action/place) stay recognizable across many
turns of a single "run" (session), and does that hold up across
independent runs?

Priority, per spec: within-run consistency is primary (does eye color and
the distinguishing mark hold turn to turn within one session); between-run
consistency is secondary (a working prompt is cheap to reuse — the image
itself doesn't need to survive between sessions, the prompt does). Both
are reported from one pass: within_run.*.mean is the average per-run
consistency rate; within_run.*.stdev is how much that rate itself varies
from run to run, i.e. the between-run signal.

FIXED_SCENE below is an example action+place — not something you
specified beyond "one action, same place" — change it if you want a
different one tested.

Metric choice matters here: whole-image CLIP similarity and generic BLIP
captions were the wrong tool for "did eye color/the scar survive" (see
attribute_check.py's docstring — captions never once mentioned either
across 3000 generations in the field-set experiment). This uses VQA
(direct yes/no and short-answer questions per image) for eye color and
the scar, and keeps CLIP image similarity only as a secondary continuity
signal, referenced against each run's own turn 0 (not a fixed prompt is
fixed here, so it should track drift meaningfully unlike the field-set
experiment's cross-field comparison).

Three phases, one model resident at a time (4GB VRAM budget, same
constraint as run_experiment.py). No images persisted beyond a small
sample — saving is costly at the scale this is meant to validate.

Usage:
    python run_session_consistency.py --runs 10 --turns 20 --steps 20    # pilot
    python run_session_consistency.py --runs 1000 --turns 100 --steps 20  # full scale
"""
import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from visual_profile import EXAMPLE_PROFILE, NEGATIVE_PROMPT, build_prompt

RESULTS_DIR = Path(__file__).parent / "results"
SAMPLES_DIR = Path(__file__).parent / "sample_images"
SAMPLE_COUNT = 10

FIXED_SCENE = "standing in a torch-lit stone corridor, hand resting on the hilt of a sword"


def run_session_experiment(num_runs: int, turns_per_run: int, steps: int, save_samples: bool):
    import sd_pipeline
    import attribute_check
    import similarity

    prompt = build_prompt(EXAMPLE_PROFILE, "short") + ", " + FIXED_SCENE
    total = num_runs * turns_per_run
    print(f"prompt: {prompt}")
    print(f"phase 1/3: generating {num_runs} runs x {turns_per_run} turns = {total} images...")

    pipe = sd_pipeline.load_pipeline()
    all_images = []  # flat list, index = run * turns_per_run + turn
    t0 = time.monotonic()
    for i in range(total):
        img = sd_pipeline.generate(pipe, prompt, NEGATIVE_PROMPT, seed=i, steps=steps)
        all_images.append(img)
        if i == 0:
            per_image_s = time.monotonic() - t0
            print(f"  first image done in {per_image_s:.1f}s -- est. total: {per_image_s * total / 60:.1f} min")
        elif (i + 1) % max(1, total // 20) == 0 or i == total - 1:
            elapsed = time.monotonic() - t0
            print(f"  {i + 1}/{total} ({elapsed:.0f}s elapsed)")
    gen_elapsed = time.monotonic() - t0
    sd_pipeline.unload_pipeline(pipe)
    print(f"generation done in {gen_elapsed:.1f}s")

    if save_samples:
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        for i, img in enumerate(all_images[:SAMPLE_COUNT]):
            img.save(SAMPLES_DIR / f"session_img{i}.png")

    print(f"phase 2/3: checking attributes on {total} images...")
    processor_v, model_v = attribute_check.load_vqa()
    attr_results = [attribute_check.check_attributes(processor_v, model_v, img) for img in all_images]
    attribute_check.unload_vqa(processor_v, model_v)

    print(f"phase 3/3: scoring image similarity...")
    processor_s, model_s = similarity.load_clip()
    image_embeds = [similarity.image_embedding(processor_s, model_s, img) for img in all_images]
    similarity.unload_clip(processor_s, model_s)

    run_stats = []
    for r in range(num_runs):
        start, end = r * turns_per_run, (r + 1) * turns_per_run
        run_attrs = attr_results[start:end]
        eye_matches = [a["eye_color"]["match"] for a in run_attrs]
        scar_matches = [a["scar_mark"]["match"] for a in run_attrs]
        both_matches = [e and s for e, s in zip(eye_matches, scar_matches)]

        ref_embed = image_embeds[start]  # this run's own turn 0 as reference
        sims = [similarity.cosine_similarity(ref_embed, e) for e in image_embeds[start + 1:end]]

        run_stats.append({
            "run": r,
            "eye_match_rate": sum(eye_matches) / len(eye_matches),
            "scar_match_rate": sum(scar_matches) / len(scar_matches),
            "both_match_rate": sum(both_matches) / len(both_matches),
            "image_sim_mean": statistics.mean(sims) if sims else None,
        })

    def _agg(key):
        vals = [rs[key] for rs in run_stats if rs[key] is not None]
        return {
            "mean": round(statistics.mean(vals), 4),
            "stdev": round(statistics.stdev(vals), 4) if len(vals) > 1 else None,
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
        }

    result = {
        "prompt": prompt,
        "num_runs": num_runs,
        "turns_per_run": turns_per_run,
        "steps": steps,
        "generation_seconds": round(gen_elapsed, 1),
        "seconds_per_image": round(gen_elapsed / total, 2),
        "within_run": {
            "eye_match_rate": _agg("eye_match_rate"),
            "scar_match_rate": _agg("scar_match_rate"),
            "both_match_rate": _agg("both_match_rate"),
            "image_sim_mean": _agg("image_sim_mean"),
        },
        "between_run_note": (
            "the 'stdev' fields above ARE the between-run consistency measure -- "
            "how much each run's own within-run rate varies from run to run"
        ),
        "sample_answers": [
            {"eye_color": attr_results[i]["eye_color"]["answer"],
             "scar_mark": attr_results[i]["scar_mark"]["answer"]}
            for i in range(min(10, total))
        ],
    }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=10, help="number of independent 'sessions'")
    p.add_argument("--turns", type=int, default=20, help="generations per run/session")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--no-samples", action="store_true")
    args = p.parse_args()

    result = run_session_experiment(args.runs, args.turns, args.steps, save_samples=not args.no_samples)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"session_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    wr = result["within_run"]
    print(f"\n=== RESULTS ===\nwrote {out_path}")
    print(f"  eye_match_rate:  mean={wr['eye_match_rate']['mean']} stdev={wr['eye_match_rate']['stdev']}")
    print(f"  scar_match_rate: mean={wr['scar_match_rate']['mean']} stdev={wr['scar_match_rate']['stdev']}")
    print(f"  both_match_rate: mean={wr['both_match_rate']['mean']} stdev={wr['both_match_rate']['stdev']}")
    print(f"  image_sim_mean:  mean={wr['image_sim_mean']['mean']} stdev={wr['image_sim_mean']['stdev']}")
    print(f"  ({result['seconds_per_image']}s/image)")


if __name__ == "__main__":
    main()
