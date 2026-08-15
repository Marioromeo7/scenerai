"""Tests for engine/voice_prompts.py — per-character voice locking and
narration-text filtering."""
from unittest.mock import patch
from engine.types import Entity
from engine.voice_prompts import get_voice_profile, filter_for_narration, AVAILABLE_VOICES


class TestGetVoiceProfile:
    def test_derives_and_locks(self):
        entity = Entity(name='Elena', pronouns='she/her', role='ranger', mood='guarded')
        with patch('engine.voice_prompts.call') as mock:
            mock.return_value = ('{"voice_id": "af_river", "speed": 1.05}', 0.1)
            profile = get_voice_profile(entity)
        assert profile == {'voice_id': 'af_river', 'speed': 1.05}
        assert entity.voice_id == 'af_river'
        assert entity.voice_speed == 1.05

    def test_second_call_reuses_locked_voice_even_if_mood_changed(self):
        entity = Entity(name='Elena', pronouns='she/her', mood='guarded')
        with patch('engine.voice_prompts.call') as mock:
            mock.return_value = ('{"voice_id": "af_river", "speed": 1.0}', 0.1)
            get_voice_profile(entity)
            entity.mood = 'furious'  # mood drifted -- voice must NOT re-derive
            profile = get_voice_profile(entity)
        assert profile['voice_id'] == 'af_river'
        assert mock.call_count == 1

    def test_invalid_voice_id_falls_back_to_pronoun_default(self):
        entity = Entity(name='Elena', pronouns='she/her')
        with patch('engine.voice_prompts.call') as mock:
            mock.return_value = ('{"voice_id": "totally_made_up", "speed": 1.0}', 0.1)
            profile = get_voice_profile(entity)
        assert profile['voice_id'] in AVAILABLE_VOICES
        assert profile['voice_id'].startswith('af_')  # she/her -> female voice pool

    def test_malformed_json_falls_back_gracefully(self):
        entity = Entity(name='Elena', pronouns='he/him')
        with patch('engine.voice_prompts.call') as mock:
            mock.return_value = ('not json at all', 0.1)
            profile = get_voice_profile(entity)
        assert profile['voice_id'] in AVAILABLE_VOICES
        assert profile['speed'] == 1.0

    def test_speed_clamped_to_sane_bounds(self):
        entity = Entity(name='Elena', pronouns='she/her')
        with patch('engine.voice_prompts.call') as mock:
            mock.return_value = ('{"voice_id": "af_sarah", "speed": 5.0}', 0.1)
            profile = get_voice_profile(entity)
        assert profile['speed'] <= 1.4


class TestFilterForNarration:
    def test_drops_pure_action_lines(self):
        text = '**She crosses her arms and looks away.**\n"I don\'t want to talk about it."'
        result = filter_for_narration(text)
        assert 'crosses her arms' not in result
        assert "I don't want to talk about it." in result

    def test_strips_bold_markers_from_mixed_lines(self):
        text = '**Elena sighs.** "Fine, have it your way," she says.'
        result = filter_for_narration(text)
        assert '*' not in result
        assert 'Fine, have it your way' in result

    def test_keeps_plain_narration_prose(self):
        text = 'The tavern was quiet, save for the crackle of the fire.'
        result = filter_for_narration(text)
        assert result == text

    def test_empty_lines_dropped(self):
        text = '"Hello."\n\n\n"Goodbye."'
        result = filter_for_narration(text)
        assert result == '"Hello."\n"Goodbye."'
