from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, Text, JSON, UniqueConstraint, Index
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
