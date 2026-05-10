import re
import json
import threading
import unicodedata
import contextvars
import concurrent.futures
import logging
from .call import call
from .types import Entity

logger = logging.getLogger(__name__)

# ── Language detection ────────────────────────────────────────

LANG_DETECT_EXAMPLES = [
    ('The evening settles over the street.', 'English'),
    ('Le soir tombe sur la rue.', 'French'),
    ('الليل يهبط على الشارع.', 'Egyptian Arabic'),
    ('La sera scende sulla strada.', 'Italian'),
    ('Der Abend senkt sich über die Straße.', 'German'),
    ('La noche cae sobre la calle.', 'Spanish'),
    ('A noite cai sobre a rua.', 'Portuguese'),
    ('夜が通りに落ちる。', 'Japanese'),
    ('夜幕降临在街道上。', 'Chinese'),
    ('Вечер опускается на улицу.', 'Russian'),
]

LANG_SAMPLES = {
    'English':         'The room was silent. She did not move.',
    'French':          'La pièce était silencieuse. Elle ne bougea pas.',
    'Egyptian Arabic': 'الغرفة كانت صامتة. هي لم تتحرك.',
    'Italian':         'La stanza era silenziosa. Lei non si mosse.',
    'German':          'Das Zimmer war still. Sie bewegte sich nicht.',
    'Spanish':         'La habitación estaba en silencio. Ella no se movió.',
    'Portuguese':      'O quarto estava silencioso. Ela não se moveu.',
    'Japanese':        '部屋は静かだった。彼女は動かなかった。',
    'Chinese':         '房间很安静。她没有动。',
    'Russian':         'Комната была тихой. Она не двигалась.',
}


def detect_language_fast(text: str, threshold: int = 5) -> str:
    """Fast regex-based language detection. Returns language name or None if uncertain."""
    sample = text[:300]

    # Arabic script
    if re.search(r'[\u0600-\u06FF]', sample):
        return 'Egyptian Arabic'
    # CJK
    if re.search(r'[\u4E00-\u9FFF]', sample):
        return 'Chinese'
    if re.search(r'[\u3040-\u309F\u30A0-\u30FF]', sample):
        return 'Japanese'
    # Cyrillic
    if re.search(r'[\u0400-\u04FF]', sample):
        return 'Russian'
    # Latin-based — word-boundary marker scoring to avoid false matches inside
    # English words (e.g. 'la' inside 'place', 'con' inside 'concern').
    # threshold=5 requires a strong signal before switching languages.
    latin_lower = sample.lower()

    lang_markers = {
        'French':     [r'\ble\b', r'\bla\b', r'\bles\b', r'\bun\b', r'\bune\b', r'\bdes\b',
                       r'\bet\b', r'\best\b', r'\bdans\b', r'\bpour\b', r'\bque\b', r'\bpas\b',
                       r'\belle\b', r'\bson\b'],
        'Spanish':    [r'\bel\b', r'\blos\b', r'\blas\b', r'\buna\b', r'\by\b', r'\bes\b',
                       r'\ben\b', r'\bpara\b', r'\bque\b', r'\bpero\b', r'\bmuy\b', r'\bcomo\b'],
        'German':     [r'\bund\b', r'\bder\b', r'\bdie\b', r'\bdas\b', r'\bist\b', r'\bein\b',
                       r'\beine\b', r'\bnicht\b', r'\bmit\b', r'\bauf\b', r'\bsich\b'],
        'Italian':    [r'\bil\b', r'\blo\b', r'\bla\b', r'\bgli\b', r'\ble\b', r'\buno\b',
                       r'\buna\b', r'\bche\b', r'\bnon\b', r'\bper\b', r'\bcon\b'],
        'Portuguese': [r'\bum\b', r'\buma\b', r'\bque\b', r'\bnao\b', r'\bpara\b',
                       r'\bcom\b', r'\bmas\b', r'\bcomo\b', r'\bos\b', r'\bas\b'],
    }

    scores = {lang: sum(1 for m in markers if re.search(m, latin_lower))
              for lang, markers in lang_markers.items()}

    best_lang = max(scores, key=scores.get)
    if scores[best_lang] >= threshold:
        return best_lang

    return None  # uncertain — will fallback to LLM


def detect_scene_language(opening):
    # Fast path: regex detection (threshold=3 is fine for the full opening text)
    fast = detect_language_fast(opening, threshold=3)
    if fast:
        logger.info(f'Scene language (fast): {fast}')
        return fast

    # Slow path: LLM detection
    examples = '\n'.join(f'Text: {t}\nLanguage: {l}' for t, l in LANG_DETECT_EXAMPLES)
    prompt   = f'{examples}\nText: {opening[:300]}\nLanguage:'
    result, _ = call(
        'You detect the language of text. Output only the language name exactly as shown. Nothing else.',
        [{'role': 'user', 'content': prompt}],
        max_tokens=8, temp=0.0
    )
    detected = result.strip().split('\n')[0].strip().rstrip('.')
    known    = {l for _, l in LANG_DETECT_EXAMPLES}
    lang = detected if detected in known else 'English'
    logger.info(f'Scene language (LLM): {lang}')
    return lang


def detect_from_player_input(text, current_lang):
    """Detect language from player input, keeping the current session language unless
    very confident the player switched. No LLM call — too expensive per-turn and
    unreliable on short creative text. Script-based detection (CJK/Arabic/Cyrillic)
    is immediate; Latin-based requires a high threshold to prevent false positives."""
    if not text.strip():
        return current_lang
    # Pass threshold=5 — needs strong evidence before switching a Latin-script session.
    detected = detect_language_fast(text, threshold=5)
    if detected and detected != current_lang:
        logger.info(f'Language switched mid-session: {current_lang} -> {detected}')
        return detected
    return current_lang


def get_lang_sample(lang):
    return LANG_SAMPLES.get(lang, LANG_SAMPLES['English'])


def translate_input(text, source_lang):
    if source_lang == 'English':
        return text
    result, _ = call(
        'Translate the following text to English. Output only the translation. Nothing else.',
        [{'role': 'user', 'content': f'Source language: {source_lang}\nText: {text}'}],
        max_tokens=400, temp=0.1
    )
    return result


def translate_output(text, target_lang):
    if target_lang == 'English':
        return text
    sample = get_lang_sample(target_lang)
    result, _ = call(
        f'Translate to {target_lang}. Match this register and style: "{sample}". '
        f'Output only the translation. Nothing else.',
        [{'role': 'user', 'content': text}],
        max_tokens=800, temp=0.1
    )
    return result


# ── Appearance parser ─────────────────────────────────────────

def parse_opening_appearance(opening, persona, highlights=''):
    prompt = (
        f'Character: {persona.name} ({persona.pronouns})\n'
        f'Highlights from scenario: {highlights or "none"}\n\n'
        f'Opening scene:\n{opening}\n\n'
        f'Write a complete, permanent physical description of {persona.name}.\n'
        f'Use pronouns {persona.pronouns} throughout.\n'
        f'Cover: height, build, hair, face, clothing. Be specific and visual.\n'
        f'Use any details explicitly in the opening text first.\n'
        f'For anything not described, infer what fits — era, setting, emotional context.\n'
        f'2-3 sentences. Concrete and consistent.'
    )
    result, _ = call(
        'You extract character appearance details. Output only the appearance description, nothing else.',
        [{'role': 'user', 'content': prompt}],
        max_tokens=160, temp=0.1
    )
    persona.appearance = result
    logger.info(f'{persona.name} appearance:\n  {result}')
    return result


# ── Name substitution ─────────────────────────────────────────

def _substitute_names(text, entities):
    result = text
    for e in entities:
        if e.is_player or e.is_collective or not e.role:
            continue
        if e.name.lower() not in text.lower() and e.role.lower() in text.lower():
            # \b prevents matching inside longer words (e.g. role "man" inside "woman")
            pattern = re.compile(r'\b' + re.escape(e.role) + r'\b', re.IGNORECASE)
            result  = pattern.sub(e.name, result)
            base    = e.role.lower()
            for article in ['his ', 'her ', 'the ', 'my ', 'your ']:
                if base.startswith(article):
                    bare     = e.role[len(article):]
                    pattern2 = re.compile(
                        r"(?i)(?:his |her |the |my |your |\w+'s )" + re.escape(bare) + r'\b'
                    )
                    result = pattern2.sub(e.name, result)
                    break
    return result


# ── Character inference ───────────────────────────────────────

def _infer_single_npc(e, entities, opening_copy, layer1_copy, sp_note):
    """Infer behavioral read, never_does, and appearance for one NPC. Thread-safe."""
    entity_context = (
        f'Name: {e.name}\n'
        f'Pronouns: {e.pronouns}\n'
        + (f'Role in this scene: {e.role}\n' if e.role else '')
        + (f'Appearance: {e.appearance}\n' if e.appearance else '')
        + (f'Emotional state: {e.emotional_state}\n' if e.emotional_state else '')
    )

    read, _ = call(
        f'{sp_note}'
        'You are the narrator. Build a private psychological understanding of this character.\n'
        'Write 5-7 sentences in present tense.\n'
        'Cover: core drive, what they conceal, how they exert power, what they fear, '
        'how they regard others in this scene, what they do under pressure.\n'
        'End with: "Under pressure, [name] always..."\n'
        'Be specific to this scene. No generic psychology.',
        [{'role': 'user', 'content':
            f'{entity_context}\n\n'
            f'Scene context:\n{layer1_copy[:600]}\n\n'
            f'Opening:\n{opening_copy[:600]}'
        }],
        max_tokens=250, temp=0.4
    )
    e.behavioral_read = read
    logger.info(f'{e.name} behavioral read:\n  {read[:200]}')

    never, _ = call(
        f'{sp_note}'
        'Complete this sentence in one line: "[Name] would never..."\n'
        'Name the ONE thing most fundamentally opposed to their nature.\n'
        'Be specific to this character. Output the complete sentence only.',
        [{'role': 'user', 'content':
            f'{entity_context}\n'
            f'Behavioral read: {read}\n\n'
            f'Scene: {layer1_copy[:400]}'
        }],
        max_tokens=40, temp=0.2
    )
    e.never_does = never.strip()
    logger.info(f'{e.name} never: {e.never_does}')

    if not e.appearance:
        persona_ref = next(
            (f'\nOther character already in scene — must look DIFFERENT:\n{en.appearance}'
             for en in entities if en.is_player and en.appearance),
            ''
        )
        app, _ = call(
            f'Generate permanent physical appearance for {e.name}.\n'
            f'Pronouns: {e.pronouns}.\n'
            f'Cover ONLY stable traits: height, build, hair, eyes, skin, face shape.\n'
            f'No wounds, tears, sweat, or clothing.\n'
            f'Make visually distinct from others in scene.\n'
            f'Complete every sentence. 2-3 sentences.',
            [{'role': 'user', 'content':
                f'Character: {e.name} ({e.pronouns})\n'
                f'Role: {e.role}\n'
                f'Scene context: {layer1_copy[:400]}'
                f'{persona_ref}'
            }],
            max_tokens=150, temp=0.4
        )
        e.appearance = app.strip()


def infer_characters(entities, opening, layer1, sp_note=''):
    npcs = [e for e in entities if not e.is_player and not e.is_collective]
    if not npcs:
        return

    opening_copy = _substitute_names(opening, entities)
    layer1_copy  = _substitute_names(layer1,  entities)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(npcs)) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, _infer_single_npc, e, entities, opening_copy, layer1_copy, sp_note)
            for e in npcs
        ]
        for f in concurrent.futures.as_completed(futures):
            f.result()  # propagate any exceptions


# ── Collective / NPC detection ────────────────────────────────

def _is_role_descriptor(name, context):
    result, _ = call(
        'Does this phrase describe a ROLE or RELATIONSHIP that a specific person holds, '
        'rather than being their actual name?\n'
        'Examples of role descriptors: "the second wife", "his butler", "the employer", "her lover"\n'
        'Examples of actual names: "James", "Sarah", "Thomas", "Amara"\n'
        'Output only YES or NO.',
        [{'role': 'user', 'content': f'Phrase: "{name}"\nContext: {context[:400]}'}],
        max_tokens=5, temp=0.0
    )
    return 'YES' in result.upper()


def _generate_npc_name(role, context):
    result, _ = call(
        'Generate a single realistic name for this character based on their role and scene context.\n'
        'The name should fit the setting, era, and cultural context of the scene.\n'
        'Output ONLY the name. Nothing else.',
        [{'role': 'user', 'content': f'Role: {role}\nScene context: {context[:400]}'}],
        max_tokens=12, temp=0.7
    )
    return result.strip().split()[0].strip('.,!?\'"')


def _infer_collective_role(name, context):
    result, _ = call(
        'Describe the role and likely behavior of this group in one sentence.',
        [{'role': 'user', 'content': f'Group: {name}\nContext: {context[:400]}'}],
        max_tokens=60, temp=0.3
    )
    return result.strip()


def _is_collective_group(name, context):
    result, _ = call(
        'You are classifying a name or phrase from a story scene.\n'
        'Decide: does this refer to a GROUP of multiple people, or a single INDIVIDUAL?\n\n'
        'A GROUP:\n'
        '  - Any word or phrase referring to multiple people collectively\n'
        '  - A role held by many people simultaneously\n'
        '  - Examples: servants, students, guards, the congregation, the crowd,\n'
        '    the household staff, his men, the mourners, the community\n\n'
        'An INDIVIDUAL:\n'
        '  - A specific single person — named, unnamed, or referred to by a relational title\n'
        '  - Relational titles are ALWAYS individual: second wife, first wife, husband, lover,\n'
        '    butler, employer, mistress, guardian, the stranger, the old man\n'
        '  - "second wife" = one specific woman. "wife" = one person. Never a group.\n\n'
        'Key rule: if the phrase could refer to exactly ONE person in this story, it is INDIVIDUAL.\n\n'
        'Output only the word GROUP or INDIVIDUAL. Nothing else.',
        [{'role': 'user', 'content': f'Name or phrase: "{name}"\nScene context: {context[:400]}'}],
        max_tokens=5, temp=0.0
    )
    return 'GROUP' in result.upper()


def scan_opening_for_npcs(opening, layer1, entities, sp_note='', char_personality=''):
    persona   = next((e for e in entities if e.is_player), None)
    main_char = next((e for e in entities if not e.is_player), None)

    # Name the known characters explicitly so the LLM knows exactly who to skip.
    known_names = []
    if main_char:
        known_names.append(f'"{main_char.name}" (main character)')
    if persona:
        known_names.append(f'"{persona.name}" (player/persona)')
    exclude_clause = (
        f'OTHER than {" and ".join(known_names)}'
        if known_names else 'OTHER than the main character and the player/persona'
    )

    combined = f'{sp_note}{layer1}\n\n{opening}'
    result, _ = call(
        f'{sp_note}'
        f'List all characters {exclude_clause} in this scene.\n'
        'Include only characters who are physically present or directly speak/act in the opening scene.\n'
        'Do NOT include characters only mentioned as backstory, colleagues, or off-screen references.\n'
        'Output a JSON array of strings — names or role descriptors as they appear in the text.\n'
        'Example: ["the second wife", "the butler", "James"]\n'
        'If no other characters, output: []\n'
        'Output ONLY the JSON array. Nothing else.',
        [{'role': 'user', 'content': combined[:1200]}],
        max_tokens=80, temp=0.0
    )
    try:
        code_block = re.search(r'```(?:\w+)?\s*([\s\S]*?)```', result)
        clean  = code_block.group(1).strip() if code_block else result.strip()
        start  = clean.find('[')
        end    = clean.rfind(']') + 1
        names  = json.loads(clean[start:end]) if start != -1 and end > start else []
    except Exception:
        names = []

    existing = {e.name.lower() for e in entities}

    for raw_name in names:
        raw_name = raw_name.strip()
        if not raw_name or raw_name.lower() in existing:
            continue

        if _is_collective_group(raw_name, combined):
            role = _infer_collective_role(raw_name, combined)
            npc  = Entity(
                name=raw_name, pronouns='they/them', role=role,
                is_collective=True,
            )
            entities.append(npc)
            existing.add(raw_name.lower())
            logger.info(f'NPC {raw_name} (they/them) — collective: {role[:80]}')
        else:
            is_role = _is_role_descriptor(raw_name, combined)
            if is_role:
                name = _generate_npc_name(raw_name, combined)
                # Guard: generated name must not collide with an already-known entity.
                if name.lower() in existing:
                    logger.info(f'Skipping role "{raw_name}" — generated name "{name}" matches known entity')
                    continue
                logger.info(f'NPC Role "{raw_name}" named "{name}" from context')
            else:
                name = raw_name

            pronouns = _extract_pronouns(
                raw_name if is_role else name,
                combined
            )
            npc = Entity(
                name=name,
                pronouns=pronouns,
                role=raw_name if is_role else '',
            )
            entities.append(npc)
            existing.add(name.lower())
            logger.info(f'NPC {name} ({pronouns}) — {raw_name}')


def _extract_pronouns(search_term, context):
    # Strip common articles/possessives to derive the bare role noun.
    base = search_term.lower()
    for article in ('my ', 'his ', 'her ', 'your ', 'our ', 'the ', 'a ', 'an '):
        if base.startswith(article):
            base = base[len(article):]
            break
    base = re.sub(r"^\w+'s\s+", '', base)  # strip leading possessive (e.g. "Elena's friend")

    variations = f'"{search_term}", "my {base}", "his {base}", "her {base}", "the {base}"'
    result, _  = call(
        f'Determine the pronouns for a character referred to as: {variations}\n\n'
        'Reason in this order:\n'
        '  1. The role or relationship title itself — e.g. "wife" implies she/her,\n'
        '     "butler" implies he/him, "second wife" implies she/her.\n'
        '     Any modifier that overrides the noun (e.g. "male wife") takes precedence.\n'
        '  2. Explicit pronouns or gendered language for this character in the text.\n'
        '  3. Cultural and historical context of the setting.\n\n'
        'Output ONLY one of:  he/him  |  she/her  |  they/them\n'
        'Use they/them ONLY when the character is explicitly non-binary or gender cannot\n'
        'be determined from the title, context, or setting — not as a default.',
        [{'role': 'user', 'content': f'Text:\n{context[:1200]}'}],
        max_tokens=8, temp=0.0
    )
    r = result.strip().lower()
    if 'she' in r: return 'she/her'
    if 'he'  in r: return 'he/him'
    return 'they/them'


def _extract_character_sentences(name, context):
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', context) if s.strip()]
    relevant  = [s for s in sentences if name.lower() in s.lower()]
    if not relevant:
        return f'[Note: {name} is mentioned but not described directly. Infer from role only.]'
    return ' '.join(relevant[:8])


def instantiate_npc(name, context, entities, original_role=None):
    pronoun_search = original_role if original_role else name
    pronouns       = _extract_pronouns(pronoun_search, context)

    if original_role:
        role       = original_role
        appearance = ''
        mood       = 'neutral'
    else:
        scoped = _extract_character_sentences(name, context)
        extraction, _ = call(
            f'What is the role of "{name}" in this scene?\n'
            'One sentence. Be specific.',
            [{'role': 'user', 'content': scoped}],
            max_tokens=40, temp=0.2
        )
        role       = extraction.strip()
        appearance = ''
        mood       = 'neutral'

    npc = Entity(
        name=name, pronouns=pronouns, role=role,
        appearance=appearance, mood=mood,
    )
    entities.append(npc)
    logger.info(f'NPC Instantiated {name} ({pronouns}) — {role[:60]}')
    return npc


# ── Preprocess ────────────────────────────────────────────────

def preprocess(raw, persona, entities, last_target=None):
    names    = {e.name.lower(): e for e in entities if not e.is_player}
    actions  = re.findall(r'\*\*(.+?)\*\*', raw)
    spoken   = re.findall(r'"(.+?)"', raw)
    thoughts = re.findall(
        r'\[[Tt]o (?:Herself|Himself|Themselves|[Mm]yself)\]\s*(.+?)(?=\[|\*\*|"|$)',
        raw, re.DOTALL
    )
    thoughts = [t.strip() for t in thoughts if t.strip()]

    target    = None
    raw_lower = raw.lower()
    for name, entity in names.items():
        if re.search(rf'\b{re.escape(name)}[,.]?\s*$', raw_lower):
            target = entity.name; break
        if spoken and any(name in s.lower() for s in spoken):
            target = entity.name; break
        if any(name in a.lower() for a in actions):
            target = entity.name; break
    if not target:
        target = last_target or (entities[1].name if len(entities) > 1 else None)

    same_space_risk = any(
        e.pronouns == persona.pronouns and e.present and not e.is_player
        for e in entities
    )

    clean = re.sub(
        r'\[[Tt]o (?:Herself|Himself|Themselves|[Mm]yself)\]', '', raw
    ).strip()

    annotated = (
        f'[SOURCE=PLAYER:{persona.name} — every action/word in this turn belongs to {persona.name} exclusively]'
        f'{", target=" + target if target else ""}'
        f': {clean}'
    )

    return {
        'raw': raw, 'clean': clean, 'annotated': annotated,
        'target': target, 'actions': actions,
        'spoken': spoken, 'thoughts': thoughts,
        'same_space_risk': same_space_risk,
    }


# ── System prompt ─────────────────────────────────────────────

def build_system(persona, entities, world, memory, layer1,
                 same_space_risk=False, scene_lang='English', content_filter=None):
    registry  = '\n\n'.join(e.block() for e in entities)
    reads     = []
    for e in entities:
        if not e.is_player and getattr(e, 'behavioral_read', ''):
            reads.append(f'  {e.name}: {e.behavioral_read}')
    behavioral_block = '\n'.join(reads) if reads else ''

    CHARACTER_INTEGRITY = ''
    for e in entities:
        if not e.is_player and e.never_does:
            CHARACTER_INTEGRITY += (
                f'\n  {e.name} — CHARACTER INTEGRITY:\n'
                f'    {e.never_does}\n'
                f'    Behavioral truth: {e.behavioral_read[:200] if e.behavioral_read else ""}\n'
            )

    disambiguation = ''
    if same_space_risk:
        risky       = [e for e in entities if e.pronouns == persona.pronouns and not e.is_player and e.present]
        risky_names = ', '.join(e.name for e in risky)
        disambiguation = (
            f'\nDISAMBIGUATION WARNING — same pronouns in scene:\n'
            f'  {persona.name} ({persona.pronouns}) = PLAYER. All input actions belong to them.\n'
            f'  {risky_names} ({persona.pronouns}) = NPC. Their actions come from your narration only.\n'
            f'  Any ambiguous pronoun in player input refers to {persona.name}.\n'
        )

    lang_sample   = get_lang_sample(scene_lang)
    filter_block  = content_filter.prompt_block() if content_filter else ''
    behavioral_section = (
        f'\n{"="*56}\nNARRATOR PRIOR — private character knowledge\n{"="*56}\n'
        f'{behavioral_block}\n'
    ) if behavioral_block else ''

    return f"""You are a skilled fiction narrator. You portray characters and narrate the world.
You NEVER speak, act, think, or feel as {persona.name}.
PLAYER IDENTITY: The player character in this session is {persona.name} ({persona.pronouns}). \
Refer to them by this name always — even if the opening scene uses a different name for them.

{"="*56}
LAYER 1 — GROUND TRUTH (never contradict)
{"="*56}
{layer1}

{"="*56}
LAYER 2 — WORLD STATE
{"="*56}
{world.block()}

{"="*56}
LAYER 3+4 — ENTITIES + STATES
{"="*56}
{registry}
{disambiguation}
{"="*56}
LAYER 5+7 — MEMORY
{"="*56}
{memory.block()}
{behavioral_section}
{"="*56}
CHARACTER INTEGRITY
{"="*56}
{CHARACTER_INTEGRITY}
{"="*56}
NARRATIVE CONTRACT
{"="*56}
You write in the tradition of literary fiction — where what characters
don't say matters as much as what they do. Subtext is not decoration.
Characters are as perceptive as their nature demands.
Tension is never resolved before it has been felt.

INPUT FORMAT:
  [SOURCE=PLAYER:{persona.name}] = everything in this turn belongs to {persona.name}.
  **text**  = {persona.name}'s physical action
  "text"    = {persona.name} speaks aloud
  plain     = ambient detail
  (internal context only...) = {persona.name}'s thought. Inform writing. Never surface it.

OUTPUT FORMAT:
  **text** for narrated action. "text" for dialogue, speaker always clear.
  Plain for atmosphere. Never second person for {persona.name}.
  Write in {scene_lang}. Match this register: "{lang_sample}"

CAMERA:
  {persona.name} is the lens. Every scene renders through their presence.
  NPC inner life surfaces only as behavior — expression, silence, movement, words.
  Never narrate what another character thinks or feels directly.

SOURCE LOCK:
  Actions tagged [SOURCE=PLAYER:{persona.name}] cannot be reassigned to any other character.
  If {persona.name} performs an action, it happened. Respond to it. Do not undo it.

{filter_block}"""


# ── Sovereignty check ─────────────────────────────────────────

def check_sovereignty(response: str, persona_name: str, entities: list = None) -> dict:
    """LLM judgment: did the narrator move, feel, think, or decide for the player?"""
    npc_names = [
        e.name for e in (entities or [])
        if not getattr(e, 'is_player', False) and not getattr(e, 'is_collective', False)
    ]
    npc_line = (
        f'NPCs in this scene (emotions/actions/thoughts assigned to them are fine): '
        f'{", ".join(npc_names)}\n'
        if npc_names else ''
    )
    # Strip bold/italic markers — **text** in narrator output is stage direction,
    # not a player-authored action tag; the LLM should not treat it differently.
    plain_response = re.sub(r'\*+', '', response)

    try:
        result, _ = call(
            'You are a strict player-sovereignty validator for interactive fiction. '
            'The player character is the sole "camera" — their body, feelings, thoughts, '
            'and decisions belong exclusively to the human player, not the narrator. '
            'A sovereignty violation occurs ONLY when the narrator directly controls the player by:\n'
            '  1. Causing the player character to physically move or act\n'
            '  2. Assigning feelings, emotions, or bodily sensations to the player character\n'
            '  3. Narrating the player character\'s thoughts, decisions, or intentions\n'
            'This applies regardless of how the player is referenced. '
            'Violations involving NPCs are NOT player sovereignty violations. '
            'Output ONLY valid JSON: {"clean": true, "violations": []}',
            [{'role': 'user', 'content': (
                f'Player character: {persona_name}\n'
                f'{npc_line}'
                f'\nNarrator response:\n{plain_response}'
            )}],
            max_tokens=150,
            temp=0.0,
        )
        code_block = re.search(r'```(?:\w+)?\s*([\s\S]*?)```', result)
        cleaned = code_block.group(1).strip() if code_block else result.strip()
        start = cleaned.find('{')
        end   = cleaned.rfind('}') + 1
        data  = json.loads(cleaned[start:end])
        violations = [str(v) for v in data.get('violations', [])]
        is_clean   = bool(data.get('clean', True))
        return {'clean': is_clean, 'violations': violations, 'count': len(violations)}
    except Exception as e:
        logger.warning('Sovereignty check failed, assuming clean: %s', e)
        return {'clean': True, 'violations': [], 'count': 0}


def _parse_guard_verdict(raw):
    code_block = re.search(r'```(?:\w+)?\s*([\s\S]*?)```', raw)
    text = code_block.group(1).strip() if code_block else raw.strip()
    start = text.find('{')
    end = text.rfind('}') + 1
    if start == -1 or end <= start:
        raise ValueError('validator did not return a JSON object')
    data = json.loads(text[start:end])
    return {
        'valid': bool(data.get('valid', False)),
        'violations': [str(v) for v in data.get('violations', [])],
    }


def _same_raw_response(a, b):
    return ' '.join((a or '').split()).lower() == ' '.join((b or '').split()).lower()


def guard_response(response, player_input, persona, entities, world, memory, layer1,
                   scene_lang='English', previous_response=''):
    """Validate and, once, repair an assistant response before it is saved or shown."""
    pinned = '\n'.join(f'- {fact}' for fact in memory.pinned) or '- none'
    entity_block = '\n\n'.join(e.block() for e in entities)
    integrity = '\n'.join(
        f'- {e.name}: {e.never_does} {e.behavioral_read[:300]}'
        for e in entities
        if not e.is_player and (e.never_does or e.behavioral_read)
    ) or '- none'

    validator_system = (
        'You are a strict continuity and safety validator for an interactive fiction engine. '
        'Check whether the assistant draft violates any pinned fact, world state, character '
        'integrity rule, physical possibility, knowledge boundary, or player agency rule. '
        'A character must not reveal knowledge they do not have, perform impossible actions, '
        'or directly do the one thing their integrity rule says they would never do. '
        'The narrator must never move, feel, think, decide, or speak for the player. '
        'Return ONLY valid JSON with keys: valid (boolean), violations (array of short strings).'
    )
    validator_payload = (
        f'PLAYER: {persona.name} ({persona.pronouns})\n'
        f'SCENE LANGUAGE: {scene_lang}\n\n'
        f'LAYER 1 GROUND TRUTH:\n{layer1}\n\n'
        f'WORLD STATE:\n{world.block()}\n\n'
        f'PINNED FACTS:\n{pinned}\n\n'
        f'ENTITIES:\n{entity_block}\n\n'
        f'CHARACTER INTEGRITY:\n{integrity}\n\n'
        f'PLAYER INPUT:\n{player_input}\n\n'
        f'ASSISTANT DRAFT:\n{response}'
    )

    violations = []
    sv = check_sovereignty(response, persona.name, entities)
    if not sv['clean']:
        violations.append(f'player agency violation: {sv["violations"]}')
    try:
        verdict_raw, _ = call(
            validator_system,
            [{'role': 'user', 'content': validator_payload}],
            max_tokens=400,
            temp=0.0,
            retries=2,
        )
        verdict = _parse_guard_verdict(verdict_raw)
        if not verdict['valid']:
            violations.extend(verdict['violations'])
    except Exception as e:
        logger.warning('Continuity validator failed closed: %s', e)
        violations.append('continuity validator could not produce a valid verdict')

    if not violations:
        return response, {'revised': False, 'violations': []}

    repair_system = (
        'Rewrite the assistant draft so it fully obeys the scene canon, pinned facts, '
        'character integrity, physical possibility, knowledge boundaries, and player agency. '
        'Preserve the useful dramatic beat, but refuse impossible or out-of-character requests '
        'through in-character behavior. Output ONLY the corrected in-scene fiction response. '
        'Do not explain the revision. Do not mention rules, canon, pinned facts, violations, '
        'or why the response is valid. Do not use bullet points or checklist language.'
    )
    repair_payload = (
        'Violations to fix:\n- ' + '\n- '.join(violations) + '\n\n'
        f'Canon and constraints:\n{validator_payload}\n\n'
        f'Corrected response in {scene_lang}:'
    )
    revised = None
    try:
        revised, _ = call(
            repair_system,
            [{'role': 'user', 'content': repair_payload}],
            max_tokens=700,
            temp=0.25,
            retries=2,
        )
        revised = revised.strip()
        revised_payload = validator_payload.replace(
            f'ASSISTANT DRAFT:\n{response}',
            f'ASSISTANT DRAFT:\n{revised}',
        )
        revised_sv = check_sovereignty(revised, persona.name, entities)
        revised_violations = [f'player agency violation: {revised_sv["violations"]}'] if not revised_sv['clean'] else []
        try:
            revised_raw, _ = call(
                validator_system,
                [{'role': 'user', 'content': revised_payload}],
                max_tokens=400,
                temp=0.0,
                retries=2,
            )
            revised_verdict = _parse_guard_verdict(revised_raw)
            if not revised_verdict['valid']:
                revised_violations.extend(revised_verdict['violations'])
        except Exception as e:
            logger.warning('Continuity repair validation failed; accepting LLM repair imperfectly: %s', e)

        if revised_violations:
            logger.warning('Continuity repair may still be imperfect: %s', revised_violations)
            fallback_system = (
                'Write a minimal in-character fallback response for an interactive fiction scene. '
                'It must avoid every disputed fact or action, refuse the unsafe premise through '
                'character behavior, preserve player agency, and stay physically possible. '
                'Output ONLY in-scene fiction. Do not explain rules, canon, validation, or safety. '
                'Do not use bullet points. The response must be novel: do not repeat the previous '
                'assistant response verbatim, and do not use the same opening sentence.'
            )
            fallback_payload = (
                'Violations still present:\n- ' + '\n- '.join(revised_violations) + '\n\n'
                f'Canon and constraints:\n{validator_payload}\n\n'
                f'Previous assistant response to avoid repeating:\n{previous_response or "(none)"}\n\n'
                f'Novel fallback response in {scene_lang}:'
            )
            try:
                fallback, _ = call(
                    fallback_system,
                    [{'role': 'user', 'content': fallback_payload}],
                    max_tokens=260,
                    temp=0.65,
                    retries=2,
                )
                fallback = fallback.strip()
                if previous_response and _same_raw_response(fallback, previous_response):
                    try:
                        fallback_retry, _ = call(
                            fallback_system + ' The last attempt exactly repeated the previous response. Produce a different response now.',
                            [{'role': 'user', 'content': fallback_payload}],
                            max_tokens=260,
                            temp=0.9,
                            retries=2,
                        )
                        fallback = fallback_retry.strip() or fallback
                    except Exception:
                        pass
                if previous_response and _same_raw_response(fallback, previous_response):
                    fallback = f'A quiet pause settles before the refusal changes shape. {fallback}'
            except Exception as e:
                logger.warning('Continuity fallback call failed: %s', e)
                fallback = ''
            if fallback:
                return fallback, {'revised': True, 'fallback': True, 'violations': violations + revised_violations}
        return revised or response, {'revised': True, 'violations': violations}
    except Exception as e:
        logger.warning('Continuity repair failed; using safe refusal: %s', e)
        safe = revised or 'A tense silence holds. Nothing moves.'
        return safe, {'revised': True, 'violations': violations}


# ── Prompt injection sanitization ─────────────────────────────

def sanitize_input(text: str) -> str:
    """Strip common prompt injection patterns from player input."""
    text = unicodedata.normalize('NFKC', text)
    patterns = [
        r'(?i)ignore\s+(all\s+)?(previous|prior)\s+(instructions?|prompts?|context)',
        r'(?i)you\s+are\s+now\s+a',
        r'(?i)forget\s+(everything|all|your)\s+(instructions?|rules|training)',
        r'(?i)system\s*:\s*',
        r'(?i)assistant\s*:\s*',
        r'(?i)disregard\s+(all\s+)?(previous|prior|your)',
        r'(?i)new\s+instructions?\s*:',
        r'(?i)override\s+(the\s+)?(system|instructions?|rules)',
        r'(?i)\[INST\]',
        r'(?i)<\|im_start\|>',
        r'(?i)<\|system\|>',
    ]
    for pattern in patterns:
        text = re.sub(pattern, '[filtered]', text)
    return text.strip()


# ── Async memory helpers ──────────────────────────────────────

def summarize_async(chunk, persona_name, memory):
    def _run():
        try:
            chunk_text = '\n'.join(f'{m["role"].upper()}: {m["content"]}' for m in chunk)
            summary, _ = call(
                'Precise narrative summarizer. Output ONLY the summary. '
                'Preserve emotional texture, key events, relationship shifts, facts.',
                [{'role': 'user', 'content': f'Summarize in 3-4 sentences:\n\n{chunk_text}'}],
                max_tokens=200, temp=0.3
            )
            beats, _ = call(
                'Output only a comma-separated list of 3 key emotional facts. Nothing else.',
                [{'role': 'user', 'content': chunk_text}],
                max_tokens=60, temp=0.1
            )
            check, _ = call(
                'Answer YES or NO only.',
                [{'role': 'user', 'content': f'Does this summary preserve: {beats}?\n\nSummary: {summary}'}],
                max_tokens=5, temp=0.1
            )
            if 'no' in check.lower():
                summary, _ = call(
                    'Precise narrative summarizer. Output ONLY the summary.',
                    [{'role': 'user', 'content':
                        f'Summarize in 3-4 sentences. Must preserve: {beats}\n\n{chunk_text}'}],
                    max_tokens=200, temp=0.3
                )
            memory.summaries.append(summary)
            logger.info(f'Summary: {summary[:70]}...')
        except Exception as e:
            logger.warning('summarize_async failed: %s', e)
    threading.Thread(target=contextvars.copy_context().run, args=(_run,), daemon=True).start()


def extract_and_save_assumptions(response, persona, entities, lock=None):
    def _run():
        all_names = [e.name for e in entities]
        try:
            result, _ = call(
                'Extract character state changes from narrative text. '
                'Output JSON only: {"stable": {"name": ["detail"]}, "physical": {"name": "state"}}. '
                'Stable = appearance, clothing, permanent traits. '
                'Physical = injuries, blood, torn clothing, exhaustion. '
                'Only include characters with changes. Output valid JSON only.',
                [{'role': 'user', 'content': (
                    f'Characters: {", ".join(all_names)}\n\n'
                    f'Narrative:\n{response}'
                )}],
                max_tokens=150, temp=0.1
            )
            code_block = re.search(r'```(?:\w+)?\s*([\s\S]*?)```', result)
            clean = code_block.group(1).strip() if code_block else result.strip()
            data  = json.loads(clean)
            for e in entities:
                n = e.name
                if lock:
                    with lock:
                        if n in data.get('stable', {}):
                            for detail in data['stable'][n]:
                                if detail not in e.saved_assumptions:
                                    e.saved_assumptions.append(detail)
                        if n in data.get('physical', {}):
                            e.physical_state = data['physical'][n]
                else:
                    if n in data.get('stable', {}):
                        for detail in data['stable'][n]:
                            if detail not in e.saved_assumptions:
                                e.saved_assumptions.append(detail)
                    if n in data.get('physical', {}):
                        e.physical_state = data['physical'][n]
        except Exception:
            pass
    threading.Thread(target=contextvars.copy_context().run, args=(_run,), daemon=True).start()
