"""
Detail-level vs. determinism correlation — follow-up to Section 4.2's
field_set comparison. Reads the latest results/*.json for each of
short/medium/long (matched by "n" in the JSON, so it only picks up
full-scale runs, not smoke tests) and reports:
  - a ranked comparison table
  - Pearson correlation between prompt detail level (field count and
    profile-text word count) and each consistency metric

With only 3 field sets, Pearson r has 1 degree of freedom — treat it as a
directional signal (does more detail help, hurt, or not matter), not a
statistically powerful test. That's consistent with what was asked: find
the minimum field set that still holds determinism, not prove significance.

Usage:
    python correlate_results.py --min-n 1000
"""
import argparse
import json
import statistics
from pathlib import Path

from visual_profile import PROFILE_FIELD_SETS

RESULTS_DIR = Path(__file__).parent / "results"


def _latest_result_for(field_set: str, min_n: int):
    """Most recent results/*.json containing this field_set at n >= min_n."""
    candidates = []
    for f in sorted(RESULTS_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if field_set in data and data[field_set]["n"] >= min_n:
            candidates.append((f.stat().st_mtime, data[field_set]))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = sum((x - mx) ** 2 for x in xs) ** 0.5
    deny = sum((y - my) ** 2 for y in ys) ** 0.5
    if denx == 0 or deny == 0:
        return None
    return num / (denx * deny)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min-n", type=int, default=1000, help="only use results with at least this many generations")
    args = p.parse_args()

    rows = {}
    for fs in PROFILE_FIELD_SETS:
        r = _latest_result_for(fs, args.min_n)
        if r is None:
            print(f"[!] no result found for field_set={fs} with n>={args.min_n} — run it first")
            continue
        rows[fs] = r

    if len(rows) < 2:
        print("Need at least 2 field sets with results to correlate. Exiting.")
        return

    print(f"{'field_set':<8} {'fields':>6} {'words':>6} {'img_mean':>9} {'img_min':>8} "
          f"{'img_stdev':>10} {'cap_mean':>9} {'s/img':>7}")
    detail_fields, detail_words = [], []
    img_means, img_mins, img_stdevs, cap_means = [], [], [], []
    for fs, r in rows.items():
        n_fields = len(PROFILE_FIELD_SETS[fs])
        n_words = len(r["profile_text"].split())
        print(f"{fs:<8} {n_fields:>6} {n_words:>6} {r['image_similarity_to_canonical']['mean']:>9.4f} "
              f"{r['image_similarity_to_canonical']['min']:>8.4f} "
              f"{r['image_similarity_to_canonical']['stdev']:>10.4f} "
              f"{r['caption_similarity_to_profile']['mean']:>9.4f} {r['seconds_per_image']:>7.2f}")
        detail_fields.append(n_fields)
        detail_words.append(n_words)
        img_means.append(r["image_similarity_to_canonical"]["mean"])
        img_mins.append(r["image_similarity_to_canonical"]["min"])
        img_stdevs.append(r["image_similarity_to_canonical"]["stdev"])
        cap_means.append(r["caption_similarity_to_profile"]["mean"])

    print(f"\n({len(rows)} field sets — Pearson r has {len(rows) - 1} degree(s) of freedom, "
          f"read as directional only)")
    for label, ys in [
        ("image_sim mean", img_means),
        ("image_sim min (tail)", img_mins),
        ("image_sim stdev", img_stdevs),
        ("caption_sim mean", cap_means),
    ]:
        r_fields = pearson(detail_fields, ys)
        r_words = pearson(detail_words, ys)
        r_fields_s = f"{r_fields:+.3f}" if r_fields is not None else "n/a"
        r_words_s = f"{r_words:+.3f}" if r_words is not None else "n/a"
        print(f"  {label:<22} vs field_count: r={r_fields_s}   vs word_count: r={r_words_s}")

    # Minimum field set that still "holds determinism": the shortest one
    # whose image_sim mean is within 1 stdev (its own) of the richest
    # field set's mean, i.e. more detail didn't meaningfully improve it.
    order = [fs for fs in PROFILE_FIELD_SETS if fs in rows]  # short, medium, long
    richest = rows[order[-1]]
    richest_mean = richest["image_similarity_to_canonical"]["mean"]
    print("\nMinimum field set within 1 stdev of richest ('long') mean:")
    for fs in order:
        r = rows[fs]
        m, sd = r["image_similarity_to_canonical"]["mean"], r["image_similarity_to_canonical"]["stdev"]
        within = abs(m - richest_mean) <= sd
        print(f"  {fs:<8} mean={m:.4f} stdev={sd:.4f}  {'<- within 1 stdev of long' if within and fs != order[-1] else ''}")


if __name__ == "__main__":
    main()
