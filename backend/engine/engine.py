import re
import json
import threading
import logging
import contextvars
import concurrent.futures
from .types import (
    Entity, WorldState, Memory, ContentFilter, FilterState, Scenario
)
from .call import call, MAX_RAW_TURNS
from .inference import (
    detect_scene_language, detect_from_player_input,
    translate_input, translate_output,
    parse_opening_appearance, scan_opening_for_npcs,
    infer_characters, preprocess, build_system,
    check_sovereignty, summarize_chunk, extract_and_save_assumptions,
    sanitize_input, guard_response,
)

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, persona, main_char, world, memory, layer1, opening,
                 persona_highlights='', extras=None, scene_lang=None,
                 content_filter=None, sp_note='', char_personality=''):
        self.persona           = persona
        self.world             = world
        self.memory            = memory
        self.layer1            = layer1
        self.turn              = 0
        self._sp_note          = sp_note
        self._char_personality = char_personality
        self.last_target       = main_char.name
        self.entities          = [persona, main_char] + (extras or [])
        self.history           = [{'role': 'assistant', 'content': opening}]
        self.metrics           = {'turns': 0, 'sv_violations': 0, 'latencies': [], 'compressions': 0}
        self._lock             = threading.Lock()

        self.filter = content_filter or ContentFilter(state=FilterState.OFF)
        if self.filter.is_active:
            self.filter.activate(self.entities)

        self.scene_lang = scene_lang or detect_scene_language(opening)
        logger.info(f'Scene language: {self.scene_lang}')

        # display_history: clean messages shown to the user (no [SOURCE=PLAYER:...] annotations).
        # engine.history is the internal annotated history used for LLM context.
        self.display_history = [{'role': 'assistant', 'content': opening}]

        parse_opening_appearance(opening, persona, persona_highlights)

        # scan_opening_for_npcs and _infer_world are independent — run in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_scan = pool.submit(
                contextvars.copy_context().run, scan_opening_for_npcs,
                opening, layer1, self.entities, sp_note, self._char_personality,
            )
            f_world = pool.submit(contextvars.copy_context().run, self._infer_world, opening, layer1) \
                if not self.world.location else None
            f_scan.result()
            if f_world: f_world.result()

        infer_characters(self.entities, opening, layer1, sp_note=sp_note)
        self._pin_opening_facts(opening, layer1)
        self._print_init_report(opening)

    # ── Core turn ─────────────────────────────────────────────
    def step(self, raw_input, continue_narrative=False):
        """continue_narrative=True: no player input this turn — the player
        wants the narrator to keep going on its own (a "continue" button, not
        a scripted action). Deliberately NOT wrapped as [SOURCE=PLAYER:...] —
        that tag exists precisely to lock in that the player performed a
        specific action, which isn't true here; wrapping it that way would
        let the model attribute a phantom action to the player. Nothing is
        appended to display_history for this turn's "input" side — there is
        no player message to show as a chat bubble."""
        if not continue_narrative:
            raw_input = sanitize_input(raw_input)

        with self._lock:
            self.turn += 1
            self.metrics['turns'] += 1

            if continue_narrative:
                parsed = {
                    'clean': '(no player input — narrator continues the scene)',
                    'target': self.last_target, 'actions': [], 'spoken': [], 'thoughts': [],
                    'same_space_risk': any(
                        e.pronouns == self.persona.pronouns and e.present and not e.is_player
                        for e in self.entities
                    ),
                }
                msg = (
                    '[SYSTEM: The player takes no action this turn. Continue the scene '
                    'naturally — advance time, deepen the moment, let other characters or '
                    f'the environment act. Do not attribute any new action, decision, or '
                    f'dialogue to {self.persona.name}; they remain exactly as they were '
                    'left at the end of the previous turn.]'
                )
            else:
                self.scene_lang  = detect_from_player_input(raw_input, self.scene_lang)
                english_input    = translate_input(raw_input, self.scene_lang)
                parsed           = preprocess(english_input, self.persona, self.entities, self.last_target)
                msg = parsed['annotated']
                if parsed['thoughts']:
                    msg += '\n(internal context only, do not reference: ' + ' | '.join(parsed['thoughts']) + ')'
            self.last_target = parsed['target']

            system = build_system(
                self.persona, self.entities, self.world,
                self.memory, self.layer1,
                same_space_risk=parsed['same_space_risk'],
                scene_lang=self.scene_lang,
                content_filter=self.filter,
            )

            self.history.append({'role': 'user', 'content': msg})
            if not continue_narrative:
                self.display_history.append({'role': 'user', 'content': parsed['clean']})
            hot = self.history[-MAX_RAW_TURNS:]
            previous_response = next(
                (m['content'] for m in reversed(self.display_history[:-1]) if m.get('role') == 'assistant'),
                '',
            )

            response_en, latency = call(system, hot)
            response             = translate_output(response_en, self.scene_lang)
            self.metrics['latencies'].append(latency)
            response, guard = guard_response(
                response, parsed['clean'], self.persona, self.entities,
                self.world, self.memory, self.layer1, self.scene_lang,
                previous_response=previous_response,
            )
            if guard.get('revised'):
                logger.warning('Continuity guard revised response: %s', guard.get('violations'))

            sv = check_sovereignty(response, self.persona.name, self.entities)
            if not sv['clean']:
                self.metrics['sv_violations'] += sv['count']
                logger.warning('Sovereignty violation leaked through guard (hard block): %s', sv['violations'])
                response = 'A tense silence holds. Nothing moves.'

            self.history.append({'role': 'assistant', 'content': response})
            self.display_history.append({'role': 'assistant', 'content': response})

            chunk_to_summarize = None
            if len(self.history) > MAX_RAW_TURNS:
                chunk_to_summarize = self.history[:-MAX_RAW_TURNS]
                self.history = self.history[-MAX_RAW_TURNS:]
                self.metrics['compressions'] += 1

            turn_result = {
                'turn': self.turn, 'response': response,
                'latency': latency, 'sovereign': sv['clean'],
                'violations': sv['violations'], 'parsed': parsed,
            }

        # Outside self._lock (it's not reentrant): both calls below are
        # synchronous and must complete before step() returns, so their
        # mutations exist before the caller's serialize_engine() runs.
        # Previously fire-and-forget on daemon threads, which meant they
        # almost always lost the race against serialization and were
        # silently discarded — see summarize_chunk / extract_and_save_assumptions.
        extract_and_save_assumptions(response, self.persona, self.entities, lock=self._lock)
        if chunk_to_summarize is not None:
            summarize_chunk(chunk_to_summarize, self.persona.name, self.memory)

        return turn_result

    def regenerate(self):
        """Discard the last turn's response and generate a fresh one for the
        SAME player input — a single bad generation doesn't have to mean the
        whole turn was wrong; try again and see (the SpicyChat-style "redo"
        pattern). Same turn number, not a new turn: self.turn/metrics['turns']
        are untouched, and history/display_history end up the same length
        they started at (pop one assistant reply, append one back)."""
        with self._lock:
            if not self.history or self.history[-1]['role'] != 'assistant':
                raise ValueError('No turn to regenerate — nothing has been said yet')
            self.history.pop()
            if self.display_history and self.display_history[-1]['role'] == 'assistant':
                self.display_history.pop()

            if not self.history or self.history[-1]['role'] != 'user':
                raise ValueError('No player turn found to regenerate')

            # A continuation turn (see step()) never added a user-role entry to
            # display_history — its absence at the top, post-pop, is the signal.
            is_continuation = not (self.display_history and self.display_history[-1]['role'] == 'user')
            player_input_for_guard = (
                '(no player input — narrator continues the scene)' if is_continuation
                else self.display_history[-1]['content']
            )

            same_space_risk = any(
                e.pronouns == self.persona.pronouns and e.present and not e.is_player
                for e in self.entities
            )
            system = build_system(
                self.persona, self.entities, self.world,
                self.memory, self.layer1,
                same_space_risk=same_space_risk,
                scene_lang=self.scene_lang,
                content_filter=self.filter,
            )
            hot = self.history[-MAX_RAW_TURNS:]
            previous_response = next(
                (m['content'] for m in reversed(self.display_history) if m.get('role') == 'assistant'),
                '',
            )

            response_en, latency = call(system, hot)
            response             = translate_output(response_en, self.scene_lang)
            self.metrics['latencies'].append(latency)
            response, guard = guard_response(
                response, player_input_for_guard, self.persona, self.entities,
                self.world, self.memory, self.layer1, self.scene_lang,
                previous_response=previous_response,
            )
            if guard.get('revised'):
                logger.warning('Continuity guard revised regenerated response: %s', guard.get('violations'))

            sv = check_sovereignty(response, self.persona.name, self.entities)
            if not sv['clean']:
                self.metrics['sv_violations'] += sv['count']
                logger.warning('Sovereignty violation leaked through guard (hard block, regenerate): %s', sv['violations'])
                response = 'A tense silence holds. Nothing moves.'

            self.history.append({'role': 'assistant', 'content': response})
            self.display_history.append({'role': 'assistant', 'content': response})

            turn_result = {
                'turn': self.turn, 'response': response,
                'latency': latency, 'sovereign': sv['clean'],
                'violations': sv['violations'],
            }

        extract_and_save_assumptions(response, self.persona, self.entities, lock=self._lock)
        return turn_result

    # ── Helpers ───────────────────────────────────────────────
    def pin(self, fact):
        self.memory.pinned.append(fact)

    def spawn_from_collective(self, collective_name, individual_name=None):
        collective = next(
            (e for e in self.entities
             if e.name.lower() == collective_name.lower() and e.is_collective),
            None,
        )
        if not collective:
            return None
        if not individual_name:
            result, _ = call(
                'Generate a name for one member of this group. Output ONLY a first name.',
                [{'role': 'user', 'content':
                    f'Group: {collective_name}\nScene context: {self.layer1[:300]}'}],
                max_tokens=8, temp=0.7,
            )
            individual_name = result.strip().split()[0].strip('.,!?\'"')
        from .inference import instantiate_npc
        ctx = f'{self.layer1}\n\n{self.world.block()}'
        npc = instantiate_npc(individual_name, ctx, self.entities)
        npc.role = f'Member of {collective_name}. {collective.role}'
        collective.collective_of.append(individual_name)
        return npc

    def add_npc(self, name):
        from .inference import instantiate_npc
        ctx = ' '.join(m['content'] for m in self.history[-6:])
        return instantiate_npc(name, ctx, self.entities)

    def _infer_world(self, opening, layer1):
        prompt  = (
            'Extract world state as JSON with exactly these keys: '
            'location, time_of_day, era, atmosphere, flags. '
            'flags: 3-5 words naming the specific power and moral texture of this scene. '
            'Name what is actually happening — cover-up, coercion, grief-weaponized, etc. '
            'Output ONLY valid JSON. No prose. No markdown.'
        )
        content = layer1 + '\n\n' + opening
        for attempt in range(2):
            result, _ = call(prompt, [{'role': 'user', 'content': content}], max_tokens=150, temp=0.1)
            try:
                code_block = re.search(r'```(?:\w+)?\s*([\s\S]*?)```', result)
                clean = code_block.group(1).strip() if code_block else result.strip()
                start = clean.find('{')
                end   = clean.rfind('}') + 1
                data  = json.loads(clean[start:end])
                self.world.location    = data.get('location', '')
                self.world.time_of_day = data.get('time_of_day', '')
                self.world.era         = data.get('era', '')
                self.world.atmosphere  = data.get('atmosphere', '')
                self.world.flags       = data.get('flags', [])
                logger.info(f'World: {self.world.location} | {self.world.time_of_day} | {self.world.era}')
                logger.info(f'World flags: {self.world.flags}')
                break
            except Exception as e:
                if attempt == 1:
                    logger.error(f'World inference failed: {e}')

    def _pin_opening_facts(self, opening, layer1):
        sp_note = getattr(self, '_sp_note', '')
        result, _ = call(
            f'{sp_note}'
            'Extract the most important facts from this scene as a JSON array of strings.\n'
            'Facts must reflect what characters actually do and intend.\n'
            'Each fact is one clear sentence. Focus on: relationships, past events, '
            'character motivations, secrets, power dynamics.\n'
            'Maximum 8 facts. Most important first.\n'
            'Output ONLY a JSON array. No markdown.',
            [{'role': 'user', 'content':
                f'Layer 1 (background):\n{layer1}\n\nOpening scene:\n{opening}'}],
            max_tokens=300, temp=0.1,
        )
        try:
            code_block = re.search(r'```(?:\w+)?\s*([\s\S]*?)```', result)
            clean = code_block.group(1).strip() if code_block else result.strip()
            start = clean.find('[')
            end   = clean.rfind(']') + 1
            if start != -1 and end > start:
                facts = json.loads(clean[start:end])
                if isinstance(facts, list):
                    self.memory.pinned.extend([f for f in facts if isinstance(f, str)])
                    logger.info(f'{len(self.memory.pinned)} facts pinned from opening')
        except Exception as e:
            logger.error(f'Fact extraction failed: {e}')

    def _print_init_report(self, opening):
        W = 62
        logger.info('=' * W)
        logger.info('  INIT REPORT — what the engine inferred')
        logger.info('=' * W)
        logger.info(f'  Language     : {self.scene_lang}')
        logger.info(f'  Filter       : {self.filter.state.value}')
        logger.info('  WORLD STATE')
        logger.info(f'  Location     : {self.world.location}')
        logger.info(f'  Time         : {self.world.time_of_day}')
        logger.info(f'  Era          : {self.world.era}')
        logger.info(f'  Atmosphere   : {self.world.atmosphere}')
        logger.info(f'  Flags        : {self.world.flags}')
        logger.info('  ENTITIES')
        for e in self.entities:
            tag = '[PLAYER]' if e.is_player else '[NPC]'
            logger.info(f'  {tag} {e.name} ({e.pronouns})')
            if e.role:            logger.info(f'    Role         : {e.role[:120]}')
            if e.appearance:      logger.info(f'    Appearance   : {e.appearance[:120]}')
            if e.mood:            logger.info(f'    Mood         : {e.mood}')
            if e.behavioral_read: logger.info(f'    Behavioral   : {e.behavioral_read[:150]}')
            if e.never_does:      logger.info(f'    NEVER        : {e.never_does}  (resolve {e.integrity_resolve * 100:.0f}%)')
        logger.info('  MEMORY')
        for f in self.memory.pinned:
            logger.info(f'  PIN: {f}')
        logger.info('  OPENING')
        logger.info('-' * W)
        for line in opening.strip().split('\n'):
            logger.info(f'  {line}')
        logger.info('=' * W)


# ── scenario_to_engine ────────────────────────────────────────

def _is_second_person(text):
    words = text.lower().split()
    return (words.count('you') + words.count('your') + words.count('yourself')) >= 2


def _second_person_note(player_name, player_pronouns):
    return (
        f'IMPORTANT: In the narrative passages of this scene, '
        f'"you" and "your" refer to {player_name} ({player_pronouns}). '
        f'In dialogue (inside quotes), "you" refers to whoever the speaker is addressing '
        f'and must NOT be interpreted as {player_name} unless context makes it clear.\n\n'
    )


def scenario_to_engine(scenario: Scenario) -> Engine:
    sp_note = ''
    if _is_second_person(scenario.greeting):
        sp_note = _second_person_note(scenario.player_name, scenario.player_pronouns)
        logger.info('Second-person opening detected')

    # Sanitize inputs
    scenario.char_personality = sanitize_input(scenario.char_personality)
    scenario.greeting = sanitize_input(scenario.greeting)

    # layer1 and highlights are independent — run in parallel
    def _infer_layer1():
        result, _ = call(
            f'{sp_note}'
            'Extract a scene context — 4 to 6 sentences — using ONLY what is explicitly stated. '
            'Do not invent or assume anything not in the text.\n'
            'Cover:\n'
            '  - The relationship between the characters and its history\n'
            '  - What happened before this scene (backstory stated in the text)\n'
            '  - The power dynamic between characters\n'
            '  - What each character wants in this moment\n'
            '  - Any secrets or conflicts established in the text\n'
            'Do not reference the character personality description — only the opening scene text.',
            [{'role': 'user', 'content':
                f'Character: {scenario.char_name} ({scenario.char_pronouns}) — {scenario.char_title}\n'
                f'Personality: {scenario.char_personality}\n\n'
                f'Opening scene:\n{scenario.greeting}'}],
            max_tokens=250, temp=0.2,
        )
        return result

    def _infer_highlights():
        result, _ = call(
            f'{sp_note}'
            f'Describe the physical appearance of {scenario.player_name} ({scenario.player_pronouns}) '
            f'based on what the opening scene states or implies.\n'
            f'Cover: height, build, hair, face, clothing, emotional state visible on the body.\n'
            f'2-3 sentences. Concrete and specific.',
            [{'role': 'user', 'content': scenario.greeting}],
            max_tokens=200, temp=0.2,
        )
        return result

    logger.info('Inferring layer1 + highlights in parallel...')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_layer1     = pool.submit(contextvars.copy_context().run, _infer_layer1)
        f_highlights = pool.submit(contextvars.copy_context().run, _infer_highlights)
        layer1     = f_layer1.result()
        highlights = f_highlights.result()
    logger.info(f'Layer1: {layer1[:80]}...')
    logger.info(f'Highlights: {highlights[:100]}...')

    persona = Entity(
        name=scenario.player_name,
        pronouns=scenario.player_pronouns,
        role='',
        is_player=True,
    )
    main_char = Entity(
        name=scenario.char_name,
        pronouns=scenario.char_pronouns,
        role=scenario.char_title,
    )
    world  = WorldState()
    memory = Memory()

    return Engine(
        persona=persona,
        main_char=main_char,
        world=world,
        memory=memory,
        layer1=layer1,
        opening=scenario.greeting,
        persona_highlights=highlights,
        scene_lang=scenario.scene_lang,
        content_filter=scenario.content_filter,
        sp_note=sp_note,
        char_personality=scenario.char_personality,
    )
