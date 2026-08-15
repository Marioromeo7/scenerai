from sqlalchemy import Column, String, Boolean, Integer, Float, DateTime, ForeignKey, Text, JSON, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import uuid


def new_uuid(): return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id              = Column(String, primary_key=True, default=new_uuid)
    email           = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    is_admin        = Column(Boolean, default=False, nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    personas        = relationship("Persona",     back_populates="user", cascade="all, delete-orphan")
    scenarios       = relationship("Scenario",    back_populates="creator", cascade="all, delete-orphan")
    saves           = relationship("ScenarioSave", back_populates="user", cascade="all, delete-orphan")
    session_logs    = relationship("SessionLog",  back_populates="user", cascade="all, delete-orphan")


class Persona(Base):
    __tablename__ = "personas"
    id          = Column(String, primary_key=True, default=new_uuid)
    user_id     = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name        = Column(String, nullable=False)
    pronouns    = Column(String, nullable=False, default="she/her")
    brief       = Column(Text, default="")
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    user        = relationship("User", back_populates="personas")
    session_logs = relationship("SessionLog", back_populates="persona")


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        # Public browse: WHERE is_public = TRUE ORDER BY created_at DESC
        Index('ix_scenarios_public_created', 'is_public', 'created_at'),
        # Creator list with cursor pagination: WHERE creator_id = ? AND created_at < ? ORDER BY created_at DESC
        Index('ix_scenarios_creator_created', 'creator_id', 'created_at'),
    )
    id               = Column(String, primary_key=True, default=new_uuid)
    creator_id       = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    char_name        = Column(String, nullable=False)
    char_pronouns    = Column(String, nullable=False, default="he/him")
    char_title       = Column(String, nullable=False)
    char_personality = Column(Text, nullable=False)
    greeting         = Column(Text, nullable=False)
    title            = Column(String, default="")
    brief            = Column(Text, default="")
    tags             = Column(JSON, default=list)
    intensity        = Column(Integer, default=3)
    image_seed       = Column(String, default="")
    saves_count      = Column(Integer, default=0)
    plays_count      = Column(Integer, default=0)
    is_public            = Column(Boolean, default=True)
    is_published         = Column(Boolean, default=False, nullable=False)
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    prefab_engine_state  = Column(JSON, nullable=True, default=None)
    # 'pending' while the background prefab job hasn't finished, 'ready' once
    # prefab_engine_state is populated, 'failed' if the job errored out or was
    # skipped under the concurrency gate — surfaced so publish failures aren't silent.
    prefab_status        = Column(String, nullable=False, default="pending", server_default="pending")
    # Section 4.1 of the plan: a compact structured descriptor (hair, eyes,
    # build, clothing, palette, art_style, distinguishing_feature), not
    # prose — the image-generation prompt is built by template-formatting
    # these fields, zero LLM calls per image. visual_seed is the fixed
    # numeric seed paired with it for deterministic reconstruction; separate
    # from the pre-existing image_seed (a placeholder-gradient string, not
    # a real generation seed — left alone, still used by the frontend's
    # CardImage placeholder until real generation replaces it).
    visual_profile       = Column(JSON, nullable=True, default=None)
    visual_seed          = Column(Integer, nullable=True, default=None)
    # Same tri-state pattern as prefab_status, for the same reason: a failed
    # or still-running image/video generation job must be visible, not silent.
    visual_status        = Column(String, nullable=False, default="pending", server_default="pending")
    image_url            = Column(String, nullable=True, default=None)
    video_url            = Column(String, nullable=True, default=None)
    creator              = relationship("User", back_populates="scenarios")
    saves            = relationship("ScenarioSave", back_populates="scenario", cascade="all, delete-orphan")
    session_logs     = relationship("SessionLog", back_populates="scenario")


class ScenarioSave(Base):
    __tablename__ = "scenario_saves"
    __table_args__ = (UniqueConstraint("user_id", "scenario_id"),)
    id          = Column(String, primary_key=True, default=new_uuid)
    user_id     = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    saved_at    = Column(DateTime(timezone=True), server_default=func.now())
    user        = relationship("User", back_populates="saves")
    scenario    = relationship("Scenario", back_populates="saves")


class SessionLog(Base):
    __tablename__ = "session_logs"
    __table_args__ = (
        # Session history cursor pagination: WHERE user_id = ? AND started_at < ? ORDER BY started_at DESC
        Index('ix_session_logs_user_started', 'user_id', 'started_at'),
        # Last-session lookup on session start: WHERE user_id = ? AND scenario_id = ? ORDER BY started_at DESC
        Index('ix_session_logs_user_scenario_started', 'user_id', 'scenario_id', 'started_at'),
    )
    id           = Column(String, primary_key=True, default=new_uuid)
    session_id   = Column(String, unique=True, nullable=False, index=True)
    user_id      = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_id  = Column(String, ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True)
    persona_id   = Column(String, ForeignKey("personas.id", ondelete="SET NULL"), nullable=True)
    history      = Column(JSON, default=list)
    turns_count  = Column(Integer, default=0)
    started_at   = Column(DateTime(timezone=True), server_default=func.now())
    ended_at     = Column(DateTime(timezone=True), nullable=True)
    engine_state = Column(JSON, default=None)
    user         = relationship("User", back_populates="session_logs")
    scenario     = relationship("Scenario", back_populates="session_logs")
    persona      = relationship("Persona", back_populates="session_logs")


class SubscriptionTier(Base):
    """Pricing tiers — informational + gates feature access, no payment
    processing lives here at all. Seeded via a migration data-insert, not
    hardcoded in application code, so tiers can be edited without a deploy."""
    __tablename__ = "subscription_tiers"
    id            = Column(String, primary_key=True, default=new_uuid)
    name          = Column(String, nullable=False)
    price_cents   = Column(Integer, nullable=False, default=0)
    currency      = Column(String, default="usd")
    features      = Column(JSON, default=list)
    turns_per_day = Column(Integer, nullable=True)  # None = unlimited
    sort_order    = Column(Integer, default=0)


class UserSubscription(Base):
    """Deliberately named/shaped so a real Stripe integration later is a
    swap, not a rewrite: is_mock flips to False and stripe_subscription_id
    gets populated, the rest of the app (tier-gating checks) doesn't change.
    No card data, no payment fields at all — this table only ever records
    which tier a user is on and whether that came from a real charge."""
    __tablename__ = "user_subscriptions"
    id                       = Column(String, primary_key=True, default=new_uuid)
    user_id                  = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    tier_id                  = Column(String, ForeignKey("subscription_tiers.id"), nullable=False)
    status                   = Column(String, default="active")  # active | cancelled
    is_mock                  = Column(Boolean, default=True, nullable=False)
    stripe_subscription_id   = Column(String, nullable=True)  # populated only once a real processor exists
    started_at               = Column(DateTime(timezone=True), server_default=func.now())
    updated_at               = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    user                     = relationship("User")
    tier                     = relationship("SubscriptionTier")


class TurnMedia(Base):
    """Per-turn generated image/audio/video-segment for the progressive
    "movie" feature. Same no-FK-to-session_logs pattern as TurnRating, for
    the same reason: preview sessions never write a SessionLog row but
    still generate media. status lets the frontend distinguish "still
    generating" from "failed" from "ready" rather than polling blind.
    image_seed is kept so a regenerate can pick a genuinely different seed
    rather than silently reusing the one that already failed."""
    __tablename__ = "turn_media"
    __table_args__ = (UniqueConstraint("session_id", "turn"),)
    id                 = Column(String, primary_key=True, default=new_uuid)
    user_id            = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id         = Column(String, nullable=False, index=True)
    scenario_id        = Column(String, ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True)
    turn               = Column(Integer, nullable=False)
    status             = Column(String, default="pending")  # pending | ready | failed
    image_url          = Column(String, nullable=True)
    image_seed         = Column(Integer, nullable=True)
    audio_url          = Column(String, nullable=True)
    video_segment_url  = Column(String, nullable=True)
    error              = Column(String, nullable=True)
    # Stored so /regenerate-media can reuse them directly with a new seed,
    # rather than reconstructing from session history (turn number -> history
    # index isn't a trivial mapping, and the session may have moved on).
    image_prompt       = Column(Text, nullable=True)
    narration_text     = Column(Text, nullable=True)
    voice_id           = Column(String, nullable=True)
    voice_speed        = Column(Float, nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    updated_at         = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    user               = relationship("User")


class TurnRating(Base):
    """User feedback on a single turn's output — thumbs/stars, not tied to
    the judge harness (docs/eval-runs/) which scores separately with an LLM.
    No FK to session_logs: preview sessions never write a SessionLog row
    (see main.py _upsert_session_log), but should still be ratable."""
    __tablename__ = "turn_ratings"
    __table_args__ = (UniqueConstraint("user_id", "session_id", "turn"),)
    id          = Column(String, primary_key=True, default=new_uuid)
    user_id     = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id  = Column(String, nullable=False, index=True)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True)
    turn        = Column(Integer, nullable=False)
    rating      = Column(Integer, nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    user        = relationship("User")
