"""Tests for engine dataclasses and state objects."""
import pytest
from engine.types import Entity, WorldState, Memory, ContentFilter, FilterState


class TestEntity:
    def test_block_player_marker(self):
        e = Entity(name='Alex', pronouns='they/them', role='Detective', is_player=True)
        assert '← PLAYER' in e.block()

    def test_block_npc_no_player_marker(self):
        e = Entity(name='Iris', pronouns='she/her', role='Archivist')
        assert '← PLAYER' not in e.block()

    def test_block_not_present(self):
        e = Entity(name='Guard', pronouns='he/him', present=False)
        assert 'NOT in scene' in e.block()

    def test_block_present(self):
        e = Entity(name='Guard', pronouns='he/him', present=True)
        assert 'present' in e.block()

    def test_block_includes_name_and_pronouns(self):
        e = Entity(name='Iris', pronouns='she/her', role='Archivist')
        block = e.block()
        assert 'Iris' in block
        assert 'she/her' in block

    def test_block_appearance(self):
        e = Entity(name='Iris', pronouns='she/her', appearance='Tall, silver-haired')
        assert 'Tall, silver-haired' in e.block()

    def test_block_physical_state(self):
        e = Entity(name='Iris', pronouns='she/her', physical_state='Standing at the door')
        assert 'Standing at the door' in e.block()

    def test_block_saved_assumptions(self):
        e = Entity(name='Iris', pronouns='she/her',
                   saved_assumptions=['Carries a bronze key', 'Hates small talk'])
        block = e.block()
        assert 'Carries a bronze key' in block
        assert 'Hates small talk' in block

    def test_block_emotional_state(self):
        e = Entity(name='Iris', pronouns='she/her', emotional_state='guarded')
        assert 'guarded' in e.block()

    def test_block_history_shows_last_three(self):
        e = Entity(name='Iris', pronouns='she/her',
                   history=[f'event {i}' for i in range(6)])
        block = e.block()
        assert 'event 3' in block
        assert 'event 4' in block
        assert 'event 5' in block
        assert 'event 0' not in block

    def test_block_partial_false_omits_state_fields(self):
        e = Entity(name='Iris', pronouns='she/her', mood='tense', location='archive')
        block = e.block(full=False)
        assert 'tense' not in block
        assert 'archive' not in block


class TestWorldState:
    def test_block_all_fields(self):
        w = WorldState(location='Archive Hall', time_of_day='midnight',
                       era='modern', atmosphere='tense')
        block = w.block()
        assert 'Archive Hall' in block
        assert 'midnight' in block
        assert 'modern' in block
        assert 'tense' in block

    def test_block_flags_listed(self):
        w = WorldState(flags=['door_locked', 'key_missing'])
        block = w.block()
        assert 'door_locked' in block
        assert 'key_missing' in block

    def test_block_no_flags(self):
        w = WorldState()
        assert 'none' in w.block()


class TestMemory:
    def test_block_empty(self):
        assert Memory().block() == '(empty)'

    def test_block_pinned_facts(self):
        m = Memory(pinned=['The archive is sealed', 'Iris has never entered'])
        block = m.block()
        assert 'PINNED FACTS' in block
        assert 'The archive is sealed' in block
        assert 'Iris has never entered' in block

    def test_block_summaries(self):
        m = Memory(summaries=['Alex arrived at midnight'])
        block = m.block()
        assert 'SUMMARIES' in block
        assert 'Alex arrived at midnight' in block

    def test_block_summaries_truncated_to_last_three(self):
        m = Memory(summaries=[f'summary {i}' for i in range(10)])
        block = m.block()
        assert 'summary 9' in block
        assert 'summary 8' in block
        assert 'summary 7' in block
        assert 'summary 0' not in block

    def test_block_pinned_and_summaries(self):
        m = Memory(pinned=['fact'], summaries=['sum'])
        block = m.block()
        assert 'fact' in block
        assert 'sum' in block


class TestContentFilter:
    def test_default_inactive(self):
        assert not ContentFilter().is_active

    def test_on_is_active(self):
        assert ContentFilter(state=FilterState.ON).is_active

    def test_force_is_active(self):
        assert ContentFilter(state=FilterState.FORCE).is_active

    def test_activate_sets_state_and_shy(self):
        f = ContentFilter()
        entities = [Entity(name='A', pronouns='they/them')]
        f.activate(entities)
        assert f.is_active
        assert entities[0].shy

    def test_deactivate_clears_state_and_shy(self):
        f = ContentFilter(state=FilterState.ON)
        entities = [Entity(name='A', pronouns='they/them', shy=True)]
        result = f.deactivate(entities)
        assert result is True
        assert not f.is_active
        assert not entities[0].shy

    def test_deactivate_blocked_by_parental_lock(self):
        f = ContentFilter(state=FilterState.ON, parental_lock=True)
        entities = [Entity(name='A', pronouns='they/them', shy=True)]
        result = f.deactivate(entities)
        assert result is False
        assert f.is_active
        assert entities[0].shy

    def test_prompt_block_empty_when_inactive(self):
        assert ContentFilter().prompt_block() == ''

    def test_prompt_block_contains_filter_text_when_active(self):
        block = ContentFilter(state=FilterState.ON).prompt_block()
        assert 'CONTENT FILTER' in block
        assert 'explicit' in block.lower()
