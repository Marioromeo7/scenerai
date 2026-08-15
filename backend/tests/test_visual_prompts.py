"""Tests for engine/visual_prompts.py — character-context image prompt
building, tag-distillation caching, and collective/crowd routing."""
from unittest.mock import patch
from engine.types import Entity
from engine.visual_prompts import (
    get_visual_tags, build_character_image_prompt, build_crowd_prompt,
    build_scene_image_prompt, _ensure_clothing, DEFAULT_CLOTHING_TAG,
)

APPEARANCE = (
    "Elena is tall and lean, with long silver hair often braided loosely "
    "down her back. Her eyes are a pale grey, framed by a thin scar across "
    "her left eyebrow. She wears a dark green traveling cloak over fitted "
    "leather armor."
)

# Real appearance text from a live turn (see conversation) -- inference.py's
# infer_characters deliberately excludes clothing from appearance
# ("stable traits" only), which is exactly the case that produced an
# unintended lingerie generation for a plain library scene.
APPEARANCE_NO_CLOTHING = (
    "Elena stands at approximately 5'5\" with a petite, athletic build, "
    "her slender physique accentuated by her toned arms and shoulders. "
    "Her long, curly silver hair cascades down her back like a river of "
    "moonlight. Her bright, piercing emerald green eyes sparkle with "
    "intelligence, set against a smooth, porcelain-like complexion."
)


class TestEnsureClothing:
    def test_leaves_tags_with_clothing_untouched(self):
        tags = "silver hair, grey eyes, dark green cloak"
        assert _ensure_clothing(tags) == tags

    def test_appends_default_when_no_clothing_keyword_present(self):
        tags = "silver hair, grey eyes"
        result = _ensure_clothing(tags)
        assert DEFAULT_CLOTHING_TAG in result
        assert tags in result

    def test_handles_empty_tags(self):
        assert _ensure_clothing('') == DEFAULT_CLOTHING_TAG

    def test_case_insensitive_and_substring_match(self):
        assert _ensure_clothing("wearing a CLOAK") == "wearing a CLOAK"


class TestGetVisualTags:
    def test_distills_and_caches(self):
        entity = Entity(name='Elena', appearance=APPEARANCE)
        with patch('engine.visual_prompts.call') as mock:
            mock.return_value = ('long silver hair, pale grey eyes, dark green cloak, scar left eyebrow', 0.1)
            tags = get_visual_tags(entity)
        assert tags == 'long silver hair, pale grey eyes, dark green cloak, scar left eyebrow'
        assert entity.visual_tags == tags
        assert entity.visual_tags_source == APPEARANCE
        assert mock.call_count == 1

    def test_second_call_uses_cache_not_a_new_groq_call(self):
        entity = Entity(name='Elena', appearance=APPEARANCE)
        with patch('engine.visual_prompts.call') as mock:
            mock.return_value = ('silver hair, grey eyes', 0.1)
            get_visual_tags(entity)
            get_visual_tags(entity)
        assert mock.call_count == 1  # second call hit the cache, not Groq again

    def test_appearance_change_invalidates_cache(self):
        entity = Entity(name='Elena', appearance=APPEARANCE)
        with patch('engine.visual_prompts.call') as mock:
            mock.return_value = ('silver hair', 0.1)
            get_visual_tags(entity)
            entity.appearance = "Elena now has short black hair and a burn scar."
            mock.return_value = ('short black hair, burn scar', 0.1)
            tags = get_visual_tags(entity)
        assert tags == 'short black hair, burn scar'
        assert mock.call_count == 2  # appearance changed -> re-distilled

    def test_no_appearance_returns_empty_without_calling_groq(self):
        entity = Entity(name='Nobody', appearance='')
        with patch('engine.visual_prompts.call') as mock:
            tags = get_visual_tags(entity)
        assert tags == ''
        assert mock.call_count == 0


class TestPromptBuilders:
    def test_individual_prompt_includes_tags_and_style(self):
        entity = Entity(name='Elena', appearance=APPEARANCE)
        with patch('engine.visual_prompts.call') as mock:
            mock.return_value = ('silver hair, grey eyes', 0.1)
            prompt = build_character_image_prompt(entity)
        assert 'silver hair, grey eyes' in prompt
        assert 'portrait' in prompt

    def test_crowd_prompt_does_not_call_groq_or_use_appearance(self):
        entity = Entity(name='Tavern crowd', role='patrons', is_collective=True,
                         appearance="individual descriptions that should never be used")
        with patch('engine.visual_prompts.call') as mock:
            prompt = build_crowd_prompt(entity)
        assert mock.call_count == 0
        assert 'patrons' in prompt
        assert 'individual descriptions' not in prompt

    def test_scene_prompt_routes_collective_to_crowd(self):
        entity = Entity(name='Guards', role='guards', is_collective=True)
        with patch('engine.visual_prompts.call') as mock:
            prompt = build_scene_image_prompt(entity)
        assert mock.call_count == 0
        assert 'guards' in prompt

    def test_scene_prompt_routes_named_character_to_individual(self):
        entity = Entity(name='Elena', appearance=APPEARANCE, is_collective=False)
        with patch('engine.visual_prompts.call') as mock:
            mock.return_value = ('silver hair', 0.1)
            prompt = build_scene_image_prompt(entity)
        assert mock.call_count == 1
        assert 'silver hair' in prompt


class TestClothingSafetyRegression:
    """Reproduces the real failure: a live turn against a character whose
    appearance (by inference.py's own design) never mentions clothing
    generated lingerie for a plain library scene. This must not regress."""

    def test_appearance_with_no_clothing_still_gets_a_clothing_tag(self):
        entity = Entity(name='Elena', appearance=APPEARANCE_NO_CLOTHING)
        with patch('engine.visual_prompts.call') as mock:
            # Real distilled output contained no clothing keyword, matching
            # what was actually observed in the live incident.
            mock.return_value = (
                'petite athletic build, long curly silver hair, '
                'bright piercing emerald green eyes, heart-shaped face, '
                'toned arms, porcelain-like complexion',
                0.1,
            )
            prompt = build_character_image_prompt(entity)
        assert DEFAULT_CLOTHING_TAG in prompt
