"""Tests for ai_service._build_turn_media_context — the piece that decides
which entity a turn's image depicts and builds the image/narration
context, without needing a live engine or Groq call."""
from unittest.mock import patch, MagicMock
from engine.types import Entity
import ai_service


def _make_engine(entities):
    e = MagicMock()
    e.entities = entities
    return e


class TestBuildTurnMediaContext:
    def test_picks_first_present_non_player_non_collective_entity(self):
        player = Entity(name='Alex', is_player=True, present=True)
        crowd = Entity(name='Guards', is_collective=True, present=True)
        npc = Entity(name='Elena', pronouns='she/her', present=True, appearance='silver hair')
        engine = _make_engine([player, crowd, npc])

        with patch('ai_service.build_scene_image_prompt', return_value='a prompt') as mock_prompt, \
             patch('ai_service.get_voice_profile', return_value={'voice_id': 'af_sarah', 'speed': 1.0}) as mock_voice:
            result = ai_service._build_turn_media_context(
                engine, '**Elena crosses her arms.**\n"Hello."'
            )

        mock_prompt.assert_called_once_with(npc)
        mock_voice.assert_called_once_with(npc)
        assert result['image_prompt'] == 'a prompt'
        assert result['voice_id'] == 'af_sarah'
        assert result['narration_text'] == '"Hello."'  # action-only line dropped

    def test_ignores_absent_entities(self):
        npc_absent = Entity(name='Ghost', present=False)
        npc_present = Entity(name='Elena', present=True, appearance='x')
        engine = _make_engine([npc_absent, npc_present])

        with patch('ai_service.build_scene_image_prompt', return_value='p'), \
             patch('ai_service.get_voice_profile', return_value={'voice_id': 'v', 'speed': 1.0}):
            result = ai_service._build_turn_media_context(engine, 'text')

        assert result is not None

    def test_returns_none_when_no_npc_to_depict(self):
        player_only = Entity(name='Alex', is_player=True, present=True)
        engine = _make_engine([player_only])

        result = ai_service._build_turn_media_context(engine, 'text')

        assert result is None

    def test_returns_none_when_narration_text_is_empty_after_filtering(self):
        """A turn that's pure stage direction (**text**) has nothing left
        to narrate after filter_for_narration strips action-only lines --
        no point enqueuing a media job with empty narration."""
        npc = Entity(name='Elena', present=True, appearance='x')
        engine = _make_engine([npc])

        with patch('ai_service.build_scene_image_prompt', return_value='p'), \
             patch('ai_service.get_voice_profile', return_value={'voice_id': 'v', 'speed': 1.0}):
            result = ai_service._build_turn_media_context(engine, '**Elena walks away silently.**')

        assert result is None
