"""
Character-context image-prompt building — turns an Entity's freeform,
2-3 sentence prose `appearance` (written by inference.parse_opening_appearance
/ infer_characters for narrative purposes, not image generation) into the
short, comma-separated tag style proven to actually work.

Grounded in two hard-won, opposite lessons from the imagepipe experiments:
  - Shorter, concrete, single-attribute-per-clause prompts hold together
    far better than flowing prose (field-set experiment: short field set
    beat medium/long on every metric, monotonically).
  - Asking an LLM to *guess substitute wording* for what an image model
    "prefers" backfires (canonicalize_profile.py's "dark green" -> "olive"
    experiment measurably made cloak_color WORSE, not better). This
    function does NOT do that — it distills/compresses existing content
    into tags, it doesn't invent new wording for attributes untested here.

Cached per-entity: visual_tags is only re-distilled when the source prose
(visual_tags_source) actually changes, so this costs one Groq call per
appearance update, not one per image generation — same prefab-style
cost pattern as the rest of the engine.

Plural/group entities (Entity.is_collective) are deliberately NOT
distilled individually — the B1-B4 background-test findings showed
multi-character attribute binding is a hard wall (0% success at 2+ named
characters), while a single generic crowd descriptor was cheap and
reliable (75-85%). Collective entities get routed to a crowd descriptor
instead of an attempted individual likeness.
"""
import logging
import re
from .call import call
from .types import Entity

logger = logging.getLogger(__name__)

STYLE_SUFFIX = "digital painting, portrait, single character, plain background"
CROWD_TEMPLATE = "a crowd of {role} in the background, digital painting"

# Found live, not theorized: a real turn for a plain library scene
# generated lingerie because the character's `appearance` (by design --
# see infer_characters's prompt in inference.py) never mentions clothing
# at all -- it's deliberately scoped to STABLE narrative traits (height,
# hair, eyes), not scene-variable ones like an outfit. That's a reasonable
# call for the narrative engine, but it leaves the image prompt with zero
# clothing constraint, and SD1.5 does not default to "modestly dressed"
# when clothing is unconstrained -- a well-documented bias, now directly
# confirmed here. This is the positive-prompt half of the fix (see
# visual_profile.NEGATIVE_PROMPT for the negative-prompt half) -- a
# guaranteed clothing tag when the distilled tags don't already include
# one, rather than leaving SD1.5 to fill an undefined gap.
_CLOTHING_KEYWORDS = (
    'shirt', 'dress', 'robe', 'cloak', 'armor', 'armour', 'tunic', 'coat',
    'jacket', 'uniform', 'gown', 'suit', 'vest', 'blouse', 'sweater', 'attire',
    'clothing', 'clothed', 'outfit', 'garment',
)
DEFAULT_CLOTHING_TAG = "modest clothing, fully clothed"
_CLOTHING_KEYWORD_RE = re.compile(
    r'\b(' + '|'.join(re.escape(kw) for kw in _CLOTHING_KEYWORDS) + r')\b'
)

_DISTILL_SYSTEM = (
    "You compress a character's physical description into short, comma-separated "
    "visual tags for an image-generation prompt. Extract only concrete, visual "
    "nouns and adjectives already present in the text -- hair (color, length, "
    "style), eyes (color), build, clothing (garment + color), and any single "
    "distinguishing feature (scar, mark). Drop everything else: mood, backstory, "
    "verbs, connecting words, anything not directly visual. Do not invent or "
    "substitute new details or colors -- only shorten what's already there. "
    "Output 4-6 short comma-separated tags, nothing else, no explanation."
)


def _distill(appearance_text: str) -> str:
    result, _ = call(
        _DISTILL_SYSTEM,
        [{'role': 'user', 'content': appearance_text}],
        max_tokens=60, temp=0.1,
    )
    return result.strip()


def get_visual_tags(entity: Entity) -> str:
    """Returns cached distilled tags, re-distilling only if `appearance`
    changed since the last call (e.g. a saved_assumptions reveal updated
    it mid-session)."""
    if not entity.appearance:
        return ''
    if entity.visual_tags and entity.visual_tags_source == entity.appearance:
        return entity.visual_tags
    entity.visual_tags = _distill(entity.appearance)
    entity.visual_tags_source = entity.appearance
    logger.info(f'[visual_prompts] {entity.name} appearance -> tags: {entity.visual_tags}')
    return entity.visual_tags


def _ensure_clothing(tags: str) -> str:
    """Guarantees SOME clothing descriptor is present -- see
    DEFAULT_CLOTHING_TAG's docstring for why this exists at all. A
    word-boundary match, not exact matching -- deliberately generous
    (catches "cloak" inside "dark green cloak") since a false positive
    (skipping the default when clothing actually was mentioned) is
    harmless, while a false negative (leaving clothing genuinely
    unconstrained) is the exact failure this fix targets.

    Word-boundary, not plain substring: a plain `kw in tags` check matches
    "clothed" inside "unclothed", "dress" inside "undressed", "shirt"
    inside "shirtless", and "robe" inside "disrobe"/"disrobed" -- all
    confirmed live. Every one of those is a false POSITIVE that defeats
    the whole point: a distilled tag string containing "unclothed" would
    be read as "clothing already mentioned" and passed through unchanged,
    skipping DEFAULT_CLOTHING_TAG entirely and leaving the literal
    negation word sitting in the positive SD1.5 prompt -- actively
    pushing toward the exact output NEGATIVE_PROMPT exists to fight."""
    if _CLOTHING_KEYWORD_RE.search(tags.lower()):
        return tags
    return f"{tags}, {DEFAULT_CLOTHING_TAG}" if tags else DEFAULT_CLOTHING_TAG


def build_character_image_prompt(entity: Entity, scene_context: str = '') -> str:
    """Individual character portrait prompt. Callers should route
    is_collective entities to build_crowd_prompt() instead -- this
    function assumes a single, named, non-collective subject."""
    tags = _ensure_clothing(get_visual_tags(entity))
    parts = [t for t in [tags, scene_context, STYLE_SUFFIX] if t]
    return ", ".join(parts)


def build_crowd_prompt(entity: Entity, scene_context: str = '') -> str:
    """For is_collective entities -- deliberately does not attempt individual
    likeness (proven unreliable), just a generic crowd descriptor."""
    role = entity.role or entity.name or "people"
    parts = [CROWD_TEMPLATE.format(role=role)]
    if scene_context:
        parts.append(scene_context)
    return ", ".join(parts)


def build_scene_image_prompt(entity: Entity, scene_context: str = '') -> str:
    """Single entry point for turn-generation code: routes to the crowd
    or individual prompt automatically based on Entity.is_collective."""
    if entity.is_collective:
        return build_crowd_prompt(entity, scene_context)
    return build_character_image_prompt(entity, scene_context)
