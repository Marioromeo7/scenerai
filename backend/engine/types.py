from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Entity:
    name:             str   = ''
    pronouns:         str   = ''
    role:             str   = ''
    appearance:       str   = ''
    physical_state:   str   = ''
    mood:             str   = 'neutral'
    location:         str   = 'unknown'
    doing:            str   = 'nothing in particular'
    present:          bool  = True
    is_player:        bool  = False
    weight:           float = 1.0
    emotional_state:  str   = ''
    history:          list  = field(default_factory=list)
    saved_assumptions: list = field(default_factory=list)
    behavioral_read:  str   = ''
    never_does:       str   = ''
    shy:              bool  = False
    is_collective:    bool  = False
    collective_of:    list  = field(default_factory=list)

    def block(self, full=True) -> str:
        player    = '  ← PLAYER — narrative camera. Never act/speak/feel as them.' if self.is_player else ''
        presence  = 'present' if self.present else 'NOT in scene'
        lines = [
            f'  [{self.name}] ({self.pronouns}) | {presence}{player}',
            f'    Role           : {self.role}',
        ]
        if self.appearance:
            lines.append(f'    Appearance     : {self.appearance}')
        if self.physical_state:
            lines.append(f'    Physical state : {self.physical_state}')
        if self.saved_assumptions:
            lines.append(f'    Saved details  : {", ".join(self.saved_assumptions)}')
        if full:
            lines.append(f'    Mood           : {self.mood}')
            lines.append(f'    Location       : {self.location}')
            lines.append(f'    Doing          : {self.doing}')
            if self.emotional_state:
                lines.append(f'    Emotional      : {self.emotional_state}')
            if self.history:
                lines.append(f'    History        : {"|  ".join(self.history[-3:])}')
        return '\n'.join(lines)


@dataclass
class WorldState:
    location:    str = ''
    time_of_day: str = ''
    era:         str = ''
    atmosphere:  str = ''
    flags:       list = field(default_factory=list)

    def block(self) -> str:
        return (
            f'  Location   : {self.location}\n'
            f'  Time       : {self.time_of_day}\n'
            f'  Era        : {self.era}\n'
            f'  Atmosphere : {self.atmosphere}\n'
            f'  Flags      : {", ".join(self.flags) if self.flags else "none"}'
        )


@dataclass
class Memory:
    pinned:    list = field(default_factory=list)
    summaries: list = field(default_factory=list)

    def block(self) -> str:
        lines = []
        if self.summaries:
            lines.append('SUMMARIES (compressed history):')
            for s in self.summaries[-3:]:
                lines.append(f'  {s}')
        if self.pinned:
            lines.append('PINNED FACTS (never forget):')
            for f in self.pinned:
                lines.append(f'  • {f}')
        return '\n'.join(lines) if lines else '(empty)'


class FilterState(Enum):
    OFF   = 'off'
    ON    = 'on'
    FORCE = 'force'


class ContentFilter:
    def __init__(self, state: FilterState = FilterState.OFF, parental_lock: bool = False):
        self.state         = state
        self.parental_lock = parental_lock

    @property
    def is_active(self):
        return self.state != FilterState.OFF

    def activate(self, entities):
        self.state = FilterState.ON
        for e in entities:
            e.shy = True

    def deactivate(self, entities):
        if self.parental_lock:
            return False
        self.state = FilterState.OFF
        for e in entities:
            e.shy = False
        return True

    def prompt_block(self):
        if self.state == FilterState.OFF:
            return ''
        return (
            "CONTENT FILTER: ACTIVE\n"
            "  Sexual content, explicit nudity, and graphic intimacy are not rendered.\n"
            "  Fade to black when scenes move toward explicit territory.\n"
        )


@dataclass
class Scenario:
    char_name:        str
    char_pronouns:    str
    char_title:       str
    char_personality: str
    greeting:         str
    player_name:      str
    player_pronouns:  str
    content_filter:   object = None
    scene_lang:       str    = None
