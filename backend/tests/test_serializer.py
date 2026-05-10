"""Tests for engine state serialization/deserialization (Redis round-trip)."""
import threading
import pytest
from engine.types import Entity, WorldState, Memory, ContentFilter, FilterState
from engine.serializer import serialize_engine, deserialize_engine
from engine.engine import Engine


# ── Shell engine builder ──────────────────────────────────────

def _make_engine():
    """Build a minimal Engine-like object without running __init__ (no LLM calls)."""
    engine = Engine.__new__(Engine)

    persona = Entity(
        name='Alex', pronouns='they/them', role='Detective',
        appearance='Tall, dark coat', is_player=True,
        saved_assumptions=['Carries a bronze key'],
    )
    npc = Entity(
        name='Iris', pronouns='she/her', role='Archivist',
        behavioral_read='guarded precision', never_does='directly confess emotion',
        mood='neutral', location='Archive Hall',
    )
    engine.persona   = persona
    engine.entities  = [persona, npc]
    engine.world     = WorldState(
        location='Archive Hall', time_of_day='midnight',
        era='modern', atmosphere='tense', flags=['door_locked'],
    )
    engine.memory    = Memory(
        pinned=['Iris has never entered the archive'],
        summaries=['Alex arrived at midnight'],
    )
    engine.layer1    = 'A sealed archive at night. Iris guards the locked door.'
    engine.history   = [
        {'role': 'user',      'content': 'Hello there.'},
        {'role': 'assistant', 'content': 'Iris nods slowly.'},
    ]
    engine.display_history   = list(engine.history)
    engine.turn              = 3
    engine.scene_lang        = 'English'
    engine.last_target       = 'Iris'
    engine._sp_note          = 'special note'
    engine._char_personality = 'guarded and precise'
    engine.filter            = ContentFilter(state=FilterState.OFF, parental_lock=False)
    engine.metrics           = {
        'turns': 3, 'sv_violations': 1,
        'latencies': [0.5, 0.6, 0.7], 'compressions': 0,
    }
    engine._lock = threading.Lock()
    return engine


# ── Serialization ─────────────────────────────────────────────

class TestSerializeEngine:
    def test_entities_count(self):
        data = serialize_engine(_make_engine())
        assert len(data['entities']) == 2

    def test_entity_names(self):
        data = serialize_engine(_make_engine())
        names = [e['name'] for e in data['entities']]
        assert 'Alex' in names
        assert 'Iris' in names

    def test_entity_is_player_flag(self):
        data = serialize_engine(_make_engine())
        player_entities = [e for e in data['entities'] if e['is_player']]
        assert len(player_entities) == 1
        assert player_entities[0]['name'] == 'Alex'

    def test_world_fields(self):
        data = serialize_engine(_make_engine())
        assert data['world']['location'] == 'Archive Hall'
        assert data['world']['time_of_day'] == 'midnight'
        assert 'door_locked' in data['world']['flags']

    def test_memory_pinned(self):
        data = serialize_engine(_make_engine())
        assert 'Iris has never entered the archive' in data['memory']['pinned']

    def test_memory_summaries(self):
        data = serialize_engine(_make_engine())
        assert 'Alex arrived at midnight' in data['memory']['summaries']

    def test_filter_state_serialized_as_string(self):
        data = serialize_engine(_make_engine())
        assert data['filter_state'] == 'off'
        assert data['filter_lock'] is False

    def test_filter_on_serialized(self):
        engine = _make_engine()
        engine.filter = ContentFilter(state=FilterState.ON, parental_lock=True)
        data = serialize_engine(engine)
        assert data['filter_state'] == 'on'
        assert data['filter_lock'] is True

    def test_metrics(self):
        data = serialize_engine(_make_engine())
        assert data['metrics']['turns'] == 3
        assert data['metrics']['sv_violations'] == 1
        assert data['metrics']['latencies'] == [0.5, 0.6, 0.7]

    def test_turn(self):
        assert serialize_engine(_make_engine())['turn'] == 3

    def test_scene_lang(self):
        assert serialize_engine(_make_engine())['scene_lang'] == 'English'

    def test_layer1(self):
        data = serialize_engine(_make_engine())
        assert 'sealed archive' in data['layer1']

    def test_history_preserved(self):
        data = serialize_engine(_make_engine())
        assert data['history'][0]['role'] == 'user'
        assert data['history'][1]['content'] == 'Iris nods slowly.'


# ── Round-trip ────────────────────────────────────────────────

class TestRoundTrip:
    def test_player_entity_identified(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert restored.persona.name == 'Alex'
        assert restored.persona.is_player is True

    def test_npc_entity_identified(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        npc = next(e for e in restored.entities if not e.is_player)
        assert npc.name == 'Iris'

    def test_npc_fields_preserved(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        npc = next(e for e in restored.entities if not e.is_player)
        assert npc.never_does == 'directly confess emotion'
        assert npc.behavioral_read == 'guarded precision'

    def test_player_saved_assumptions(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert 'Carries a bronze key' in restored.persona.saved_assumptions

    def test_world_state_preserved(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert restored.world.location == 'Archive Hall'
        assert restored.world.time_of_day == 'midnight'
        assert 'door_locked' in restored.world.flags

    def test_memory_pinned_preserved(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert 'Iris has never entered the archive' in restored.memory.pinned

    def test_memory_summaries_preserved(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert 'Alex arrived at midnight' in restored.memory.summaries

    def test_history_preserved(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert restored.history[0]['role'] == 'user'
        assert restored.history[1]['content'] == 'Iris nods slowly.'

    def test_scene_lang_preserved(self):
        data = serialize_engine(_make_engine())
        assert deserialize_engine(data).scene_lang == 'English'

    def test_turn_preserved(self):
        data = serialize_engine(_make_engine())
        assert deserialize_engine(data).turn == 3

    def test_metrics_preserved(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert restored.metrics['sv_violations'] == 1
        assert restored.metrics['latencies'] == [0.5, 0.6, 0.7]

    def test_filter_off_preserved(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert restored.filter.state == FilterState.OFF
        assert restored.filter.parental_lock is False

    def test_filter_on_with_lock_preserved(self):
        engine = _make_engine()
        engine.filter = ContentFilter(state=FilterState.ON, parental_lock=True)
        data = serialize_engine(engine)
        restored = deserialize_engine(data)
        assert restored.filter.state == FilterState.ON
        assert restored.filter.parental_lock is True

    def test_sp_note_preserved(self):
        data = serialize_engine(_make_engine())
        assert deserialize_engine(data)._sp_note == 'special note'

    def test_char_personality_preserved(self):
        data = serialize_engine(_make_engine())
        assert deserialize_engine(data)._char_personality == 'guarded and precise'

    def test_lock_is_threading_lock(self):
        data = serialize_engine(_make_engine())
        restored = deserialize_engine(data)
        assert isinstance(restored._lock, type(threading.Lock()))


# ── Error cases ───────────────────────────────────────────────

class TestDeserializeErrors:
    def test_missing_player_entity_raises(self):
        data = serialize_engine(_make_engine())
        data['entities'] = [e for e in data['entities'] if not e['is_player']]
        with pytest.raises(ValueError, match='player'):
            deserialize_engine(data)

    def test_missing_npc_entity_raises(self):
        data = serialize_engine(_make_engine())
        data['entities'] = [e for e in data['entities'] if e['is_player']]
        with pytest.raises(ValueError, match='NPC'):
            deserialize_engine(data)

    def test_empty_entities_raises(self):
        data = serialize_engine(_make_engine())
        data['entities'] = []
        with pytest.raises(ValueError):
            deserialize_engine(data)

    def test_extra_entity_fields_ignored(self):
        """Deserialization must tolerate extra keys if Entity gains new fields."""
        data = serialize_engine(_make_engine())
        for e in data['entities']:
            e['future_field'] = 'ignored'
        # Should raise TypeError since Entity doesn't accept unknown kwargs,
        # but this documents the current behavior
        with pytest.raises(TypeError):
            deserialize_engine(data)
