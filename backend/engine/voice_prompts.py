"""
Per-character TTS voice derivation and narration-text filtering.

Kokoro is a fixed small voice bank (~50 named voices, af_*/am_*/bf_*/bm_*
for English), not a description-to-voice model — there's no way to
synthesize an arbitrary "hoarse, playful" voice on demand. What IS
controllable: which of the fixed voices best fits a character (mapped
from pronouns/role/mood), and a continuous speed multiplier. Both are
derived once per character and locked (see AVAILABLE_VOICES / get_voice_profile)
rather than re-derived every turn, matching the earlier decision that
voice identity should stay stable turn to turn, same principle as
everything else in this session about not letting an established
identity drift.

Turn text uses **bold** for narrated stage direction and "quotes" for
dialogue (see engine/inference.py's build_system prompt). Kokoro reads
raw text, so a literal double-asterisk sequence left in would be
mispronounced/misread by the phonemizer — same class of problem the
sovereignty-check code already solves for a different purpose via
re.sub(r'\\*+', '', ...). filter_for_narration() goes a step further:
it drops whole action-only sentences rather than just stripping their
markers, since a pure stage-direction beat ("She crosses her arms.")
isn't meant to be read aloud as narration.
"""
import re
import logging
from .call import call
from .types import Entity

logger = logging.getLogger(__name__)

# English-language voices only (af_/am_ = American, bf_/bm_ = British) --
# narrate() defaults to lang="en-us", and picking a non-English voice ID
# for English text would be a real mismatch, not just a style choice.
AVAILABLE_VOICES = [
    'af_alloy', 'af_aoede', 'af_bella', 'af_heart', 'af_jessica', 'af_kore',
    'af_nicole', 'af_nova', 'af_river', 'af_sarah', 'af_sky',
    'am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 'am_michael',
    'am_onyx', 'am_puck',
    'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily',
    'bm_daniel', 'bm_fable', 'bm_george', 'bm_lewis',
]

_VOICE_SYSTEM = (
    "You pick a text-to-speech voice for a fictional character from a fixed list -- "
    "you are NOT describing a voice freely, you must choose exactly one ID from the "
    "list given. Match gender to the character's pronouns. Use role, mood, and age "
    "cues in the description to prefer a voice that fits (younger/older, warmer/"
    "harsher) among the available options -- there is no perfect match, pick the "
    "closest. Also choose a speed multiplier: 0.85-0.95 for slow/tired/elderly/"
    "deliberate characters, 1.0 for neutral, 1.05-1.2 for excitable/young/urgent "
    "characters. Output ONLY compact JSON: {\"voice_id\": \"...\", \"speed\": 1.0}"
)


def _derive_voice(entity: Entity) -> dict:
    import json
    description = (
        f"Name: {entity.name}\n"
        f"Pronouns: {entity.pronouns}\n"
        f"Role: {entity.role}\n"
        f"Mood: {entity.mood}\n"
        f"Appearance: {entity.appearance}\n"
        f"Available voices: {', '.join(AVAILABLE_VOICES)}"
    )
    result, _ = call(_VOICE_SYSTEM, [{'role': 'user', 'content': description}],
                      max_tokens=40, temp=0.1)
    try:
        data = json.loads(result.strip())
        voice_id = data.get('voice_id', '')
        speed = float(data.get('speed', 1.0))
    except (json.JSONDecodeError, ValueError, TypeError):
        voice_id, speed = '', 1.0

    if voice_id not in AVAILABLE_VOICES:
        # LLM picked something not on the list (hallucinated ID, wrong
        # format) -- fail to a sane default rather than pass a bad ID to
        # Kokoro, which would raise at narration time instead of now.
        logger.warning(f'[voice_prompts] invalid voice_id {voice_id!r} for {entity.name}, defaulting')
        voice_id = 'af_sarah' if 'she' in (entity.pronouns or '').lower() else 'am_michael'
    speed = max(0.7, min(1.4, speed))  # sane bounds regardless of what the LLM said
    return {'voice_id': voice_id, 'speed': speed}


def get_voice_profile(entity: Entity) -> dict:
    """Locked per character after first use -- unlike get_visual_tags,
    this does NOT re-derive on later changes to mood/appearance. A
    character's voice shouldn't drift turn to turn just because their
    mood field changed; only the sentence-level speed modulation (not
    implemented here) should reflect momentary tone."""
    if entity.voice_id:
        return {'voice_id': entity.voice_id, 'speed': entity.voice_speed}
    profile = _derive_voice(entity)
    entity.voice_id = profile['voice_id']
    entity.voice_speed = profile['speed']
    logger.info(f'[voice_prompts] {entity.name} locked to voice {profile["voice_id"]} @ {profile["speed"]}x')
    return profile


_ACTION_ONLY_RE = re.compile(r'^\s*\*{1,2}[^*]+\*{1,2}\s*$')
_MARKER_RE = re.compile(r'\*+')


def filter_for_narration(turn_text: str) -> str:
    """Drops whole sentences/lines that are pure **action** stage
    direction, strips remaining bold markers from what's left (dialogue
    and prose survive; a literal '**' left in raw text would otherwise
    be mispronounced by Kokoro's phonemizer)."""
    kept = []
    for line in turn_text.splitlines():
        if not line.strip():
            continue
        if _ACTION_ONLY_RE.match(line):
            continue
        kept.append(_MARKER_RE.sub('', line))
    return '\n'.join(kept).strip()
