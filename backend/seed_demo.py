"""Seed one ready-to-play public portfolio scenario into a fresh database.

The demo prefab is authored as a small, versioned engine snapshot so the
scenario is playable immediately and boot never spends Groq tokens creating
seed data. It follows engine.serializer.serialize_engine's JSON shape.
"""
import asyncio
import logging
import secrets

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from auth import hash_password
from database import AsyncSessionLocal, close_connections
from engine.types import Entity, Memory, WorldState
from models import Scenario, User

logger = logging.getLogger(__name__)

DEMO_OWNER_ID = "00000000-0000-4000-8000-000000000001"
DEMO_SCENARIO_ID = "00000000-0000-4000-8000-000000000002"
DEMO_EMAIL = "portfolio-scenario-owner@example.invalid"

TITLE = "The Quiet Frequency"
CHAR_NAME = "Mara Venn"
CHAR_PRONOUNS = "she/her"
CHAR_TITLE = "Relay engineer and former survey-ship officer"
CHAR_PERSONALITY = (
    "Mara is precise, dry-witted, and careful with every claim. She is loyal to the people "
    "in her care, but distrustful of institutions that call secrecy a form of protection. "
    "Six years ago she served aboard the survey ship Meridian and has never explained why "
    "she left before its final voyage. She wants to understand the new signal without "
    "letting fear decide what the crew does next. She will not knowingly endanger someone "
    "just to protect her own reputation."
)
GREETING = (
    "The relay room shivers as a storm leans against the station. Mara Venn sets a battered "
    "flight recorder beside the dark console; its red indicator blinks although the power bus "
    "is cut. The signal carries the call sign of the survey ship Meridian, lost six years ago. "
    "Mara watches the waveform climb. 'That ship went quiet before I left it,' she says. 'This "
    "part is new.' A voice crackles through the speaker, older and unmistakably yours: 'Do not "
    "open the lower hatch.' Behind the sealed hatch, metal turns once in its frame. Mara keeps "
    "her hands away from the controls. 'I can kill the relay, or we can find out who sent that. "
    "Your call.'"
)
LAYER1 = (
    "A winter storm has isolated the Kestrel relay station on a remote moon. Mara Venn is its "
    "relay engineer and the only other person currently in the control room. She once served "
    "aboard the survey ship Meridian, which vanished six years ago; she has admitted only that "
    "she left before its final voyage. A newly received transmission uses Meridian's call sign "
    "and contains a recording of the player's voice warning against opening the lower hatch. "
    "The hatch has moved from the other side, but neither Mara nor the player has opened it. "
    "Mara wants to identify the signal and keep the station safe; she is withholding some of her "
    "history, but has not explained why. The player controls every choice and action for their "
    "own character."
)


def _prefab_state() -> dict:
    player = Entity(
        name="Player", pronouns="they/them", is_player=True,
        location="Kestrel relay control room", doing="uncommitted",
    )
    mara = Entity(
        name=CHAR_NAME,
        pronouns=CHAR_PRONOUNS,
        role=CHAR_TITLE,
        appearance=(
            "A lean woman in her late thirties with close-cropped dark hair, a weatherproof "
            "station jacket, and a thin burn scar along her left jaw."
        ),
        physical_state="Alert, with a faint tremor in her left hand",
        mood="guarded and analytical",
        location="Kestrel relay control room, beside the console",
        doing="watching the signal trace without touching the controls",
        emotional_state="uneasy about the Meridian call sign",
        behavioral_read=(
            "Mara uses precision to keep fear from becoming a decision. She tests claims against "
            "what the instruments actually show. She protects people by giving them choices, "
            "even when she would prefer to decide for them. She is ashamed of leaving the "
            "Meridian before its last voyage, but will not invent an explanation to make herself "
            "look better. Under pressure, Mara always checks who might be harmed before she "
            "touches a control."
        ),
        never_does="Mara would never knowingly endanger another person to protect her reputation.",
    )
    opening_message = {"role": "assistant", "content": GREETING}
    return {
        "entities": [player.__dict__, mara.__dict__],
        "world": WorldState(
            location="Kestrel relay station control room, remote moon",
            time_of_day="station night",
            era="near-future space exploration",
            atmosphere="isolated, storm-bound, watchful",
            flags=["Meridian signal", "sealed lower hatch", "storm isolation"],
        ).__dict__,
        "memory": Memory(pinned=[
            "The Meridian survey ship vanished six years ago.",
            "Mara served aboard the Meridian and left before its final voyage.",
            "The new signal carries the Meridian call sign and a recording of the player's voice.",
            "The lower hatch has moved, but it remains sealed.",
            "Mara offered to let the player decide whether to kill the relay or investigate.",
        ]).__dict__,
        "layer1": LAYER1,
        "history": [opening_message],
        "display_history": [opening_message],
        "turn": 0,
        "scene_lang": "English",
        "last_target": CHAR_NAME,
        "sp_note": "",
        "char_personality": CHAR_PERSONALITY,
        "filter_state": "off",
        "filter_lock": False,
        "metrics": {"turns": 0, "sv_violations": 0, "latencies": [], "compressions": 0},
    }


async def seed_demo_scenario() -> None:
    async with AsyncSessionLocal() as db:
        # A reserved, non-loginable owner is required by Scenario.creator_id.
        # The random password is only stored as a bcrypt hash and is never logged.
        await db.execute(pg_insert(User).values(
            id=DEMO_OWNER_ID,
            email=DEMO_EMAIL,
            hashed_password=hash_password(secrets.token_urlsafe(48)),
            is_admin=False,
        ).on_conflict_do_nothing())

        await db.execute(pg_insert(Scenario).values(
            id=DEMO_SCENARIO_ID,
            creator_id=DEMO_OWNER_ID,
            char_name=CHAR_NAME,
            char_pronouns=CHAR_PRONOUNS,
            char_title=CHAR_TITLE,
            char_personality=CHAR_PERSONALITY,
            greeting=GREETING,
            title=TITLE,
            brief=(
                "A storm-bound relay station receives a voice from a lost ship—and a warning "
                "spoken in the player's own voice. A quiet sci-fi mystery about trust, memory, "
                "and what to do when the past answers back."
            ),
            tags=["science fiction", "mystery", "suspense", "character drama"],
            intensity=2,
            image_seed="quiet-frequency",
            is_public=True,
            is_published=True,
            prefab_engine_state=_prefab_state(),
            prefab_status="ready",
        ).on_conflict_do_nothing())
        await db.commit()

        scenario = (await db.execute(
            select(Scenario).where(Scenario.id == DEMO_SCENARIO_ID)
        )).scalar_one()
        if scenario.prefab_status == "ready" and scenario.prefab_engine_state:
            logger.info("Portfolio demo scenario is present and prefab-ready")
        else:
            logger.warning("Portfolio demo scenario exists but is not prefab-ready")


async def main() -> None:
    try:
        await seed_demo_scenario()
    finally:
        await close_connections()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
