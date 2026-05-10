"""Tests for extract_and_save_assumptions and summarize_async helpers."""
import threading
import time
import pytest
from unittest.mock import patch, MagicMock
from engine.inference import extract_and_save_assumptions, summarize_async
from engine.types import Entity, Memory


def _wait_for_threads(timeout=2.0):
    """Let daemon threads spawned by helpers finish."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t.daemon and t.name != 'MainThread' and t.is_alive()]
        if not alive:
            break
        time.sleep(0.05)


# ── extract_and_save_assumptions ──────────────────────────────

class TestExtractAndSaveAssumptions:
    def _entities(self):
        player = Entity(name='Alex', pronouns='they/them', is_player=True)
        npc    = Entity(name='Iris', pronouns='she/her')
        return [player, npc]

    def test_stable_assumption_appended(self):
        entities = self._entities()
        iris = entities[1]

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {"Iris": ["silver hair pins"]}, "physical": {}}', 0.1
            )
            extract_and_save_assumptions('Iris adjusts her silver pins.', entities[0], entities)
            _wait_for_threads()

        assert 'silver hair pins' in iris.saved_assumptions

    def test_stable_assumption_not_duplicated(self):
        entities = self._entities()
        iris = entities[1]
        iris.saved_assumptions = ['silver hair pins']

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {"Iris": ["silver hair pins"]}, "physical": {}}', 0.1
            )
            extract_and_save_assumptions('Iris adjusts her pins.', entities[0], entities)
            _wait_for_threads()

        assert iris.saved_assumptions.count('silver hair pins') == 1

    def test_physical_state_set(self):
        entities = self._entities()
        iris = entities[1]

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {}, "physical": {"Iris": "bleeding from a cut on her hand"}}', 0.1
            )
            extract_and_save_assumptions('Iris cuts her hand.', entities[0], entities)
            _wait_for_threads()

        assert iris.physical_state == 'bleeding from a cut on her hand'

    def test_code_fenced_json_parsed(self):
        entities = self._entities()
        iris = entities[1]

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '```json\n{"stable": {"Iris": ["black gloves"]}, "physical": {}}\n```', 0.1
            )
            extract_and_save_assumptions('Iris wears black gloves.', entities[0], entities)
            _wait_for_threads()

        assert 'black gloves' in iris.saved_assumptions

    def test_bad_json_silently_ignored(self):
        entities = self._entities()

        with patch('engine.inference.call') as mock:
            mock.return_value = ('not json', 0.1)
            extract_and_save_assumptions('Something happened.', entities[0], entities)
            _wait_for_threads()

        # Should not raise; entities unchanged
        assert entities[1].saved_assumptions == []

    def test_with_lock(self):
        entities = self._entities()
        iris = entities[1]
        lock = threading.Lock()

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {"Iris": ["reading glasses"]}, "physical": {}}', 0.1
            )
            extract_and_save_assumptions('Iris puts on glasses.', entities[0], entities, lock=lock)
            _wait_for_threads()

        assert 'reading glasses' in iris.saved_assumptions

    def test_unknown_character_ignored(self):
        entities = self._entities()

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {"Unknown": ["some detail"]}, "physical": {}}', 0.1
            )
            extract_and_save_assumptions('Someone passes by.', entities[0], entities)
            _wait_for_threads()

        assert entities[0].saved_assumptions == []
        assert entities[1].saved_assumptions == []

    def test_llm_exception_silently_ignored(self):
        entities = self._entities()

        with patch('engine.inference.call') as mock:
            mock.side_effect = RuntimeError('API down')
            # Should not propagate — thread swallows it
            extract_and_save_assumptions('Something happened.', entities[0], entities)
            _wait_for_threads()

        assert entities[1].saved_assumptions == []


# ── summarize_async ───────────────────────────────────────────

class TestSummarizeAsync:
    def _chunk(self):
        return [
            {'role': 'user',      'content': 'Where did you come from?'},
            {'role': 'assistant', 'content': 'Iris stares without answering.'},
        ]

    def test_summary_appended_to_memory(self):
        memory = Memory()

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                ('Iris refused to answer.', 0.1),         # summary
                ('silence, refusal, tension', 0.1),        # beats
                ('YES', 0.1),                              # quality check
            ]
            summarize_async(self._chunk(), 'Alex', memory)
            _wait_for_threads()

        assert len(memory.summaries) == 1
        assert 'Iris refused to answer.' in memory.summaries[0]

    def test_poor_quality_triggers_retry(self):
        memory = Memory()

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                ('Bad summary.', 0.1),                     # first summary
                ('silence, refusal, tension', 0.1),        # beats
                ('NO', 0.1),                               # quality check fails
                ('Iris refused to answer Alex.', 0.1),     # retry summary
            ]
            summarize_async(self._chunk(), 'Alex', memory)
            _wait_for_threads()

        assert memory.summaries == ['Iris refused to answer Alex.']

    def test_llm_exception_does_not_crash(self):
        memory = Memory()

        with patch('engine.inference.call') as mock:
            mock.side_effect = RuntimeError('API down')
            summarize_async(self._chunk(), 'Alex', memory)
            _wait_for_threads()

        # Exception in daemon thread — memory unchanged, no crash
        assert memory.summaries == []


# ── guard_response: fallback dedup path ──────────────────────

class TestFallbackDedup:
    """Tests for the fallback retry when fallback == previous_response."""

    def _fixtures(self):
        from engine.types import WorldState
        persona  = Entity(name='Alex', pronouns='they/them', is_player=True)
        npc      = Entity(name='Iris', pronouns='she/her')
        entities = [persona, npc]
        world    = WorldState(location='Archive', time_of_day='night', era='modern', atmosphere='tense')
        memory   = Memory(pinned=['Iris has never entered the archive'])
        layer1   = 'Archive hall, locked door, night.'
        return persona, entities, world, memory, layer1

    def test_fallback_retry_triggered_when_matches_previous(self):
        from engine.inference import guard_response
        persona, entities, world, memory, layer1 = self._fixtures()
        prev = 'A tense silence holds.'

        VIOLATED  = '{"clean": false, "violations": ["narrator moved player"]}'
        SOVEREIGN = '{"clean": true, "violations": []}'
        VALID     = '{"valid": true, "violations": []}'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),          # sovereignty on original
                (VALID, 0.1),             # validator
                ('You step forward.', 0.2), # repair
                (VIOLATED, 0.1),          # sovereignty on repair: still violated
                (VALID, 0.1),             # validator on repair
                (prev, 0.2),              # fallback matches previous_response
                ('A different refusal.', 0.2),  # fallback retry
            ]
            result, meta = guard_response(
                'You walk to the door.', 'Walk.', persona, entities, world, memory, layer1,
                previous_response=prev,
            )

        assert result == 'A different refusal.'
        assert meta.get('fallback') is True

    def test_fallback_prefix_added_when_retry_also_matches(self):
        from engine.inference import guard_response
        persona, entities, world, memory, layer1 = self._fixtures()
        prev = 'A tense silence holds.'

        VIOLATED  = '{"clean": false, "violations": ["moved"]}'
        SOVEREIGN = '{"clean": true, "violations": []}'
        VALID     = '{"valid": true, "violations": []}'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),
                (VALID, 0.1),
                ('You step forward.', 0.2),
                (VIOLATED, 0.1),
                (VALID, 0.1),
                (prev, 0.2),             # fallback == previous
                (prev, 0.2),             # retry also == previous
            ]
            result, meta = guard_response(
                'You walk.', 'Walk.', persona, entities, world, memory, layer1,
                previous_response=prev,
            )

        # Should have the prefix prepended
        assert result.startswith('A quiet pause settles before the refusal changes shape.')
        assert meta.get('fallback') is True
