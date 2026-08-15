"""
Character/background/tier definitions for the B1-B4 scene-complexity
tests — extends today's single-character-plain-background findings into
real backgrounds and multiple named characters.

Deliberately reuses the "concrete, visually-anchored descriptors beat
abstract ones" lesson from the field-set experiment: each character is
defined by hair color + one clothing color, nothing fuzzier, and each
character uses a DIFFERENT hair color so a per-position VQA check
("what color is the hair of the person on the left?") has an
unambiguous right answer.

Tiers isolate one variable at a time rather than stacking all of them at
once (matching the session's established methodology):
  B1 — one character + a real background (not "plain background")
  B2 — two characters + background
  B3 — three characters + background
  B4 — one character + background + a crowd in the background (crowd
       tested in isolation, not combined with B2/B3's multi-character
       case, so a bad result can't be blamed on the wrong variable)
"""

CHARACTERS = {
    "A": {"hair": "long silver hair", "hair_color_words": ["silver", "white", "grey", "gray"],
          "clothing": "a green cloak"},
    "B": {"hair": "short black hair", "hair_color_words": ["black", "dark"],
          "clothing": "a red tunic"},
    "C": {"hair": "long red hair", "hair_color_words": ["red", "auburn", "orange"],
          "clothing": "a blue robe"},
}

BACKGROUND_SCENE = "in a torch-lit stone corridor"

NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four"}

TIERS = {
    "B1": {"positions": [("center", "A")], "crowd": False},
    "B2": {"positions": [("left", "A"), ("right", "B")], "crowd": False},
    "B3": {"positions": [("left", "A"), ("center", "B"), ("right", "C")], "crowd": False},
    "B4": {"positions": [("center", "A")], "crowd": True},
}


def build_scene_prompt(tier: str, art_style_fallback: str = "digital painting") -> str:
    cfg = TIERS[tier]
    count = len(cfg["positions"])
    parts = []
    for pos, char_id in cfg["positions"]:
        char = CHARACTERS[char_id]
        pos_phrase = "in the center" if (count == 1 or pos == "center") else f"on the {pos}"
        parts.append(f"{char['hair']} character {pos_phrase}, wearing {char['clothing']}")
    parts.append(BACKGROUND_SCENE)
    if cfg["crowd"]:
        parts.append("a crowd of people in the background")
    parts.append(art_style_fallback)
    parts.append(f"{NUMBER_WORDS[count]} main character{'s' if count > 1 else ''}" if count > 1 else "single character")
    return ", ".join(parts)


def build_checks(tier: str) -> list:
    """Dynamic VQA checks for this tier: person-count sanity gate, one
    per-position hair-color check (unambiguous since every character has
    a distinct hair color), and a crowd-presence check if applicable."""
    cfg = TIERS[tier]
    count = len(cfg["positions"])
    checks = []
    if not cfg["crowd"]:
        # "how many people" has no single right answer once a crowd is
        # deliberately in frame — skip it there rather than produce a
        # false failure for correctly-generated crowd scenes.
        checks.append({
            "name": "person_count",
            "question": "how many people are in this image?",
            "expected_any": [str(count), NUMBER_WORDS[count]],
        })
    for pos, char_id in cfg["positions"]:
        char = CHARACTERS[char_id]
        pos_phrase = "in the center" if (count == 1 or pos == "center") else f"on the {pos}"
        question = ("what color is the hair?" if count == 1
                    else f"what color is the hair of the person {pos_phrase}?")
        checks.append({
            "name": f"hair_{pos}",
            "question": question,
            "expected_any": char["hair_color_words"],
        })
    if cfg["crowd"]:
        checks.append({
            "name": "crowd_present",
            "question": "are there other people in the background?",
            "expected_any": ["yes"],
        })
    return checks
