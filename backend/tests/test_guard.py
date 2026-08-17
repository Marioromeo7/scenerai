"""Tests for guard_response — the continuity + sovereignty repair chain."""
from unittest.mock import patch
from engine.inference import guard_response
from engine.types import Entity, WorldState, Memory

# ── Shared JSON stubs ─────────────────────────────────────────

SOVEREIGN     = '{"clean": true,  "violations": []}'
VIOLATED      = '{"clean": false, "violations": ["narrator moved player"]}'
VALID         = '{"valid": true,  "violations": []}'
INVALID       = '{"valid": false, "violations": ["Iris revealed unknown knowledge"]}'


# ── Fixtures ──────────────────────────────────────────────────

def _fixtures():
    persona  = Entity(name='Alex', pronouns='they/them', role='Detective', is_player=True)
    npc      = Entity(name='Iris', pronouns='she/her', role='Archivist',
                      never_does='directly confess emotion', behavioral_read='guarded precision')
    entities = [persona, npc]
    world    = WorldState(location='Archive Hall', time_of_day='midnight',
                          era='modern', atmosphere='tense', flags=['door_locked'])
    memory   = Memory(pinned=['Iris has never entered the archive'])
    layer1   = 'A sealed archive at night. Iris stands at the locked door.'
    return persona, entities, world, memory, layer1


# ── Happy path ────────────────────────────────────────────────

class TestCleanResponse:
    def test_clean_response_returned_unchanged(self):
        persona, entities, world, memory, layer1 = _fixtures()
        response = 'Iris nods quietly, her gaze unwavering.'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (SOVEREIGN, 0.1),  # check_sovereignty on original
                (VALID, 0.1),      # continuity validator
            ]
            result, meta = guard_response(
                response, 'Speak to me.', persona, entities, world, memory, layer1
            )

        assert result == response
        assert meta['revised'] is False
        assert meta['violations'] == []


# ── Sovereignty violation path ────────────────────────────────

class TestSovereigntyViolation:
    def test_sovereignty_violation_triggers_repair_and_returns_repaired(self):
        persona, entities, world, memory, layer1 = _fixtures()
        response = 'You walk to the archive door.'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),              # sovereignty: violated
                (VALID, 0.1),                 # continuity validator: ok
                ('Iris watches steadily.', 0.2),  # repair LLM
                (SOVEREIGN, 0.1),             # sovereignty on repair: clean
                (VALID, 0.1),                 # validator on repair: ok
            ]
            result, meta = guard_response(
                response, 'Walk to the door.', persona, entities, world, memory, layer1
            )

        assert result == 'Iris watches steadily.'
        assert meta['revised'] is True
        assert len(meta['violations']) > 0

    def test_sovereignty_violation_recorded_in_meta(self):
        persona, entities, world, memory, layer1 = _fixtures()

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),
                (VALID, 0.1),
                ('Iris stands firm.', 0.2),
                (SOVEREIGN, 0.1),
                (VALID, 0.1),
            ]
            _, meta = guard_response(
                'You step forward.', 'Move.', persona, entities, world, memory, layer1
            )

        assert any('player agency' in v for v in meta['violations'])


# ── Continuity violation path ─────────────────────────────────

class TestContinuityViolation:
    def test_continuity_violation_triggers_repair(self):
        persona, entities, world, memory, layer1 = _fixtures()
        response = 'Iris described every detail of the mosaic inside the archive.'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (SOVEREIGN, 0.1),                                   # sovereignty: clean
                (INVALID, 0.1),                                     # continuity: violated
                ('Iris said nothing about the archive interior.', 0.2),  # repair
                (SOVEREIGN, 0.1),                                   # sovereignty on repair
                (VALID, 0.1),                                       # validator on repair
            ]
            result, meta = guard_response(
                response, 'Describe inside.', persona, entities, world, memory, layer1
            )

        assert result == 'Iris said nothing about the archive interior.'
        assert meta['revised'] is True


# ── Near-duplicate repair escalates to fallback ────────────────
# In production, a repair pass re-used an entire prior paragraph verbatim
# with just a sentence appended, passed re-validation cleanly, and shipped
# as-is — _same_raw_response's exact-match check never saw it as a repeat.

class TestNearDuplicateRepair:
    def test_repair_reusing_previous_response_escalates_to_fallback(self):
        persona, entities, world, memory, layer1 = _fixtures()
        previous = (
            'Iris nods once, her gaze steady, and returns to the ledger without '
            'another word, the silence settling comfortably between you.'
        )
        # Repair reuses the entire previous response almost verbatim, tacking on
        # one extra sentence — a real repeat a human reader would notice instantly.
        near_duplicate_repair = previous + ' She closes the book.'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (SOVEREIGN, 0.1),                 # sovereignty: clean
                (INVALID, 0.1),                   # continuity: violated
                (near_duplicate_repair, 0.2),     # repair: near-duplicate of previous
                (SOVEREIGN, 0.1),                 # sovereignty on repair: clean
                (VALID, 0.1),                     # continuity re-validation on repair: clean
                ('Iris looks away, saying nothing new.', 0.2),  # fallback: genuinely different
            ]
            result, meta = guard_response(
                'Describe the archive.', 'Describe inside.', persona, entities, world, memory, layer1,
                previous_response=previous,
            )

        assert result == 'Iris looks away, saying nothing new.'
        assert meta.get('fallback') is True

    def test_repair_not_similar_to_previous_is_not_escalated(self):
        """Sanity check: a genuinely different, clean repair must NOT be routed
        through fallback just because a previous_response exists."""
        persona, entities, world, memory, layer1 = _fixtures()
        previous = 'Iris nods once and returns to the ledger.'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (SOVEREIGN, 0.1),
                (INVALID, 0.1),
                ('A cold wind moves through the archive, and Iris shivers.', 0.2),  # unrelated repair
                (SOVEREIGN, 0.1),
                (VALID, 0.1),
            ]
            result, meta = guard_response(
                'Describe the archive.', 'Describe inside.', persona, entities, world, memory, layer1,
                previous_response=previous,
            )

        assert result == 'A cold wind moves through the archive, and Iris shivers.'
        assert meta.get('fallback') is not True


# ── Fallback path ─────────────────────────────────────────────

class TestFallback:
    def test_fallback_used_when_repair_still_imperfect(self):
        persona, entities, world, memory, layer1 = _fixtures()

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),                    # sovereignty: violated
                (VALID, 0.1),                       # validator: ok
                ('You step forward slightly.', 0.2),  # repair LLM (still violating)
                (VIOLATED, 0.1),                    # sovereignty on repair: still violated
                (VALID, 0.1),                       # validator on repair: ok
                ('A tense silence settles.', 0.2),  # fallback LLM
            ]
            result, meta = guard_response(
                'You walk to the door.', 'Walk.', persona, entities, world, memory, layer1
            )

        assert result == 'A tense silence settles.'
        assert meta.get('fallback') is True

    def test_fallback_marked_in_meta(self):
        persona, entities, world, memory, layer1 = _fixtures()

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),
                (VALID, 0.1),
                ('You move forward.', 0.2),
                (VIOLATED, 0.1),
                (VALID, 0.1),
                ('Iris remains still.', 0.2),
            ]
            _, meta = guard_response(
                'You walk.', 'Walk.', persona, entities, world, memory, layer1
            )

        assert meta['revised'] is True
        assert 'fallback' in meta


# ── H-2 fix: no path returns original violating response ──────

class TestH2Fix:
    def test_repair_llm_exception_returns_safe_string_not_original(self):
        """When the repair LLM throws, must not return the original violating response."""
        persona, entities, world, memory, layer1 = _fixtures()
        original = 'You walk directly to the archive door.'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),          # sovereignty: violated
                (VALID, 0.1),             # validator: ok
                RuntimeError('API down'),  # repair LLM raises
            ]
            result, meta = guard_response(
                original, 'Walk.', persona, entities, world, memory, layer1
            )

        assert result != original
        assert meta['revised'] is True

    def test_fallback_llm_exception_returns_repair_not_original(self):
        """When fallback LLM throws, must return the imperfect repair, not the original."""
        persona, entities, world, memory, layer1 = _fixtures()
        original = 'You walk directly to the archive door.'
        repair   = 'You hesitate slightly at the threshold.'

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),           # sovereignty: violated
                (VALID, 0.1),              # validator: ok
                (repair, 0.2),             # repair LLM
                (VIOLATED, 0.1),           # sovereignty on repair: still violated
                (VALID, 0.1),              # validator on repair: ok
                RuntimeError('fallback!'),  # fallback LLM raises (inner try/except)
            ]
            result, meta = guard_response(
                original, 'Walk.', persona, entities, world, memory, layer1
            )

        assert result != original
        assert result == repair

    def test_complete_chain_failure_returns_safe_not_original(self):
        """If the entire repair try block throws, returns safe string, not original."""
        persona, entities, world, memory, layer1 = _fixtures()
        original = 'You teleport inside the archive.'

        with patch('engine.inference.call') as mock:
            # sovereignty check passes (so check_sovereignty is fine)
            # but validator call raises immediately
            mock.side_effect = [
                (SOVEREIGN, 0.1),           # sovereignty: clean
                RuntimeError('network'),    # validator raises → outer except
            ]
            result, meta = guard_response(
                original, 'Teleport.', persona, entities, world, memory, layer1
            )

        assert result != original
        assert meta['revised'] is True


# ── Validator JSON parse failure ──────────────────────────────

class TestValidatorParseFailure:
    def test_non_json_validator_response_treated_as_violation(self):
        """When continuity validator returns non-JSON, it should be treated as a failed check."""
        persona, entities, world, memory, layer1 = _fixtures()
        response = 'Iris nods quietly.'

        with patch('engine.inference.call') as mock:
            # sovereignty clean, but validator returns garbage → violation recorded
            mock.side_effect = [
                (SOVEREIGN, 0.1),          # sovereignty: clean
                ('Sorry, I cannot help.', 0.1),  # validator returns non-JSON
                (response, 0.2),           # repair (returns same)
                (SOVEREIGN, 0.1),          # sovereignty on repair: clean
                (VALID, 0.1),              # validator on repair: ok
            ]
            result, meta = guard_response(
                response, 'Speak.', persona, entities, world, memory, layer1
            )

        # Non-JSON validator means guard recorded a "could not produce verdict" violation
        assert 'continuity validator could not produce a valid verdict' in meta['violations']


# ── Scene language forwarded ──────────────────────────────────

class TestSceneLang:
    def test_scene_lang_passed_through(self):
        """The repair payload must contain the scene language instruction."""
        persona, entities, world, memory, layer1 = _fixtures()

        with patch('engine.inference.call') as mock:
            mock.side_effect = [
                (VIOLATED, 0.1),
                (VALID, 0.1),
                ('Iris se tient immobile.', 0.2),  # repair in French
                (SOVEREIGN, 0.1),
                (VALID, 0.1),
            ]
            result, _ = guard_response(
                'Vous marchez.', 'Marchez.', persona, entities, world, memory, layer1,
                scene_lang='French'
            )

        # Verify the repair call included 'French' somewhere in its payload
        repair_call_args = mock.call_args_list[2]
        repair_user_content = repair_call_args[0][1][0]['content']
        assert 'French' in repair_user_content
