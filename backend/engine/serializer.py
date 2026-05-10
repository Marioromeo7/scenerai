"""
Engine state serializer for Redis storage.
Engine objects contain dataclass instances — serialize to plain dicts,
reconstruct on deserialization. Called every turn.
"""
from dataclasses import asdict
from .types import Entity, WorldState, Memory, ContentFilter, FilterState


def serialize_engine(engine) -> dict:
    """Convert live Engine object to JSON-serializable dict."""
    return {
        'entities': [asdict(e) for e in engine.entities],
        'world':    asdict(engine.world),
        'memory':   asdict(engine.memory),
        'layer1':   engine.layer1,
        'history':         engine.history,
        'display_history': getattr(engine, 'display_history', []),
        'turn':     engine.turn,
        'scene_lang':       engine.scene_lang,
        'last_target':      engine.last_target,
        'sp_note':          engine._sp_note,
        'char_personality': engine._char_personality,
        'filter_state':     engine.filter.state.value,
        'filter_lock':      engine.filter.parental_lock,
        'metrics':          engine.metrics,
    }


def deserialize_engine(data: dict):
    """Reconstruct Engine state from Redis dict without re-running init."""
    import threading
    from .engine import Engine

    entities = []
    for ed in data['entities']:
        e = Entity(**{k: v for k, v in ed.items()})
        entities.append(e)

    world  = WorldState(**data['world'])
    memory = Memory(**data['memory'])

    content_filter = ContentFilter(
        state=FilterState(data.get('filter_state', 'off')),
        parental_lock=data.get('filter_lock', False),
    )

    persona   = next((e for e in entities if e.is_player), None)
    main_char = next((e for e in entities if not e.is_player and not e.is_collective), None)
    if persona is None or main_char is None:
        raise ValueError(f'Corrupted engine state: missing {"player" if persona is None else "NPC"} entity')

    # Build a shell engine without running __init__
    engine = Engine.__new__(Engine)
    engine.persona           = persona
    engine.world             = world
    engine.memory            = memory
    engine.layer1            = data['layer1']
    engine.history           = data['history']
    engine.display_history   = data.get('display_history', [])
    engine.turn              = data['turn']
    engine.scene_lang        = data.get('scene_lang', 'English')
    engine.last_target       = data.get('last_target', main_char.name)
    engine._sp_note          = data.get('sp_note', '')
    engine._char_personality = data.get('char_personality', '')
    engine.filter            = content_filter
    engine.metrics           = data.get('metrics', {'turns': 0, 'sv_violations': 0, 'latencies': [], 'compressions': 0})
    engine.entities          = entities
    engine._lock             = threading.Lock()

    return engine
