"""
AI service layer.
- generate_scenario_metadata(): uses LLaMA 3.3 70B via Groq (one call at scenario creation)
- engine_init(): initializes the full notebook engine for a new session
- engine_turn(): runs one turn through the full engine (not a bare Groq call)
"""
import asyncio
import re
import json
import logging
from groq import Groq
from config import settings
from engine import Scenario, scenario_to_engine, serialize_engine, deserialize_engine
from engine.call import init_client, MAX_RAW_TURNS
from engine.inference import (
    detect_from_player_input,
    translate_input, translate_output,
    preprocess, build_system,
    check_sovereignty, extract_and_save_assumptions, summarize_chunk,
    guard_response, sanitize_input,
)
from engine.call import async_stream_call
from engine.visual_prompts import build_scene_image_prompt
from engine.voice_prompts import get_voice_profile, filter_for_narration
from database import get_redis
from rate_limiter import reserve_token_budget

logger = logging.getLogger(__name__)

# Conservative flat reservation for a whole turn, not a single Groq call --
# engine.step()/regenerate() make several internal call() invocations
# (narrative response, guard validation, occasional summarization), each
# funneling through engine/call.py individually. Retrofitting the reservation
# into every one of those ~20 call sites would be invasive; reserving once
# per turn at this single choke point (every turn passes through here before
# entering the sync engine) is the safer trade: slightly less precise, but
# serializes concurrent turns at the right granularity without touching the
# engine internals. call()'s existing retry-on-429 remains the fine-grained
# safety net for any single call that still overshoots.
# 4000 = the upper end of the real measured per-turn range (see
# rate_limiter.py's docstring / SESSION_SUMMARY.md), not a guess.
TURN_TOKEN_ESTIMATE = 4000
# engine_prefab/engine_init run 15-20 internal Groq calls (full context-layer
# build) rather than a turn's 2-4 -- scaled up proportionally, not a separate
# measurement. engine_prefab already has its own concurrency gate
# (worker.py's PREFAB_MAX=2), this is a second, independent layer against
# the same TPM ceiling, not a replacement for it.
FULL_INIT_TOKEN_ESTIMATE = 20000


async def _reserve_turn_budget(estimated_tokens: int = TURN_TOKEN_ESTIMATE):
    """Blocks until there's room in the current minute's Groq TPM budget.
    Raises RuntimeError (not silently proceeds) if the queue doesn't clear
    within reserve_token_budget's max_wait -- callers should surface that
    as a capacity/retry message, not let the turn fire into a near-certain
    429 storm."""
    redis = await get_redis()
    max_wait = 60.0 if estimated_tokens > TURN_TOKEN_ESTIMATE else 30.0
    ok = await reserve_token_budget(redis, estimated_tokens, max_wait=max_wait)
    if not ok:
        raise RuntimeError("Groq capacity is fully booked right now — try again shortly.")

# Engine models available for play. The entire previous roster (llama-3.1-8b-
# instant, llama-3.3-70b-versatile, llama-3.1-70b-versatile, gemma2-9b-it,
# mixtral-8x7b-32768) was decommissioned by Groq -- confirmed live via
# client.models.list() against the real account, every one of those IDs is
# gone, not just the two Groq's deprecation-notice emails named. Replaced
# with Groq's own recommended successors, cross-checked against that same
# live model list (openai/gpt-oss-20b, openai/gpt-oss-120b, qwen/qwen3.6-27b
# all confirmed present and active).
ENGINE_MODELS = {
    "openai/gpt-oss-20b":  "GPT-OSS 20B — fastest",
    "openai/gpt-oss-120b": "GPT-OSS 120B — best quality",
    "qwen/qwen3.6-27b":    "Qwen 3.6 27B — balanced",
}
DEFAULT_ENGINE_MODEL = "openai/gpt-oss-20b"
METADATA_MODEL       = "openai/gpt-oss-120b"

_groq = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None


# ── Scenario metadata ─────────────────────────────────────────

async def generate_scenario_metadata(char_name, char_title, char_personality, greeting) -> dict:
    if not _groq:
        return {"title": char_name, "brief": char_title, "tags": [], "intensity": 3}

    prompt = (
        "You are reading a roleplay scenario.\n\n"
        f"Character: {char_name}\nRole: {char_title}\n"
        f"Personality: {char_personality}\n\nOpening:\n{greeting}\n\n"
        "Generate metadata as JSON with exactly these keys:\n"
        "  title: short evocative display title (3-6 words, no quotes)\n"
        "  brief: 1-2 sentences — the hook. What will the player walk into?\n"
        "  tags: array of 3-5 single-word thematic tags from this list:\n"
        "    drama, power, grief, betrayal, romance, thriller, tension,\n"
        "    obsession, manipulation, revenge, loyalty, corruption, desire, loss, control\n"
        "  intensity: integer 1-5 (1=mild, 5=extreme)\n\n"
        "Output ONLY valid JSON. No markdown. No explanation."
    )
    completion = _groq.chat.completions.create(
        model=METADATA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300, temperature=0.2,
    )
    raw = completion.choices[0].message.content.strip()
    try:
        code_block = re.search(r'```(?:\w+)?\s*([\s\S]*?)```', raw)
        clean = code_block.group(1).strip() if code_block else raw.strip()
        start = clean.find("{"); end = clean.rfind("}") + 1
        return json.loads(clean[start:end])
    except Exception:
        return {"title": char_name, "brief": char_title, "tags": [], "intensity": 3}


# ── Engine init ───────────────────────────────────────────────

async def engine_init(
    char_name: str,
    char_pronouns: str,
    char_title: str,
    char_personality: str,
    greeting: str,
    player_name: str,
    player_pronouns: str,
    engine_model: str = DEFAULT_ENGINE_MODEL,
    content_filter: str = "off",
) -> dict:
    """
    Initializes the full roleplay engine for a new session.
    Runs 15-20 Groq calls to build all 7 context layers.
    Returns serialized engine state ready for Redis storage.
    """
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    await _reserve_turn_budget(FULL_INIT_TOKEN_ESTIMATE)
    # Point engine's call() at the chosen model
    init_client(settings.groq_api_key, engine_model)

    # Build content filter object
    from engine.types import ContentFilter, FilterState
    cf = ContentFilter(state=FilterState(content_filter))

    scenario = Scenario(
        char_name=char_name,
        char_pronouns=char_pronouns,
        char_title=char_title,
        char_personality=char_personality,
        greeting=greeting,
        player_name=player_name,
        player_pronouns=player_pronouns,
        content_filter=cf,
    )

    # Run the heavy sync engine init in a thread
    engine = await asyncio.to_thread(scenario_to_engine, scenario)
    return serialize_engine(engine)


# ── Engine prefab (pre-computed at scenario creation) ─────────

async def engine_prefab(
    char_name: str,
    char_pronouns: str,
    char_title: str,
    char_personality: str,
    greeting: str,
) -> dict | None:
    """
    Pre-computes all scenario-specific engine layers once at scenario creation.
    Uses a neutral placeholder persona so the heavy LLM work (layer1, NPC scan,
    world inference, fact pinning, character inference) is done ahead of time.
    Result stored in Scenario.prefab_engine_state.
    At session start, engine_init_from_prefab() patches the actual player in
    with 1 LLM call instead of 15-20.
    """
    if not settings.groq_api_key:
        return None
    await _reserve_turn_budget(FULL_INIT_TOKEN_ESTIMATE)
    init_client(settings.groq_api_key, DEFAULT_ENGINE_MODEL)
    from engine.types import ContentFilter, FilterState
    scenario = Scenario(
        char_name=char_name,
        char_pronouns=char_pronouns,
        char_title=char_title,
        char_personality=char_personality,
        greeting=greeting,
        player_name='Player',
        player_pronouns='they/them',
        content_filter=ContentFilter(state=FilterState.OFF),
    )
    engine = await asyncio.to_thread(scenario_to_engine, scenario)
    return serialize_engine(engine)


async def engine_init_from_prefab(
    prefab_state: dict,
    player_name: str,
    player_pronouns: str,
    greeting: str,
    engine_model: str = DEFAULT_ENGINE_MODEL,
    content_filter: str = 'off',
) -> dict:
    """
    Patches a prefab engine state with the actual player persona.
    Only runs 1 LLM call (player appearance highlights) vs 15-20 for a full init.
    """
    import copy
    from engine.call import call
    await _reserve_turn_budget(estimated_tokens=1500)  # single call -- appearance highlights only
    init_client(settings.groq_api_key, engine_model)

    state = copy.deepcopy(prefab_state)

    # Patch player entity name and pronouns
    for e in state['entities']:
        if e.get('is_player'):
            e['name'] = player_name
            e['pronouns'] = player_pronouns
            break

    # Rebuild sp_note for the actual player
    words = greeting.lower().split()
    is_2p = (words.count('you') + words.count('your') + words.count('yourself')) >= 2
    state['sp_note'] = (
        f'IMPORTANT: In the narrative passages of this scene, '
        f'"you" and "your" refer to {player_name} ({player_pronouns}). '
        f'In dialogue (inside quotes), "you" refers to whoever the speaker is addressing '
        f'and must NOT be interpreted as {player_name} unless context makes it clear.\n\n'
    ) if is_2p else ''

    # Run highlights inference for the actual player (1 LLM call)
    sp_note = state['sp_note']
    highlights, _ = await asyncio.to_thread(
        call,
        f'{sp_note}'
        f'Describe the physical appearance of {player_name} ({player_pronouns}) '
        f'based on what the opening scene states or implies.\n'
        f'Cover: height, build, hair, face, clothing, emotional state visible on the body.\n'
        f'2-3 sentences. Concrete and specific.',
        [{'role': 'user', 'content': greeting}],
        200,
        0.2,
    )
    for e in state['entities']:
        if e.get('is_player'):
            e['appearance'] = highlights
            break

    # Set content filter and reset session-specific state
    state['filter_state']    = content_filter
    state['filter_lock']     = False
    state['turn']            = 0
    state['metrics']         = {'turns': 0, 'sv_violations': 0, 'latencies': [], 'compressions': 0}
    dh = state['display_history']
    opening_msg              = dh[0] if dh else {'role': 'assistant', 'content': greeting}
    state['history']         = [opening_msg]
    state['display_history'] = [opening_msg]

    return state


# ── Engine turn ───────────────────────────────────────────────

def _build_turn_media_context(engine, response_text: str) -> dict | None:
    """Picks a focus entity for the turn's image (the first present,
    non-collective NPC -- the player is the narrative "camera" per the
    engine's own sovereignty design, so depicting the player themselves
    isn't the natural default; depicting who they're looking at is) and
    builds the image prompt + narration text + voice from it. Returns
    None if there's no NPC to depict yet (e.g. an empty opening scene) --
    callers should skip enqueuing media for that turn rather than guess."""
    focus = next(
        (e for e in engine.entities if e.present and not e.is_player and not e.is_collective),
        None,
    )
    if focus is None:
        return None
    image_prompt = build_scene_image_prompt(focus)
    voice = get_voice_profile(focus)
    narration_text = filter_for_narration(response_text)
    if not narration_text:
        return None
    return {
        "image_prompt": image_prompt,
        "narration_text": narration_text,
        "voice_id": voice["voice_id"],
        "voice_speed": voice["speed"],
    }


async def engine_step(
    engine_state: dict,
    player_input: str = "",
    engine_model: str = DEFAULT_ENGINE_MODEL,
    continue_narrative: bool = False,
) -> dict:
    """
    Runs one turn through the full engine.
    Deserializes state from Redis, calls engine.step(), returns
    updated state + response. continue_narrative=True: no player input this
    turn — the narrator advances the scene on its own (see engine.step()).
    """
    await _reserve_turn_budget()
    # Re-point call() at the model for this turn
    init_client(settings.groq_api_key, engine_model)

    engine = deserialize_engine(engine_state)
    result = await asyncio.to_thread(engine.step, player_input, continue_narrative)
    media_context = _build_turn_media_context(engine, result["response"])

    return {
        "response":    result["response"],
        "sovereign":   result["sovereign"],
        "violations":  result["violations"],
        "turn":        result["turn"],
        "engine_state": serialize_engine(engine),
        "media_context": media_context,
    }


async def engine_regenerate(
    engine_state: dict,
    engine_model: str = DEFAULT_ENGINE_MODEL,
) -> dict:
    """
    Discards the last turn's response and generates a fresh one for the same
    player input (see engine.regenerate()) — same shape as engine_step's
    return so callers don't need to special-case it. Raises ValueError if
    there's no turn yet to regenerate.
    """
    await _reserve_turn_budget()
    init_client(settings.groq_api_key, engine_model)

    engine = deserialize_engine(engine_state)
    result = await asyncio.to_thread(engine.regenerate)
    media_context = _build_turn_media_context(engine, result["response"])

    return {
        "response":    result["response"],
        "sovereign":   result["sovereign"],
        "violations":  result["violations"],
        "turn":        result["turn"],
        "engine_state": serialize_engine(engine),
        "media_context": media_context,
    }


# ── Engine turn (streaming) ───────────────────────────────────

async def engine_step_stream(engine_state, player_input, engine_model=DEFAULT_ENGINE_MODEL, continue_narrative=False):
    """Streaming version — yields tokens, then a final metadata dict.

    continue_narrative mirrors Engine.step()'s handling (see its docstring):
    no player input, the narrator advances the scene on its own. Added
    after code review found this streaming path had silently diverged from
    Engine.step() by never supporting it at all -- /sessions/{id}/turn-stream
    couldn't do what /sessions/{id}/continue does. Not currently wired to
    any frontend "continue" trigger (that button calls the non-streaming
    endpoint), but the two turn pipelines should stay capable of the same
    things rather than drift further apart.
    """
    await _reserve_turn_budget()
    init_client(settings.groq_api_key, engine_model)
    engine = deserialize_engine(engine_state)

    engine.turn += 1
    engine.metrics['turns'] += 1

    if continue_narrative:
        parsed = {
            'clean': '(no player input — narrator continues the scene)',
            'target': engine.last_target, 'actions': [], 'spoken': [], 'thoughts': [],
            'same_space_risk': any(
                e.pronouns == engine.persona.pronouns and e.present and not e.is_player
                for e in engine.entities
            ),
        }
        msg = (
            '[SYSTEM: The player takes no action this turn. Continue the scene '
            'naturally — advance time, deepen the moment, let other characters or '
            f'the environment act. Do not attribute any new action, decision, or '
            f'dialogue to {engine.persona.name}; they remain exactly as they were '
            'left at the end of the previous turn.]'
        )
    else:
        player_input = sanitize_input(player_input)
        engine.scene_lang = detect_from_player_input(player_input, engine.scene_lang)
        # translate_input calls sync call() — run in thread to avoid blocking the event loop.
        english_input = await asyncio.to_thread(translate_input, player_input, engine.scene_lang)
        parsed = preprocess(english_input, engine.persona, engine.entities, engine.last_target)
        msg = parsed['annotated']
        if parsed['thoughts']:
            msg += '\n(internal context only, do not reference: ' + ' | '.join(parsed['thoughts']) + ')'
    engine.last_target = parsed['target']

    system = build_system(
        engine.persona, engine.entities, engine.world,
        engine.memory, engine.layer1,
        same_space_risk=parsed['same_space_risk'],
        scene_lang=engine.scene_lang,
        content_filter=engine.filter,
    )

    engine.history.append({'role': 'user', 'content': msg})
    # display_history stores the clean input (no [SOURCE=PLAYER:...] annotation)
    # -- skipped for continue_narrative, same as Engine.step(): there's no
    # player message to show as a chat bubble.
    if not hasattr(engine, 'display_history'):
        engine.display_history = []
    if not continue_narrative:
        engine.display_history.append({'role': 'user', 'content': parsed['clean']})
    previous_response = next(
        (m['content'] for m in reversed(engine.display_history[:-1]) if m.get('role') == 'assistant'),
        '',
    )

    hot = engine.history[-MAX_RAW_TURNS:]

    response_en = ""
    async for token in async_stream_call(system, hot):
        if token is not None:
            response_en += token
        else:
            break

    # translate_output calls sync call() — run in thread.
    response = await asyncio.to_thread(translate_output, response_en, engine.scene_lang)
    response, guard = await asyncio.to_thread(
        guard_response,
        response,
        parsed['clean'],
        engine.persona,
        engine.entities,
        engine.world,
        engine.memory,
        engine.layer1,
        engine.scene_lang,
        previous_response,
    )
    if guard.get('revised'):
        logger.warning('Continuity guard revised streamed response: %s', guard.get('violations'))
    sv = check_sovereignty(response, engine.persona.name, engine.entities)
    if not sv['clean']:
        engine.metrics['sv_violations'] += sv['count']
        logger.warning('Sovereignty violation leaked through guard (hard block, stream): %s', sv['violations'])
        response = 'A tense silence holds. Nothing moves.'
    for i in range(0, len(response), 24):
        yield {"type": "token", "content": response[i:i + 24]}

    engine.history.append({'role': 'assistant', 'content': response})
    engine.display_history.append({'role': 'assistant', 'content': response})

    # Synchronous calls (see engine/inference.py) run off the event loop via
    # to_thread, but must be awaited here — the serialize_engine() a few lines
    # down needs their mutations to have already landed, not be still in flight.
    await asyncio.to_thread(
        extract_and_save_assumptions, response, engine.persona, engine.entities, lock=engine._lock
    )
    if len(engine.history) > MAX_RAW_TURNS:
        chunk = engine.history[:-MAX_RAW_TURNS]
        await asyncio.to_thread(summarize_chunk, chunk, engine.persona.name, engine.memory)
        engine.history = engine.history[-MAX_RAW_TURNS:]
        engine.metrics['compressions'] += 1

    yield {
        "type": "done",
        "response": response,
        "session_id": None,  # caller fills this
        "turn": engine.turn,
        "sovereign": sv['clean'],
        "violations": sv['violations'],
        "engine_state": serialize_engine(engine),
        "media_context": _build_turn_media_context(engine, response),
    }
