"""Tests for Engine.step(continue_narrative=True) — the "let the AI continue
without player input" mode. Constructs a minimal Engine via __new__ (same
technique serializer.deserialize_engine uses) rather than running the full
LLM-heavy __init__, then mocks call() for the turn itself.

call() has to be patched in two places: engine.engine imports it directly
(`from .call import call`) for the main response, while guard_response /
check_sovereignty / extract_and_save_assumptions are defined in
engine.inference and use their own module's bound name — same underlying
function, two separate references, both need patching to the same mock so
one ordered side_effect list plays out correctly across both."""
import threading
from unittest.mock import patch, MagicMock

from engine.engine import Engine
from engine.types import Entity, WorldState, Memory, ContentFilter, FilterState

SOVEREIGN = '{"clean": true, "violations": []}'
VALID     = '{"valid": true, "violations": []}'
EXTRACT   = '{"stable": {}, "physical": {}}'


def _build_engine():
    persona = Entity(name='Alex', pronouns='they/them', is_player=True)
    npc     = Entity(name='Iris', pronouns='she/her', role='Archivist')
    e = Engine.__new__(Engine)
    e.persona = persona
    e.world = WorldState(location='Archive', time_of_day='night', era='modern', atmosphere='tense')
    e.memory = Memory()
    e.layer1 = 'A sealed archive at night.'
    e.turn = 0
    e._sp_note = ''
    e._char_personality = ''
    e.last_target = npc.name
    e.entities = [persona, npc]
    e.history = [{'role': 'assistant', 'content': 'Iris waits at the door.'}]
    e.display_history = [{'role': 'assistant', 'content': 'Iris waits at the door.'}]
    e.metrics = {'turns': 0, 'sv_violations': 0, 'latencies': [], 'compressions': 0}
    e._lock = threading.Lock()
    e.filter = ContentFilter(state=FilterState.OFF)
    e.scene_lang = 'English'  # skips translate_input/output's LLM calls entirely
    return e


def _step(e, raw_input, continue_narrative, main_response):
    """English scene_lang means no translation calls. Sequence: main
    response, guard's sovereignty check, guard's continuity validator,
    step's own hard-block sovereignty check, then extract_and_save_assumptions."""
    shared = MagicMock(side_effect=[
        (main_response, 0.5),  # main response
        (SOVEREIGN, 0.1),      # guard_response -> check_sovereignty
        (VALID, 0.1),          # guard_response -> continuity validator
        (SOVEREIGN, 0.1),      # step's own hard-block check_sovereignty
        (EXTRACT, 0.1),        # extract_and_save_assumptions
    ])
    with patch('engine.engine.call', shared), patch('engine.inference.call', shared):
        return e.step(raw_input, continue_narrative=continue_narrative)


class TestContinueNarrative:
    def test_continuation_not_wrapped_as_source_player(self):
        e = _build_engine()
        _step(e, '', True, 'Iris turns the page slowly, saying nothing.')

        last_user_msg = [m for m in e.history if m['role'] == 'user'][-1]
        assert '[SOURCE=PLAYER:' not in last_user_msg['content']
        assert '[SYSTEM:' in last_user_msg['content']

    def test_continuation_does_not_add_fake_user_bubble(self):
        """display_history is what the frontend renders — no player input
        happened, so no user-role message should appear there for this turn."""
        e = _build_engine()
        _step(e, '', True, 'Iris turns the page slowly, saying nothing.')

        user_messages = [m for m in e.display_history if m['role'] == 'user']
        assert user_messages == []

    def test_continuation_still_appends_assistant_response(self):
        e = _build_engine()
        result = _step(e, '', True, 'Iris turns the page slowly, saying nothing.')

        assert e.display_history[-1] == {'role': 'assistant', 'content': result['response']}
        assert e.turn == 1

    def test_continuation_preserves_last_target(self):
        e = _build_engine()
        _step(e, '', True, 'Iris turns the page slowly, saying nothing.')

        assert e.last_target == 'Iris'

    def test_continuation_passes_actual_previous_response_to_guard(self):
        """Regression: previous_response used to be computed from
        display_history[:-1], which is only correct on the normal-turn path
        (where a user message was just appended and needs skipping).
        continue_narrative never appends a user message, so the slice
        stripped off the real previous response instead and guard_response's
        anti-repeat check silently compared against the wrong (or no) turn."""
        e = _build_engine()
        shared = MagicMock(side_effect=[
            ('Iris turns the page slowly, saying nothing.', 0.5),  # main response
            (SOVEREIGN, 0.1),  # step's own hard-block check_sovereignty
            (EXTRACT, 0.1),    # extract_and_save_assumptions
        ])
        with patch('engine.engine.call', shared), patch('engine.inference.call', shared), \
             patch('engine.engine.guard_response') as mock_guard:
            mock_guard.return_value = (
                'Iris turns the page slowly, saying nothing.', {'revised': False, 'violations': []}
            )
            e.step('', continue_narrative=True)

        assert mock_guard.call_args.kwargs['previous_response'] == 'Iris waits at the door.'

    def test_normal_turn_still_wraps_as_source_player(self):
        """Sanity check the existing path is unchanged by the new branch."""
        e = _build_engine()
        _step(e, 'I knock on the door.', False, 'Iris looks up, startled.')

        last_user_msg = [m for m in e.history if m['role'] == 'user'][-1]
        assert '[SOURCE=PLAYER:Alex' in last_user_msg['content']
        user_display = [m for m in e.display_history if m['role'] == 'user']
        assert len(user_display) == 1
        assert user_display[0]['content'] == 'I knock on the door.'
