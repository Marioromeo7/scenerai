from pydantic import BaseModel, EmailStr, Field, computed_field
from typing import Any, List, Literal, Optional
from datetime import datetime


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: str; email: str; created_at: datetime; is_admin: bool = False
    class Config: from_attributes = True

class Token(BaseModel):
    access_token: str; token_type: str = "bearer"; refresh_token: str; user: UserOut

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str


class PersonaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    pronouns: str = Field(default="she/her", max_length=20)
    brief: str = Field(default="", max_length=500)

class PersonaOut(BaseModel):
    id: str; user_id: str; name: str; pronouns: str; brief: str; created_at: datetime
    class Config: from_attributes = True

class PersonaUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    pronouns: Optional[str] = Field(None, max_length=20)
    brief: Optional[str] = Field(None, max_length=500)


class ScenarioCreate(BaseModel):
    char_name: str = Field(min_length=1, max_length=100)
    char_pronouns: str = Field(default="he/him", max_length=20)
    char_title: str = Field(min_length=1, max_length=200)
    char_personality: str = Field(min_length=1, max_length=5000)
    greeting: str = Field(min_length=1, max_length=10000)

class ScenarioOut(BaseModel):
    id: str; creator_id: str; char_name: str; char_pronouns: str
    char_title: str; char_personality: str; greeting: str
    title: str; brief: str; tags: List[str]; intensity: int
    image_seed: str; saves_count: int; plays_count: int
    is_public: bool; is_published: bool; created_at: datetime
    prefab_status: Literal["pending", "ready", "failed"] = "pending"
    prefab_engine_state: Optional[Any] = Field(None, exclude=True)
    # visual_profile/visual_seed are internal generation inputs, not exposed
    # (same reasoning as prefab_engine_state) — image_url/video_url/
    # visual_status are the only parts a client needs.
    visual_status: Literal["pending", "ready", "failed"] = "pending"
    image_url: Optional[str] = None
    video_url: Optional[str] = None

    @computed_field
    @property
    def prefab_ready(self) -> bool:
        return self.prefab_engine_state is not None

    class Config: from_attributes = True

class ScenarioUpdate(BaseModel):
    char_name: Optional[str] = Field(None, max_length=100)
    char_pronouns: Optional[str] = Field(None, max_length=20)
    char_title: Optional[str] = Field(None, max_length=200)
    char_personality: Optional[str] = Field(None, max_length=5000)
    greeting: Optional[str] = Field(None, max_length=10000)


class SessionCreate(BaseModel):
    scenario_id: str
    persona_id: str
    content_filter: Literal["off", "on", "force"] = "off"
    preview: bool = False

class SessionOut(BaseModel):
    session_id: str; scenario_id: str; persona_id: str; started_at: datetime

class PlayTurnWithModel(BaseModel):
    input: str = Field(min_length=1, max_length=2000); engine_model: Optional[str] = None

class ContinueTurnRequest(BaseModel):
    engine_model: Optional[str] = None

class RegenerateRequest(BaseModel):
    engine_model: Optional[str] = None

class PlayResponse(BaseModel):
    response: str; session_id: str; turn: int
    sovereign: bool; violations: List[Any]
    is_continuation: bool = False
    is_regeneration: bool = False

class TurnRatingCreate(BaseModel):
    rating: int = Field(ge=1, le=5)

class TurnRatingOut(BaseModel):
    session_id: str; turn: int; rating: int
    class Config: from_attributes = True

class ExportRequest(BaseModel):
    from_turn: int = Field(ge=1)
    to_turn: int = Field(ge=1)

class ExportOut(BaseModel):
    download_url: str
    turns: List[int]

class SessionMovieOut(BaseModel):
    available: bool
    url: Optional[str] = None

class TurnMediaOut(BaseModel):
    turn: int; status: str
    image_url: Optional[str]; audio_url: Optional[str]; video_segment_url: Optional[str]
    error: Optional[str]
    class Config: from_attributes = True

class SessionLogOut(BaseModel):
    id: str; session_id: str; scenario_id: Optional[str]
    persona_id: Optional[str]; turns_count: int
    started_at: datetime; ended_at: Optional[datetime]
    thumbnail_url: Optional[str] = None
    class Config: from_attributes = True


class TelemetryOut(BaseModel):
    groq_tpm_used: int
    groq_tpm_limit: int
    active_sessions: int
    prefab_jobs_running: int
    prefab_jobs_max: int
    total_scenarios: int
    total_plays: int
    turn_media_by_status: dict

class SubscriptionTierOut(BaseModel):
    id: str; name: str; price_cents: int; currency: str
    features: List[str]; turns_per_day: Optional[int]; sort_order: int
    class Config: from_attributes = True

class UserSubscriptionOut(BaseModel):
    tier_id: str; status: str; is_mock: bool; started_at: datetime
    class Config: from_attributes = True


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

class ScenarioReport(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
