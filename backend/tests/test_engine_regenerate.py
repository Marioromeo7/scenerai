"""Tests for Engine.regenerate() — the "try again, that output was probably
a fluke" button (SpicyChat-style). Discards the last response and generates
a fresh one for the same input, same turn number. Same __new__-construction
technique as test_engine_step_continue.py, same dual call() patch target
(engine.engine.call for the main response, engine.inference.call for
guard_response/check_sovereignty/extract_and_save_assumptions internals)."""
import threading
from unittest.mock import patch, MagicMock

import pytest

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
    e.scene_lang = 'English'
    return e


def _mock_calls(response):
    return MagicMock(side_effect=[
        (response, 0.5),   # main response
        (SOVEREIGN, 0.1),  # guard_response -> check_sovereignty
        (VALID, 0.1),      # guard_response -> continuity validator
        (SOVEREIGN, 0.1),  # hard-block check_sovereignty
        (EXTRACT, 0.1),    # extract_and_save_assumptions
    ])


def _step(e, raw_input, continue_narrative, response):
    shared = _mock_calls(response)
    with patch('engine.engine.call', shared), patch('engine.inference.call', shared):
        return e.step(raw_input, continue_narrative=continue_narrative)


def _regenerate(e, response):
    shared = _mock_calls(response)
    with patch('engine.engine.call', shared), patch('engine.inference.call', shared):
        return e.regenerate()


class TestRegenerate:
    def test_regenerate_replaces_last_response(self):
        e = _build_engine()
        _step(e, 'I knock on the door.', False, 'Iris ignores you completely.')
        result = _regenerate(e, 'Iris looks up, startled by the knock.')

        assert result['response'] == 'Iris looks up, startled by the knock.'
        assert e.history[-1] == {'role': 'assistant', 'content': 'Iris looks up, startled by the knock.'}
        assert e.display_history[-1] == {'role': 'assistant', 'content': 'Iris looks up, startled by the knock.'}
        # The bad first draft must not linger anywhere.
        assert 'Iris ignores you completely.' not in [m['content'] for m in e.history]
        assert 'Iris ignores you completely.' not in [m['content'] for m in e.display_history]

    def test_regenerate_keeps_same_turn_number(self):
        e = _build_engine()
        _step(e, 'I knock on the door.', False, 'Iris ignores you completely.')
        turn_after_step = e.turn
        result = _regenerate(e, 'Iris looks up, startled.')

        assert e.turn == turn_after_step
        assert result['turn'] == turn_after_step

    def test_regenerate_does_not_change_history_length(self):
        e = _build_engine()
        _step(e, 'I knock on the door.', False, 'Iris ignores you completely.')
        history_len_before = len(e.history)
        display_len_before = len(e.display_history)

        _regenerate(e, 'Iris looks up, startled.')

        assert len(e.history) == history_len_before
        assert len(e.display_history) == display_len_before

    def test_regenerate_preserves_player_input_in_history(self):
        """The player's own message must survive — only the assistant's
        reply to it is being redone."""
        e = _build_engine()
        _step(e, 'I knock on the door.', False, 'Iris ignores you completely.')
        _regenerate(e, 'Iris looks up, startled.')

        user_display = [m for m in e.display_history if m['role'] == 'user']
        assert len(user_display) == 1
        assert user_display[0]['content'] == 'I knock on the door.'

    def test_regenerate_after_continuation_turn_works(self):
        e = _build_engine()
        _step(e, '', True, 'Iris turns the page slowly.')
        result = _regenerate(e, 'Iris sets down the ledger and sighs.')

        assert result['response'] == 'Iris sets down the ledger and sighs.'
        # A continuation turn never had a user display entry — must still not have one.
        assert [m for m in e.display_history if m['role'] == 'user'] == []

    def test_regenerate_with_nothing_said_yet_raises(self):
        e = _build_engine()
        e.history = []
        e.display_history = []
        with pytest.raises(ValueError, match='nothing has been said yet'):
            _regenerate(e, 'irrelevant')

    def test_regenerate_with_only_opening_greeting_raises(self):
        """The fixture's fresh state (opening greeting, no turns played yet)
        must not be regenerable — there's no player input behind it."""
        e = _build_engine()
        with pytest.raises(ValueError, match='No player turn found'):
            _regenerate(e, 'irrelevant')

    def test_regenerate_twice_in_a_row(self):
        """Each regenerate() call must clean up after the previous one, not
        just the original — history must not grow across repeated retries."""
        e = _build_engine()
        _step(e, 'I knock on the door.', False, 'First attempt.')
        _regenerate(e, 'Second attempt.')
        history_len_after_first_regen = len(e.history)

        result = _regenerate(e, 'Third attempt.')

        assert len(e.history) == history_len_after_first_regen
        assert result['response'] == 'Third attempt.'
        assert e.history[-1]['content'] == 'Third attempt.'
