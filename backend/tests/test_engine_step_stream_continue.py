"""Tests for engine_step_stream's continue_narrative support -- added so
this streaming path stops silently diverging from Engine.step(), which has
supported continue_narrative from the start (see engine/engine.py). No
existing test file covered engine_step_stream at all before this."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import ai_service


def _make_engine():
    e = MagicMock()
    e.turn = 3
    e.metrics = {'turns': 3}
    e.last_target = 'Elena'
    e.scene_lang = 'en'
    e.persona = MagicMock(name='Alex', pronouns='he/him')
    e.persona.name = 'Alex'
    e.entities = []
    e.history = []
    e.display_history = []
    return e


async def _drain(agen):
    events = []
    async for event in agen:
        events.append(event)
    return events


class TestEngineStepStreamContinueNarrative:
    @pytest.mark.asyncio
    async def test_continue_narrative_skips_input_processing_and_display_history(self):
        engine = _make_engine()
        with patch('ai_service._reserve_turn_budget', new=AsyncMock()), \
             patch('ai_service.init_client'), \
             patch('ai_service.deserialize_engine', return_value=engine), \
             patch('ai_service.serialize_engine', return_value={}), \
             patch('ai_service.build_system', return_value='system prompt'), \
             patch('ai_service.detect_from_player_input') as mock_detect, \
             patch('ai_service.translate_input') as mock_translate_in, \
             patch('ai_service.preprocess') as mock_preprocess, \
             patch('ai_service.translate_output', return_value='The room settles into silence.'), \
             patch('ai_service.guard_response', return_value=('The room settles into silence.', {'revised': False})), \
             patch('ai_service.check_sovereignty', return_value={'clean': True, 'violations': [], 'count': 0}), \
             patch('ai_service.extract_and_save_assumptions'), \
             patch('ai_service._build_turn_media_context', return_value=None):

            async def fake_stream(*a, **kw):
                yield 'The room settles into silence.'
            with patch('ai_service.async_stream_call', side_effect=fake_stream):
                events = await _drain(ai_service.engine_step_stream(
                    {}, '', continue_narrative=True,
                ))

        mock_detect.assert_not_called()
        mock_translate_in.assert_not_called()
        mock_preprocess.assert_not_called()

        # No player message shown as a chat bubble for a continue turn.
        assert engine.display_history == [
            {'role': 'assistant', 'content': 'The room settles into silence.'}
        ]
        # The synthetic SYSTEM instruction is what actually goes to the model.
        assert engine.history[0]['role'] == 'user'
        assert '[SYSTEM:' in engine.history[0]['content']
        assert 'Alex' in engine.history[0]['content']

        done = next(e for e in events if e['type'] == 'done')
        assert done['response'] == 'The room settles into silence.'

    @pytest.mark.asyncio
    async def test_continue_narrative_previous_response_is_the_actual_last_turn(self):
        """Regression: same bug as Engine.step() (see test_engine_step_continue.py)
        -- previous_response was computed from display_history[:-1], which strips
        off the real previous response on the continue_narrative path since no
        user message gets appended there to justify the slice."""
        engine = _make_engine()
        engine.display_history = [{'role': 'assistant', 'content': 'Iris waits at the door.'}]
        with patch('ai_service._reserve_turn_budget', new=AsyncMock()), \
             patch('ai_service.init_client'), \
             patch('ai_service.deserialize_engine', return_value=engine), \
             patch('ai_service.serialize_engine', return_value={}), \
             patch('ai_service.build_system', return_value='system prompt'), \
             patch('ai_service.detect_from_player_input'), \
             patch('ai_service.translate_input'), \
             patch('ai_service.preprocess'), \
             patch('ai_service.translate_output', return_value='Iris turns the page.'), \
             patch('ai_service.guard_response', return_value=('Iris turns the page.', {'revised': False})) as mock_guard, \
             patch('ai_service.check_sovereignty', return_value={'clean': True, 'violations': [], 'count': 0}), \
             patch('ai_service.extract_and_save_assumptions'), \
             patch('ai_service._build_turn_media_context', return_value=None):

            async def fake_stream(*a, **kw):
                yield 'Iris turns the page.'
            with patch('ai_service.async_stream_call', side_effect=fake_stream):
                await _drain(ai_service.engine_step_stream({}, '', continue_narrative=True))

        assert mock_guard.call_args.args[-1] == 'Iris waits at the door.'

    @pytest.mark.asyncio
    async def test_stream_failure_propagates_instead_of_faking_a_blank_turn(self):
        """Regression: async_stream_call used to signal failure by yielding a
        None sentinel and then trying to `raise` on the next line -- but the
        caller `break`s the moment it sees that sentinel, so `.__anext__()`
        is never called again and that `raise` was dead code. A Groq failure
        was silently swallowed and treated as a successful empty/partial
        response instead of an error. It must now propagate all the way out
        of engine_step_stream so main.py's event_generator can turn it into
        a real "type": "error" SSE event instead of persisting a blank turn."""
        engine = _make_engine()
        with patch('ai_service._reserve_turn_budget', new=AsyncMock()), \
             patch('ai_service.init_client'), \
             patch('ai_service.deserialize_engine', return_value=engine), \
             patch('ai_service.build_system', return_value='system prompt'):

            async def fake_stream(*a, **kw):
                raise RuntimeError('Groq rate limit exceeded')
                yield  # pragma: no cover -- keeps this an async generator function

            with patch('ai_service.async_stream_call', side_effect=fake_stream):
                with pytest.raises(RuntimeError, match='Groq rate limit exceeded'):
                    await _drain(ai_service.engine_step_stream({}, '', continue_narrative=True))

        # No blank turn silently committed to session state.
        assert engine.history == [] or engine.history[-1]['role'] != 'assistant'
        assert engine.display_history == []

    @pytest.mark.asyncio
    async def test_default_still_processes_player_input_as_before(self):
        """continue_narrative defaults to False -- existing behavior (the
        only behavior that existed before this change) must be unchanged."""
        engine = _make_engine()
        with patch('ai_service._reserve_turn_budget', new=AsyncMock()), \
             patch('ai_service.init_client'), \
             patch('ai_service.deserialize_engine', return_value=engine), \
             patch('ai_service.serialize_engine', return_value={}), \
             patch('ai_service.build_system', return_value='system prompt'), \
             patch('ai_service.detect_from_player_input', return_value='en') as mock_detect, \
             patch('ai_service.translate_input', return_value='I step forward.') as mock_translate_in, \
             patch('ai_service.preprocess', return_value={
                 'clean': 'I step forward.', 'annotated': '[SOURCE=PLAYER:Alex] I step forward.',
                 'target': 'Elena', 'thoughts': [], 'same_space_risk': False,
             }) as mock_preprocess, \
             patch('ai_service.translate_output', return_value='You step forward.'), \
             patch('ai_service.guard_response', return_value=('You step forward.', {'revised': False})), \
             patch('ai_service.check_sovereignty', return_value={'clean': True, 'violations': [], 'count': 0}), \
             patch('ai_service.extract_and_save_assumptions'), \
             patch('ai_service._build_turn_media_context', return_value=None):

            async def fake_stream(*a, **kw):
                yield 'You step forward.'
            with patch('ai_service.async_stream_call', side_effect=fake_stream):
                await _drain(ai_service.engine_step_stream({}, 'I step forward.'))

        mock_detect.assert_called_once()
        mock_translate_in.assert_called_once()
        mock_preprocess.assert_called_once()
        assert engine.display_history == [
            {'role': 'user', 'content': 'I step forward.'},
            {'role': 'assistant', 'content': 'You step forward.'},
        ]
