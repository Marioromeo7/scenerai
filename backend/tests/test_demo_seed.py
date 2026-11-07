"""Contract checks for the captured portfolio prefab used by the seeder."""
from engine.serializer import deserialize_engine
from seed_demo import CHAR_NAME, CHAR_PERSONALITY, GREETING, _prefab_state


def test_captured_prefab_is_loadable_by_the_real_engine_serializer():
    state = _prefab_state()
    engine = deserialize_engine(state)

    assert state["history"][0]["content"] == GREETING
    assert state["char_personality"] == CHAR_PERSONALITY
    assert len(state["layer1"]) >= 100
    assert len(state["memory"]["pinned"]) >= 3
    assert engine.world.location
    assert engine.persona.is_player
    assert any(entity.name == CHAR_NAME and not entity.is_player for entity in engine.entities)
    assert all(entity.appearance for entity in engine.entities)
    assert all(entity.behavioral_read and entity.never_does for entity in engine.entities if not entity.is_player)


def test_each_seed_load_gets_an_independent_prefab_object():
    first = _prefab_state()
    second = _prefab_state()

    first["world"]["flags"].append("test mutation")
    assert "test mutation" not in second["world"]["flags"]
