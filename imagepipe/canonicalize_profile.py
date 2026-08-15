"""
LLM-assisted prompt canonicalization via Groq — one-time, cached step that
simplifies compound/ambiguous attribute descriptions into terms SD1.5
renders more reliably, before they ever reach the image pipeline.

Grounded in what today's incremental-build experiment found live: a
compound modifier+color instruction like "dark green cloak" fights
SD1.5's learned color prior for that garment type and gets misrendered as
grey/brown/black far more often than a single, unambiguous hue would.
Splitting each attribute into its simplest form, and separating adjacent
same-sentence objects that can bleed color onto each other (e.g. "cloak
over leather armor"), is the fix being tested here.

This is a single Groq call per profile (cheap, matches the existing
prefab pattern in backend/engine/engine.py: pay the LLM cost once at
scenario-creation time, reuse the simplified text for every subsequent
image generation), not a per-image cost.
"""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from groq import Groq

MODEL = "llama-3.1-8b-instant"

SYSTEM_PROMPT = """You simplify visual-description phrases for a Stable Diffusion 1.5 image prompt so the model renders them more reliably. You make the MINIMUM edit needed -- most phrases need zero changes.

Only two situations justify an edit:
1. A compound/modified color ("dark green", "muted grey-blue") -- replace it with ONE single common color word that is the closest visual match. NEVER delete a color outright; always substitute it with another color word.
2. Two or more colored objects sharing one clause ("cloak over leather armor") -- split into separate clauses so each object's color is unambiguous.

Never drop, reword, or paraphrase anything else -- not adjectives, not materials, not structure -- even if it seems like a simplification. If neither situation applies, output the input completely unchanged, word for word.

Examples:
input: "dark green traveling cloak over leather armor"
output: "olive traveling cloak, brown leather armor"

input: "pale grey eyes"
output: "pale grey eyes"
(no compound color, no shared clause -- "grey" alone is already a single color word, so this is unchanged)

input: "tall, lean build"
output: "tall, lean build"
(no color at all -- unchanged)

input: "guarded, watchful expression"
output: "guarded, watchful expression"
(no color, no object-sharing -- unchanged)

input: "muted green and grey tones"
output: "muted green and grey tones"
(these are two separate tones already, not one compound modifier on one object -- unchanged)

Output ONLY the rewritten (or unchanged) phrase, no explanation, no quotes."""


def get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set -- check .env at project root")
    return Groq(api_key=api_key)


def canonicalize_field(client: Groq, field_text: str, model: str = MODEL) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": field_text},
        ],
        max_tokens=60,
        temperature=0.0,
    )
    return resp.choices[0].message.content.strip()


def canonicalize_profile(profile: dict, fields: list, model: str = MODEL) -> dict:
    """Returns a new profile dict with only the given fields canonicalized --
    everything else in the original profile is left untouched."""
    client = get_client()
    result = dict(profile)
    for f in fields:
        if f in profile and profile[f]:
            result[f] = canonicalize_field(client, profile[f], model=model)
    return result


if __name__ == "__main__":
    from visual_profile import EXAMPLE_PROFILE

    client = get_client()
    for field, text in EXAMPLE_PROFILE.items():
        simplified = canonicalize_field(client, text)
        marker = "  <-- CHANGED" if simplified != text else ""
        print(f"{field:24} {text!r:65} -> {simplified!r}{marker}")
