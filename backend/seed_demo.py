"""Seed one ready-to-play public portfolio scenario into the database.

Its prefab is a captured, versioned result of the real engine initialization,
so visitors can start immediately without spending Groq tokens on boot.
"""
import asyncio
import json
import logging
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from auth import hash_password
from database import AsyncSessionLocal, close_connections
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
def _prefab_state() -> dict:
    """Load the versioned prefab captured from the real engine initialization."""
    path = Path(__file__).with_name("demo_prefab.json")
    with path.open(encoding="utf-8") as prefab_file:
        return json.load(prefab_file)


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

        scenario_values = dict(
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
        )
        await db.execute(pg_insert(Scenario).values(**scenario_values).on_conflict_do_update(
            index_elements=[Scenario.id],
            set_={key: value for key, value in scenario_values.items() if key != "id"},
        ))
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
