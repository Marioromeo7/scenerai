"""
Structured visual-profile schema and deterministic prompt construction —
Section 4.1/4.2 of NEXT_PHASE_PLAN.md.

The whole point: build the image-generation prompt by template-formatting
fixed structured fields, zero LLM calls per image. Three candidate profile
lengths to compare — the experiment (run_experiment.py) finds the shortest
one that still holds visual identity across many generations, instead of
guessing at the token/consistency tradeoff.
"""

PROFILE_FIELD_SETS = {
    "short": ["hair", "eyes", "clothing", "distinguishing_feature"],
    "medium": ["hair", "eyes", "build", "clothing", "palette", "distinguishing_feature"],
    "long": ["hair", "eyes", "build", "clothing", "palette", "art_style",
             "expression", "distinguishing_feature"],
}

# One example character's full field set — every candidate length is a
# truncation of this same underlying description, so shorter lengths are a
# fair subset comparison, not a different character.
EXAMPLE_PROFILE = {
    "hair": "long silver hair, loosely braided",
    "eyes": "pale grey eyes",
    "build": "tall, lean build",
    "clothing": "dark green traveling cloak over leather armor",
    "palette": "muted green and grey tones",
    "art_style": "painterly digital fantasy illustration",
    "expression": "guarded, watchful expression",
    "distinguishing_feature": "a thin scar across the left eyebrow",
}

NEGATIVE_PROMPT = (
    "extra limbs, extra fingers, deformed hands, blurry, low quality, "
    "watermark, text, disfigured, mutated, bad anatomy, "
    "nudity, nsfw, lingerie, underwear, exposed, bare skin, revealing clothing, "
    "sexualized, suggestive"
)
# Added live, after a real generation for a plain library scene defaulted
# to lingerie with no clothing anywhere in the prompt (see
# visual_prompts.py's DEFAULT_CLOTHING_TAG docstring for the other half of
# this fix — a guaranteed positive clothing tag, not just this negative
# one). Neither layer alone is reliable; SD1.5 can still drift even with
# both, this reduces the failure rate, it doesn't guarantee it to zero.


def build_prompt(profile: dict, field_set: str, art_style_fallback: str = "digital painting") -> str:
    """Deterministic template formatting — no LLM call. Same profile +
    field_set always produces the same prompt string."""
    fields = PROFILE_FIELD_SETS[field_set]
    parts = [profile[f] for f in fields if f in profile and profile[f]]
    if "art_style" not in fields:
        parts.append(art_style_fallback)
    parts.append("portrait, single character, plain background")
    return ", ".join(parts)


def profile_text_for_comparison(profile: dict, field_set: str) -> str:
    """Same content as build_prompt but without the fixed boilerplate
    (art style fallback, 'portrait, single character...') — used as the
    text side of the CLIP text-text similarity check against a caption,
    where the boilerplate would just dilute the signal."""
    fields = PROFILE_FIELD_SETS[field_set]
    parts = [profile[f] for f in fields if f in profile and profile[f]]
    return ", ".join(parts)
