"""
Visual-consistency experiment — Section 4.2 of NEXT_PHASE_PLAN.md.

For each candidate profile length, generate the same character N times
(same structured profile -> same deterministic prompt, varying only the
diffusion seed) and measure:
  - image-image consistency: CLIP cosine similarity between each generation
    and a canonical reference (seed=0), the same field_set's first image.
  - round-trip fidelity: caption each generation with BLIP (local stand-in
    for the plan's Gemini vision check), then CLIP text-text similarity
    between that caption and the original profile text.

Three sequential phases (generate all -> caption all -> score all), one
model resident in GPU memory at a time — this machine has ~4GB VRAM total,
not enough headroom to keep SD1.5 + BLIP + CLIP loaded simultaneously.

Per your instruction: saving is costly at the scale this is meant to
validate (millions of generations in production), so this does NOT persist
every generated image — only a small sample for spot-checking, plus the
numeric aggregate report. The report is the deliverable, not an image
archive.

Usage:
    python run_experiment.py --field-set short --n 5     # smoke test first
    python run_experiment.py --field-set short --n 1000
    python run_experiment.py --all --n 1000               # all three field sets, for comparison
"""
import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from visual_profile import PROFILE_FIELD_SETS, EXAMPLE_PROFILE, NEGATIVE_PROMPT, build_prompt, profile_text_for_comparison

RESULTS_DIR = Path(__file__).parent / "results"
SAMPLES_DIR = Path(__file__).parent / "sample_images"
SAMPLE_COUNT = 10  # how many of the N generations to actually keep as files


def run_field_set(field_set: str, n: int, steps: int, save_samples: bool):
    import sd_pipeline
    import caption_model
    import similarity

    prompt = build_prompt(EXAMPLE_PROFILE, field_set)
    profile_text = profile_text_for_comparison(EXAMPLE_PROFILE, field_set)
    print(f"[{field_set}] prompt: {prompt}")

    # ── Phase 1: generation (SD1.5 resident) ──────────────────────
    print(f"[{field_set}] phase 1/3: generating {n + 1} images (1 canonical + {n} variations)...")
    pipe = sd_pipeline.load_pipeline()
    images = []
    t0 = time.monotonic()
    for seed in range(n + 1):  # seed 0 = canonical reference
        img = sd_pipeline.generate(pipe, prompt, NEGATIVE_PROMPT, seed=seed, steps=steps)
        images.append(img)
        if seed == 0:
            per_image_s = time.monotonic() - t0
            print(f"[{field_set}]   canonical done in {per_image_s:.1f}s — "
                  f"est. total generation time: {per_image_s * (n + 1) / 60:.1f} min")
        elif seed % max(1, n // 10) == 0 or seed == n:
            elapsed = time.monotonic() - t0
            print(f"[{field_set}]   {seed}/{n} ({elapsed:.0f}s elapsed)")
    gen_elapsed = time.monotonic() - t0
    sd_pipeline.unload_pipeline(pipe)
    print(f"[{field_set}] generation done in {gen_elapsed:.1f}s")

    if save_samples:
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        for i, img in enumerate(images[:SAMPLE_COUNT]):
            img.save(SAMPLES_DIR / f"{field_set}_seed{i}.png")

    # ── Phase 2: captioning (BLIP resident) ───────────────────────
    print(f"[{field_set}] phase 2/3: captioning {len(images)} images...")
    processor_c, model_c = caption_model.load_captioner()
    captions = [caption_model.describe_image(processor_c, model_c, img) for img in images]
    caption_model.unload_captioner(processor_c, model_c)

    # ── Phase 3: scoring (CLIP resident) ──────────────────────────
    print(f"[{field_set}] phase 3/3: scoring consistency...")
    processor_s, model_s = similarity.load_clip()
    image_embeds = [similarity.image_embedding(processor_s, model_s, img) for img in images]
    canonical_embed = image_embeds[0]
    profile_text_embed = similarity.text_embedding(processor_s, model_s, profile_text)

    image_sims = [similarity.cosine_similarity(canonical_embed, e) for e in image_embeds[1:]]
    caption_sims = [
        similarity.cosine_similarity(profile_text_embed, similarity.text_embedding(processor_s, model_s, c))
        for c in captions
    ]
    similarity.unload_clip(processor_s, model_s)

    result = {
        "field_set": field_set,
        "n": n,
        "prompt": prompt,
        "profile_text": profile_text,
        "steps": steps,
        "generation_seconds": round(gen_elapsed, 1),
        "seconds_per_image": round(gen_elapsed / (n + 1), 2),
        "image_similarity_to_canonical": {
            "mean": round(statistics.mean(image_sims), 4),
            "stdev": round(statistics.stdev(image_sims), 4) if len(image_sims) > 1 else None,
            "min": round(min(image_sims), 4),
            "max": round(max(image_sims), 4),
        },
        "caption_similarity_to_profile": {
            "mean": round(statistics.mean(caption_sims), 4),
            "stdev": round(statistics.stdev(caption_sims), 4) if len(caption_sims) > 1 else None,
            "min": round(min(caption_sims), 4),
            "max": round(max(caption_sims), 4),
        },
        "sample_captions": captions[:5],
    }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--field-set", choices=list(PROFILE_FIELD_SETS), default="short")
    p.add_argument("--all", action="store_true", help="run all three field sets for comparison")
    p.add_argument("--n", type=int, default=5, help="number of variation generations beyond the canonical reference")
    p.add_argument("--steps", type=int, default=25)
    p.add_argument("--no-samples", action="store_true", help="don't save any sample images, numbers only")
    args = p.parse_args()

    field_sets = list(PROFILE_FIELD_SETS) if args.all else [args.field_set]
    results = {}
    for fs in field_sets:
        results[fs] = run_field_set(fs, args.n, args.steps, save_samples=not args.no_samples)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\n=== RESULTS ===\nwrote {out_path}")
    for fs, r in results.items():
        print(f"  {fs}: image_sim mean={r['image_similarity_to_canonical']['mean']} "
              f"stdev={r['image_similarity_to_canonical']['stdev']}  "
              f"caption_sim mean={r['caption_similarity_to_profile']['mean']}  "
              f"({r['seconds_per_image']}s/image)")


if __name__ == "__main__":
    main()
