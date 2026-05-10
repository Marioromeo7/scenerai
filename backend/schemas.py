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
    id: str; email: str; created_at: datetime
    class Config: from_attributes = True

class Token(BaseModel):
    access_token: str; token_type: str = "bearer"; user: UserOut


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
    prefab_engine_state: Optional[Any] = Field(None, exclude=True)

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

class PlayResponse(BaseModel):
    response: str; session_id: str; turn: int
    sovereign: bool; violations: List[Any]

class SessionLogOut(BaseModel):
    id: str; session_id: str; scenario_id: Optional[str]
    persona_id: Optional[str]; turns_count: int
    started_at: datetime; ended_at: Optional[datetime]
    class Config: from_attributes = True


class PaginatedResponse(BaseModel):
    items: list
    next_cursor: Optional[str] = None
    has_more: bool = False


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

class ScenarioReport(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
