"""Tests for extract_and_save_assumptions and summarize_chunk helpers.

Both used to run on daemon threads (fire-and-forget) — that meant their
mutations almost always lost the race against serialize_engine() running
immediately after step() returned, and were silently discarded every turn.
Both are now synchronous; no thread-waiting scaffolding needed anymore."""
import threading
from unittest.mock import patch
from engine.inference import extract_and_save_assumptions, summarize_chunk
from engine.types import Entity, Memory


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

        assert iris.saved_assumptions.count('silver hair pins') == 1

    def test_physical_state_set(self):
        entities = self._entities()
        iris = entities[1]

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {}, "physical": {"Iris": "bleeding from a cut on her hand"}}', 0.1
            )
            extract_and_save_assumptions('Iris cuts her hand.', entities[0], entities)

        assert iris.physical_state == 'bleeding from a cut on her hand'

    def test_code_fenced_json_parsed(self):
        entities = self._entities()
        iris = entities[1]

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '```json\n{"stable": {"Iris": ["black gloves"]}, "physical": {}}\n```', 0.1
            )
            extract_and_save_assumptions('Iris wears black gloves.', entities[0], entities)

        assert 'black gloves' in iris.saved_assumptions

    def test_bad_json_silently_ignored(self):
        entities = self._entities()

        with patch('engine.inference.call') as mock:
            mock.return_value = ('not json', 0.1)
            extract_and_save_assumptions('Something happened.', entities[0], entities)

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

        assert 'reading glasses' in iris.saved_assumptions

    def test_unknown_character_ignored(self):
        entities = self._entities()

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {"Unknown": ["some detail"]}, "physical": {}}', 0.1
            )
            extract_and_save_assumptions('Someone passes by.', entities[0], entities)

        assert entities[0].saved_assumptions == []
        assert entities[1].saved_assumptions == []

    def test_llm_exception_silently_ignored(self):
        entities = self._entities()

        with patch('engine.inference.call') as mock:
            mock.side_effect = RuntimeError('API down')
            extract_and_save_assumptions('Something happened.', entities[0], entities)

        assert entities[1].saved_assumptions == []

    def test_name_matched_case_insensitively(self):
        """A model returning 'iris' or 'IRIS' instead of the exact-cased 'Iris'
        must still match — this used to silently drop the update entirely."""
        entities = self._entities()
        iris = entities[1]

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {"iris": ["a small scar"]}, "physical": {}}', 0.1
            )
            extract_and_save_assumptions('Iris turns her head.', entities[0], entities)

        assert 'a small scar' in iris.saved_assumptions

    # ── resolve_shift (integrity erosion) ──────────────────────

    def test_resolve_shift_applied_for_tracked_npc(self):
        entities = self._entities()
        iris = entities[1]
        iris.never_does = 'would never yield the archive key'

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {}, "physical": {}, '
                '"resolve_shift": {"Iris": {"delta": -0.1, "reason": "showed vulnerability"}}}', 0.1
            )
            extract_and_save_assumptions('Iris hesitates, her composure cracking.', entities[0], entities)

        assert iris.integrity_resolve == 0.9
        assert iris.integrity_notes == ['showed vulnerability']

    def test_resolve_shift_ignored_for_untracked_npc(self):
        """No never_does rule means nothing to erode — resolve_shift for this
        character (if the model hallucinates one anyway) must be a no-op."""
        entities = self._entities()
        iris = entities[1]  # no never_does set

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {}, "physical": {}, '
                '"resolve_shift": {"Iris": {"delta": -0.5, "reason": "irrelevant"}}}', 0.1
            )
            extract_and_save_assumptions('Something happens.', entities[0], entities)

        assert iris.integrity_resolve == 1.0

    def test_resolve_shift_survives_the_full_pipeline_with_lock(self):
        """The glue between the LLM call and _apply_resolve_shifts, exercised
        end to end with the lock argument engine.step() actually passes."""
        entities = self._entities()
        iris = entities[1]
        iris.never_does = 'would never surrender her power over the archive'
        lock = threading.Lock()

        with patch('engine.inference.call') as mock:
            mock.return_value = (
                '{"stable": {"Iris": ["a bronze key"]}, "physical": {}, '
                '"resolve_shift": {"Iris": {"delta": -0.15, "reason": "trusted a stranger"}}}', 0.1
            )
            extract_and_save_assumptions(
                'Iris presses the key into your hand.', entities[0], entities, lock=lock
            )

        assert 'a bronze key' in iris.saved_assumptions
        assert iris.integrity_resolve == 0.85
        assert iris.integrity_notes == ['trusted a stranger']


# ── summarize_chunk ────────────────────────────────────────────

class TestSummarizeChunk:
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
            summarize_chunk(self._chunk(), 'Alex', memory)

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
            summarize_chunk(self._chunk(), 'Alex', memory)

        assert memory.summaries == ['Iris refused to answer Alex.']

    def test_llm_exception_does_not_crash(self):
        memory = Memory()

        with patch('engine.inference.call') as mock:
            mock.side_effect = RuntimeError('API down')
            summarize_chunk(self._chunk(), 'Alex', memory)

        # Exception raised and caught inline — memory unchanged, no crash
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
        VALID     = '{"valid": true, "violations": []}'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),          # sovereignty on original
                (VALID, 0.1),             # validator
                ('You step forward.', 0.2),  # repair
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
