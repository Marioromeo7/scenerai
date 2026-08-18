import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from typing import List, Optional
import uuid
import json
import os

from arq import create_pool
from arq.connections import RedisSettings

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import settings
from database import get_db, get_redis, close_connections, AsyncSessionLocal
from models import (
    User, Persona, Scenario, ScenarioSave, SessionLog, TurnRating, TurnMedia, SubscriptionTier, UserSubscription,
)
from schemas import (
    UserCreate, UserLogin, UserOut, Token, RefreshRequest, LogoutRequest,
    PersonaCreate, PersonaOut, PersonaUpdate,
    ScenarioCreate, ScenarioOut, ScenarioUpdate,
    SessionCreate, SessionOut,
    PlayTurnWithModel, ContinueTurnRequest, RegenerateRequest, PlayResponse,
    TurnRatingCreate, TurnRatingOut, TurnMediaOut, ExportRequest, ExportOut, SessionMovieOut,
    SessionLogOut,
    PasswordChange, ScenarioReport,
    SubscriptionTierOut, UserSubscriptionOut, TelemetryOut,
)
from auth import hash_password, verify_password, create_token, get_current_user, require_admin
from user_rate_limit import enforce_user_rate_limit
from refresh_tokens import create_refresh_token, rotate_refresh_token, revoke_refresh_token
from error_monitoring import RequestIDMiddleware, unhandled_exception_handler
from ai_service import (
    generate_scenario_metadata, engine_step, engine_step_stream, engine_regenerate,
    ENGINE_MODELS, DEFAULT_ENGINE_MODEL,
)
from engine.inference import sanitize_input
from video_utils import concat_videos

SESSION_TTL = 60 * 60 * 12  # 12 hours


def get_real_client_ip(request: Request) -> str:
    """slowapi's default get_remote_address reads request.client.host,
    which for every request through nginx -- the app's primary/only
    production path -- is nginx's own container IP, not the real client.
    Found live via code review: every @limiter.limit(...) route was
    effectively a platform-wide limit in disguise (one abusive client
    could 429-lock out everyone) rather than a per-client one. nginx
    already sets X-Real-IP to the true connecting IP (see nginx.conf);
    trust it when present, fall back to the raw connection for direct/dev
    access that bypasses nginx."""
    real_ip = request.headers.get("x-real-ip")
    return real_ip or get_remote_address(request)


limiter = Limiter(key_func=get_real_client_ip)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic (runs in Dockerfile before uvicorn starts).
    # create_all is intentionally removed to avoid conflicts with migration tracking.
    if settings.jwt_secret == "dev_secret":
        logger.warning("SECURITY: jwt_secret is using the insecure default. "
                       "Set JWT_SECRET in .env before any non-local deployment.")
    if "changeme" in settings.database_url:
        logger.warning("SECURITY: database_url contains the default 'changeme' password. Set DATABASE_URL in .env.")
    if not settings.groq_api_key:
        logger.warning("SECURITY: GROQ_API_KEY is not set — all LLM calls will fail.")
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.arq_pool.close()
    await close_connections()


app = FastAPI(title="Scenarai API", version="2.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded. Please slow down."})


app.add_exception_handler(Exception, unhandled_exception_handler)


# ── Models ────────────────────────────────────────────────────
@app.get("/models")
async def list_engine_models():
    return [{"id": k, "label": v} for k, v in ENGINE_MODELS.items()]


# ── Auth ──────────────────────────────────────────────────────
@app.post("/auth/register", response_model=Token, status_code=201)
@limiter.limit("5/minute")
async def register(request: Request, body: UserCreate, db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none():
        raise HTTPException(409, "Email already registered")
    user = User(id=str(uuid.uuid4()), email=body.email, hashed_password=hash_password(body.password))
    db.add(user); await db.commit(); await db.refresh(user)
    redis = await get_redis()
    refresh_token = await create_refresh_token(redis, user.id)
    return Token(access_token=create_token(user.id), refresh_token=refresh_token, user=UserOut.model_validate(user))

@app.post("/auth/login", response_model=Token)
@limiter.limit("10/minute")
async def login(request: Request, body: UserLogin, db: AsyncSession = Depends(get_db)):
    u = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if not u or not verify_password(body.password, u.hashed_password):
        raise HTTPException(401, "Invalid credentials")
    redis = await get_redis()
    refresh_token = await create_refresh_token(redis, u.id)
    return Token(access_token=create_token(u.id), refresh_token=refresh_token, user=UserOut.model_validate(u))

@app.post("/auth/refresh", response_model=Token)
@limiter.limit("20/minute")
async def refresh(request: Request, body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchanges a still-valid refresh token for a new access token, and
    rotates the refresh token itself (old one is retired, a new one is
    issued) -- see refresh_tokens.py for why rotation, not reuse."""
    redis = await get_redis()
    result = await rotate_refresh_token(redis, body.refresh_token)
    if result is None:
        raise HTTPException(401, "Invalid or expired refresh token")
    user_id, new_refresh_token = result
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(401, "User not found")
    return Token(access_token=create_token(u.id), refresh_token=new_refresh_token, user=UserOut.model_validate(u))

@app.post("/auth/logout", status_code=204)
async def logout(body: LogoutRequest):
    """Revokes the refresh token -- this device/session can no longer
    mint new access tokens. Its current access token (if any) stays
    valid until its own short expiry runs out; that bounded window is
    the tradeoff a stateless JWT makes, see config.py's jwt_expire_minutes."""
    redis = await get_redis()
    await revoke_refresh_token(redis, body.refresh_token)

@app.get("/auth/me", response_model=UserOut)
async def me(cu: User = Depends(get_current_user)):
    return UserOut.model_validate(cu)

@app.post("/auth/change-password", status_code=204)
@limiter.limit("5/minute")
async def change_password(request: Request, body: PasswordChange, db: AsyncSession = Depends(get_db),
                           cu: User = Depends(get_current_user)):
    if not verify_password(body.current_password, cu.hashed_password):
        raise HTTPException(401, "Current password is incorrect")
    cu.hashed_password = hash_password(body.new_password)
    await db.commit()

@app.delete("/auth/me", status_code=204)
@limiter.limit("3/minute")
async def delete_account(request: Request, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    await db.delete(cu)
    await db.commit()


# ── Personas ──────────────────────────────────────────────────
@app.post("/personas", response_model=PersonaOut, status_code=201)
async def create_persona(body: PersonaCreate, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    p = Persona(id=str(uuid.uuid4()), user_id=cu.id, name=body.name, pronouns=body.pronouns, brief=body.brief)
    db.add(p); await db.commit(); await db.refresh(p)
    return PersonaOut.model_validate(p)

@app.get("/personas")
async def list_personas(cursor: str = None, limit: int = 20, db: AsyncSession = Depends(get_db),
                         cu: User = Depends(get_current_user)):
    limit = min(max(limit, 1), 50)
    query = select(Persona).where(Persona.user_id == cu.id).order_by(Persona.created_at.desc())
    if cursor:
        try:
            cursor_time = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid cursor value")
        query = query.where(Persona.created_at < cursor_time)
    query = query.limit(limit + 1)
    r = await db.execute(query)
    items = r.scalars().all()
    has_more = len(items) > limit
    items = items[:limit]
    next_cursor = items[-1].created_at.isoformat() if has_more and items else None
    return {"items": [PersonaOut.model_validate(p) for p in items], "next_cursor": next_cursor, "has_more": has_more}

@app.put("/personas/{pid}", response_model=PersonaOut)
async def update_persona(pid: str, body: PersonaUpdate, db: AsyncSession = Depends(get_db),
                          cu: User = Depends(get_current_user)):
    p = (await db.execute(select(Persona).where(Persona.id == pid, Persona.user_id == cu.id))).scalar_one_or_none()
    if not p: raise HTTPException(404, "Persona not found")
    if body.name is not None: p.name = body.name
    if body.pronouns is not None: p.pronouns = body.pronouns
    if body.brief is not None: p.brief = body.brief
    await db.commit(); await db.refresh(p)
    return PersonaOut.model_validate(p)

@app.delete("/personas/{pid}", status_code=204)
async def delete_persona(pid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    p = (await db.execute(select(Persona).where(Persona.id == pid, Persona.user_id == cu.id))).scalar_one_or_none()
    if not p: raise HTTPException(404, "Persona not found")
    await db.delete(p); await db.commit()


# ── Scenarios ─────────────────────────────────────────────────

@app.post("/scenarios", response_model=ScenarioOut, status_code=201)
@limiter.limit("10/minute")
async def create_scenario(request: Request, body: ScenarioCreate, db: AsyncSession = Depends(get_db),
                           cu: User = Depends(get_current_user)):
    char_personality = sanitize_input(body.char_personality)
    greeting         = sanitize_input(body.greeting)
    meta = await generate_scenario_metadata(body.char_name, body.char_title, char_personality, greeting)
    s = Scenario(
        id=str(uuid.uuid4()), creator_id=cu.id,
        char_name=body.char_name, char_pronouns=body.char_pronouns,
        char_title=body.char_title, char_personality=char_personality,
        greeting=greeting,
        title=meta.get("title", body.char_name),
        brief=meta.get("brief", body.char_title),
        tags=meta.get("tags", []),
        intensity=meta.get("intensity", 3),
        image_seed=str(uuid.uuid4())[:8],
    )
    db.add(s); await db.commit(); await db.refresh(s)
    return ScenarioOut.model_validate(s)


@app.get("/scenarios")
async def list_scenarios(cursor: str = None, limit: int = 20, db: AsyncSession = Depends(get_db),
                          cu: User = Depends(get_current_user)):
    limit = min(max(limit, 1), 50)
    query = select(Scenario).where(Scenario.creator_id == cu.id).order_by(Scenario.created_at.desc())
    if cursor:
        try:
            cursor_time = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid cursor value")
        query = query.where(Scenario.created_at < cursor_time)
    query = query.limit(limit + 1)
    r = await db.execute(query)
    items = r.scalars().all()
    has_more = len(items) > limit
    items = items[:limit]
    next_cursor = items[-1].created_at.isoformat() if has_more and items else None
    return {"items": [ScenarioOut.model_validate(s) for s in items], "next_cursor": next_cursor, "has_more": has_more}

@app.get("/scenarios/public")
async def list_public(cursor: str = None, limit: int = 20, db: AsyncSession = Depends(get_db)):
    limit = min(max(limit, 1), 50)
    query = select(Scenario).where(
        Scenario.is_public == True,  # noqa: E712 — SQLAlchemy ORM expression
        Scenario.is_published == True,  # noqa: E712
    ).order_by(Scenario.created_at.desc())
    if cursor:
        try:
            cursor_time = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid cursor value")
        query = query.where(Scenario.created_at < cursor_time)
    query = query.limit(limit + 1)
    r = await db.execute(query)
    items = r.scalars().all()
    has_more = len(items) > limit
    items = items[:limit]
    next_cursor = items[-1].created_at.isoformat() if has_more and items else None
    return {"items": [ScenarioOut.model_validate(s) for s in items], "next_cursor": next_cursor, "has_more": has_more}

@app.get("/scenarios/saved-ids")
async def get_saved_scenario_ids(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """The frontend's savedIds state has no other way to know which
    scenarios a user saved in a previous session -- without this, every
    fresh page load silently shows every scenario as unsaved even though
    ScenarioSave rows are still there. A plain id list (not full
    ScenarioOut objects) since that's all the star-icon UI needs.

    Must be registered before /scenarios/{sid} below -- found live: FastAPI
    matches routes in registration order, so with this route after the
    {sid} one, a request for /scenarios/saved-ids was being swallowed by
    get_scenario with sid='saved-ids' instead, returning a 404."""
    rows = (await db.execute(select(ScenarioSave.scenario_id).where(ScenarioSave.user_id == cu.id))).scalars().all()
    return {"ids": list(rows)}

@app.get("/scenarios/{sid}", response_model=ScenarioOut)
async def get_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(select(Scenario).where(Scenario.id == sid))).scalar_one_or_none()
    if not s: raise HTTPException(404, "Scenario not found")
    if s.creator_id != cu.id and not (s.is_public and s.is_published):
        raise HTTPException(404, "Scenario not found")
    return ScenarioOut.model_validate(s)

async def _get_owned_scenario(db: AsyncSession, sid: str, cu: User) -> Scenario:
    """Fetch a scenario the requesting user owns, or 404 -- shared by every
    route below that mutates a scenario by id (edit, delete, publish,
    retry-prefab, unpublish). Not used by get_scenario (read access allows
    any public+published scenario, not just owned ones) or report_scenario
    (looks up by is_published, not ownership)."""
    s = (await db.execute(
        select(Scenario).where(Scenario.id == sid, Scenario.creator_id == cu.id)
    )).scalar_one_or_none()
    if not s: raise HTTPException(404, "Scenario not found")
    return s


@app.put("/scenarios/{sid}", response_model=ScenarioOut)
async def update_scenario(sid: str, body: ScenarioUpdate, db: AsyncSession = Depends(get_db),
                           cu: User = Depends(get_current_user)):
    s = await _get_owned_scenario(db, sid, cu)
    if s.is_published:
        raise HTTPException(403, "Published scenarios cannot be edited")
    if body.char_name is not None: s.char_name = body.char_name
    if body.char_pronouns is not None: s.char_pronouns = body.char_pronouns
    if body.char_title is not None: s.char_title = body.char_title
    if body.char_personality is not None: s.char_personality = sanitize_input(body.char_personality)
    if body.greeting is not None: s.greeting = sanitize_input(body.greeting)
    await db.commit(); await db.refresh(s)
    return ScenarioOut.model_validate(s)

@app.delete("/scenarios/{sid}", status_code=204)
async def delete_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = await _get_owned_scenario(db, sid, cu)
    if s.is_published:
        raise HTTPException(400, "Published scenarios cannot be deleted. Contact support to unpublish.")
    redis = await get_redis()
    session_set_key = f"scenario_sessions:{sid}"
    session_ids = await redis.smembers(session_set_key)
    for member in session_ids:
        session_key = f"session:{member.decode() if isinstance(member, bytes) else member}"
        await redis.delete(session_key)
    await redis.delete(session_set_key)
    await db.delete(s); await db.commit()

async def _enqueue_prefab_job(request: Request, db: AsyncSession, s: Scenario):
    """Queue the prefab job. If the enqueue call itself fails (e.g. Redis
    briefly unreachable) — distinct from the job failing once it's running,
    which worker.py already handles — mark prefab_status=failed rather than
    leaving it stuck at 'pending' forever with no job ever queued. The
    retry-prefab endpoint remains available to recover from either failure."""
    try:
        await request.app.state.arq_pool.enqueue_job(
            "prefab_engine_job", s.id,
            s.char_name, s.char_pronouns, s.char_title, s.char_personality, s.greeting,
        )
    except Exception as e:
        logger.error(f"Failed to enqueue prefab job for scenario {s.id[:8]}: {e}")
        s.prefab_status = "failed"
        await db.commit()
        raise HTTPException(503, "Could not queue prefab job — try again shortly")


@app.post("/scenarios/{sid}/publish", response_model=ScenarioOut)
async def publish_scenario(sid: str, request: Request, db: AsyncSession = Depends(get_db),
                            cu: User = Depends(get_current_user)):
    s = await _get_owned_scenario(db, sid, cu)
    if s.is_published: raise HTTPException(400, "Scenario already published")
    s.is_published = True
    s.prefab_status = "pending"
    await db.commit(); await db.refresh(s)
    await _enqueue_prefab_job(request, db, s)
    return ScenarioOut.model_validate(s)

@app.post("/scenarios/{sid}/retry-prefab", response_model=ScenarioOut)
@limiter.limit("5/minute")
async def retry_prefab(sid: str, request: Request, db: AsyncSession = Depends(get_db),
                        cu: User = Depends(get_current_user)):
    s = await _get_owned_scenario(db, sid, cu)
    if not s.is_published: raise HTTPException(400, "Scenario is not published")
    if s.prefab_status == "ready": raise HTTPException(400, "Prefab is already ready")
    s.prefab_status = "pending"
    await db.commit(); await db.refresh(s)
    await _enqueue_prefab_job(request, db, s)
    return ScenarioOut.model_validate(s)

@app.post("/scenarios/{sid}/unpublish", response_model=ScenarioOut)
async def unpublish_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = await _get_owned_scenario(db, sid, cu)
    if not s.is_published: raise HTTPException(400, "Scenario is not published")
    s.is_published = False
    s.prefab_engine_state = None
    s.prefab_status = "pending"
    await db.commit(); await db.refresh(s)
    return ScenarioOut.model_validate(s)

@app.post("/scenarios/{sid}/report", status_code=201)
@limiter.limit("10/minute")
async def report_scenario(request: Request, sid: str, body: ScenarioReport,
                           db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(
        select(Scenario).where(Scenario.id == sid, Scenario.is_published == True)  # noqa: E712
    )).scalar_one_or_none()
    if not s: raise HTTPException(404, "Scenario not found")
    redis = await get_redis()
    report = {"scenario_id": sid, "reporter_id": cu.id, "reason": body.reason,
              "created_at": datetime.now(timezone.utc).isoformat()}
    await redis.lpush("scenario_reports", json.dumps(report))
    await redis.ltrim("scenario_reports", 0, 9999)
    return {"reported": True}

@app.post("/scenarios/{sid}/save", status_code=201)
async def save_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(select(Scenario).where(Scenario.id == sid))).scalar_one_or_none()
    if not s or (s.creator_id != cu.id and not (s.is_public and s.is_published)):
        raise HTTPException(404, "Scenario not found")
    already_saved = (await db.execute(
        select(ScenarioSave).where(ScenarioSave.user_id == cu.id, ScenarioSave.scenario_id == sid)
    )).scalar_one_or_none()
    if already_saved:
        return {"saved": True}
    db.add(ScenarioSave(id=str(uuid.uuid4()), user_id=cu.id, scenario_id=sid))
    await db.execute(update(Scenario).where(Scenario.id == sid).values(saves_count=Scenario.saves_count + 1))
    try:
        await db.commit()
    except IntegrityError:
        # Two concurrent saves (double-click, two tabs) both pass the
        # already_saved=None check above and both insert -- the second
        # commit hits ScenarioSave's UniqueConstraint(user_id, scenario_id).
        # Found live via code review. The save already exists either way,
        # so this is a no-op success, not an error.
        await db.rollback()
    return {"saved": True}

@app.delete("/scenarios/{sid}/save", status_code=204)
async def unsave_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    # Bulk DELETE + rowcount, not select-then-ORM-delete: two concurrent
    # unsaves (double-click, two tabs) both loading the row before either
    # commits would otherwise both see it as present and both run the
    # saves_count decrement below -- one logical unsave, counted twice.
    # A rowcount-gated bulk DELETE only lets the request that actually
    # removed a row touch the counter, mirroring save_scenario's
    # IntegrityError-based idempotency for the same race shape.
    result = await db.execute(
        delete(ScenarioSave).where(ScenarioSave.user_id == cu.id, ScenarioSave.scenario_id == sid)
    )
    if result.rowcount:
        await db.execute(
            update(Scenario).where(Scenario.id == sid)
            .values(saves_count=func.greatest(Scenario.saves_count - 1, 0))
        )
    await db.commit()


# ── Sessions ──────────────────────────────────────────────────

@app.get("/scenarios/{scenario_id}/last-session")
async def get_last_session(scenario_id: str, persona_id: str,
                            db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """persona_id is required and must match create_session's own resume
    query (below) exactly -- found live via code review that this used to
    be persona-agnostic while create_session's actual resume was always
    persona-scoped. The frontend renders this endpoint's history as the
    visible chat transcript immediately, before the engine finishes
    initializing; a mismatch meant a user switching personas on a
    previously-played scenario would see a prior persona's conversation
    glued onto a freshly-initialized engine that had no memory of it.
    engine_state.isnot(None) is included for the same reason -- a row this
    endpoint shows must be one create_session could actually resume from."""
    r = await db.execute(
        select(SessionLog)
        .where(
            SessionLog.user_id == cu.id,
            SessionLog.scenario_id == scenario_id,
            SessionLog.persona_id == persona_id,
            SessionLog.engine_state.isnot(None),
        )
        .order_by(SessionLog.started_at.desc()).limit(1)
    )
    log = r.scalar_one_or_none()
    if not log: return None
    return {
        "session_id":  log.session_id,
        "scenario_id": log.scenario_id,
        "turns_count": log.turns_count,
        "started_at":  log.started_at.isoformat(),
        "ended_at":    log.ended_at.isoformat() if log.ended_at else None,
        "history":     log.history,
    }


@app.post("/sessions", response_model=SessionOut, status_code=201)
@limiter.limit("20/minute")
async def create_session(
    request: Request,
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    redis = await get_redis()

    sc = (await db.execute(select(Scenario).where(
        Scenario.id == body.scenario_id,
    ))).scalar_one_or_none()
    if not sc: raise HTTPException(404, "Scenario not found")
    if sc.creator_id != cu.id and not (sc.is_public and sc.is_published):
        raise HTTPException(404, "Scenario not found")
    if body.preview and sc.creator_id != cu.id:
        raise HTTPException(404, "Scenario not found")

    p = (await db.execute(select(Persona).where(
        Persona.id == body.persona_id, Persona.user_id == cu.id
    ))).scalar_one_or_none()
    if not p: raise HTTPException(404, "Persona not found")

    # Preview sessions skip history restore and don't save to session_logs
    last_log = None
    if not body.preview:
        last_log = (await db.execute(
            select(SessionLog)
            .where(
                SessionLog.user_id == cu.id,
                SessionLog.scenario_id == body.scenario_id,
                SessionLog.persona_id == body.persona_id,
                SessionLog.engine_state.isnot(None),
            )
            .order_by(SessionLog.started_at.desc()).limit(1)
        )).scalar_one_or_none()

    session_id = str(uuid.uuid4())
    now        = datetime.now(timezone.utc)

    placeholder = {
        "session_id":       session_id,
        "user_id":          cu.id,
        "scenario_id":      body.scenario_id,
        "persona_id":       body.persona_id,
        "persona_name":     p.name,
        "persona_pronouns": p.pronouns,
        "char_name":        sc.char_name,
        "char_pronouns":    sc.char_pronouns,
        "status":           "initializing",
        "engine_state":     None,
        "history":          [],
        "turn":             0,
        "started_at":       now.isoformat(),
        "preview":          body.preview,
    }
    await redis.setex(f"session:{session_id}", SESSION_TTL, json.dumps(placeholder))
    if not body.preview:
        await redis.sadd(f"scenario_sessions:{body.scenario_id}", session_id)

    try:
        await request.app.state.arq_pool.enqueue_job(
            "init_engine_job", session_id, sc.id,
            p.name, p.pronouns, sc.greeting, body.content_filter, body.preview,
            last_log.engine_state if last_log else None,
        )
    except Exception as e:
        # Distinct from the job itself failing (worker.py already sets status=error
        # for that) — this is the enqueue call never reaching Redis at all. Without
        # this, the placeholder above would sit at "initializing" forever with no
        # job ever queued to move it forward.
        logger.error(f"Failed to enqueue init job for session {session_id[:8]}: {e}")
        placeholder["status"] = "error"
        placeholder["error"]  = "Could not queue session initialization"
        await redis.setex(f"session:{session_id}", SESSION_TTL, json.dumps(placeholder))
        raise HTTPException(503, "Could not start session — try again shortly")

    return SessionOut(session_id=session_id, scenario_id=body.scenario_id, persona_id=body.persona_id, started_at=now)


@app.get("/sessions/{session_id}/status")
async def session_status(session_id: str, cu: User = Depends(get_current_user)):
    """Polls engine initialization status. Frontend shows loading until 'ready'."""
    redis = await get_redis()
    raw   = await redis.get(f"session:{session_id}")
    if not raw: raise HTTPException(404, "Session not found")
    ctx = json.loads(raw)
    if ctx["user_id"] != cu.id: raise HTTPException(403, "Forbidden")
    return {"status": ctx.get("status", "initializing"), "turn": ctx.get("turn", 0)}


async def _upsert_session_log(ctx: dict, ended_at: datetime = None, db: AsyncSession = None):
    """Checkpoint a session's history/engine_state to Postgres. Called on every
    turn (not just on clean end_session) so a crashed client, dropped connection,
    or Redis eviction under memory pressure doesn't lose the whole conversation —
    the resumable state lives in session_logs as of the last completed turn."""
    if ctx.get("preview"):
        return
    session_id = ctx["session_id"]
    clean_history = [
        {"role": m["role"], "content": m["content"]}
        for m in ctx.get("history", [])
        if m.get("role") in ("user", "assistant")
    ]
    own_session = db is None
    if own_session:
        db = AsyncSessionLocal()
    try:
        existing = (await db.execute(
            select(SessionLog).where(SessionLog.session_id == session_id)
        )).scalar_one_or_none()
        if existing:
            existing.history = clean_history
            existing.turns_count = ctx.get("turn", 0)
            existing.engine_state = ctx.get("engine_state")
            if ended_at:
                existing.ended_at = ended_at
        else:
            db.add(SessionLog(
                id=str(uuid.uuid4()),
                session_id=session_id,
                user_id=ctx["user_id"],
                scenario_id=ctx.get("scenario_id"),
                persona_id=ctx.get("persona_id"),
                history=clean_history,
                turns_count=ctx.get("turn", 0),
                engine_state=ctx.get("engine_state"),
                ended_at=ended_at,
            ))
        await db.commit()
    finally:
        if own_session:
            await db.close()


async def _execute_turn(request: Request, session_id: str, cu: User, redis,
                         player_input: str, engine_model: str, mode: str):
    """Shared by play_turn / continue_turn / regenerate_turn — identical
    lock/checkpoint/plays_count handling in all three; only which ai_service
    call gets made, and what it's called with, differs. mode is one of
    "turn" | "continue" | "regenerate"."""
    raw = await redis.get(f"session:{session_id}")
    if not raw: raise HTTPException(404, "Session expired or not found")

    ctx = json.loads(raw)
    if ctx["user_id"] != cu.id: raise HTTPException(403, "Forbidden")
    if ctx.get("status") != "ready": raise HTTPException(503, "Engine still initializing")
    if not ctx.get("engine_state"): raise HTTPException(503, "Engine state missing")

    lock_key = f"lock:session:{session_id}"
    # 120s, matching play_turn_stream's lock on the same key pattern below --
    # found live via code review that this used to be 60s here with no
    # explanation for the asymmetry. 60s is undersized on its own:
    # _reserve_turn_budget's own max_wait can be up to 60s under Groq
    # rate-limit backoff, which could exhaust the whole lock window before
    # the LLM call even starts, letting it expire mid-turn and opening the
    # exact concurrent-write race this lock exists to prevent.
    acquired = await redis.set(lock_key, "1", nx=True, ex=120)
    if not acquired:
        raise HTTPException(409, "A turn is already in progress for this session")

    try:
        model = engine_model or DEFAULT_ENGINE_MODEL
        if model not in ENGINE_MODELS:
            raise HTTPException(400, f"Unknown engine model: {model}")
        if mode == "regenerate":
            try:
                result = await engine_regenerate(ctx["engine_state"], engine_model=model)
            except ValueError as e:
                raise HTTPException(400, str(e))
            except RuntimeError as e:
                raise HTTPException(503, str(e))
        else:
            try:
                result = await engine_step(ctx["engine_state"], player_input, engine_model=model,
                                            continue_narrative=(mode == "continue"))
            except RuntimeError as e:
                # covers both "GROQ_API_KEY not set" (misconfiguration, but
                # honest 503 beats a raw 500) and the TPM-budget capacity
                # message from ai_service._reserve_turn_budget.
                raise HTTPException(503, str(e))

        ctx["engine_state"] = result["engine_state"]
        ctx["history"]      = result["engine_state"].get("display_history") or result["engine_state"].get("history", [])
        ctx["turn"]         = result["turn"]

        await redis.setex(f"session:{session_id}", SESSION_TTL, json.dumps(ctx))
        await _upsert_session_log(ctx)

        # regenerate reuses the same turn number — must not double-count a play
        # that already happened, or re-bill turn 1 as a second "first play."
        if result["turn"] == 1 and not ctx.get("preview") and mode != "regenerate":
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(Scenario).where(Scenario.id == ctx["scenario_id"])
                    .values(plays_count=Scenario.plays_count + 1)
                )
                await db.commit()

        if mode == "regenerate":
            # The old rating (if any) was for content that no longer exists.
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TurnRating).where(
                    TurnRating.user_id == cu.id, TurnRating.session_id == session_id, TurnRating.turn == result["turn"]
                ))
                await db.commit()

        # Fire-and-forget: media generation happens off the request/response
        # cycle, same pattern as prefab/init jobs. media_context is None when
        # there's no NPC to depict yet (e.g. an empty opening) -- skip rather
        # than enqueue a job with nothing to generate. Enqueue failure (e.g.
        # Redis hiccup) must not fail the turn itself -- the text response
        # already succeeded and matters more than the illustration.
        media_context = result.get("media_context")
        if media_context:
            try:
                await request.app.state.arq_pool.enqueue_job(
                    "generate_turn_media_job", session_id, ctx["scenario_id"], cu.id, result["turn"],
                    media_context["image_prompt"], media_context["narration_text"],
                    media_context["voice_id"], media_context["voice_speed"],
                )
            except Exception as e:
                logger.warning(
                    f"Failed to enqueue turn media job for session={session_id[:8]} turn={result['turn']}: {e}"
                )
    finally:
        await redis.delete(lock_key)

    return PlayResponse(
        response=result["response"],
        session_id=session_id,
        turn=result["turn"],
        sovereign=result["sovereign"],
        violations=result["violations"],
        is_continuation=(mode == "continue"),
        is_regeneration=(mode == "regenerate"),
    )


@app.post("/sessions/{session_id}/turn", response_model=PlayResponse)
@limiter.limit("60/minute")
async def play_turn(session_id: str, request: Request, body: PlayTurnWithModel,
                     cu: User = Depends(enforce_user_rate_limit)):
    redis = await get_redis()
    return await _execute_turn(request, session_id, cu, redis, body.input, body.engine_model, mode="turn")


@app.post("/sessions/{session_id}/continue", response_model=PlayResponse)
@limiter.limit("60/minute")
async def continue_turn(session_id: str, request: Request, body: ContinueTurnRequest = ContinueTurnRequest(),
                          cu: User = Depends(enforce_user_rate_limit)):
    """No player input this turn — advance the scene on its own. Shares the
    same rate limit bucket as /turn: it's the same generation cost."""
    redis = await get_redis()
    return await _execute_turn(request, session_id, cu, redis, "", body.engine_model, mode="continue")


@app.post("/sessions/{session_id}/regenerate", response_model=PlayResponse)
@limiter.limit("60/minute")
async def regenerate_turn(session_id: str, request: Request, body: RegenerateRequest = RegenerateRequest(),
                            cu: User = Depends(enforce_user_rate_limit)):
    """Discard the last response and try again for the same input — a single
    bad generation doesn't have to mean the whole turn was wrong. Shares the
    same rate limit bucket as /turn: it's the same generation cost."""
    redis = await get_redis()
    return await _execute_turn(request, session_id, cu, redis, "", body.engine_model, mode="regenerate")


@app.post("/sessions/{session_id}/turns/{turn}/rating", response_model=TurnRatingOut, status_code=201)
async def rate_turn(session_id: str, turn: int, body: TurnRatingCreate,
                     db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """Upsert — a user can change their rating. No FK/liveness check against
    the Redis session: ratings must survive session TTL/end, and preview
    sessions never get a SessionLog row to check against anyway."""
    existing = (await db.execute(
        select(TurnRating).where(TurnRating.user_id == cu.id, TurnRating.session_id == session_id,
                                 TurnRating.turn == turn)
    )).scalar_one_or_none()
    if existing:
        existing.rating = body.rating
        await db.commit()
    else:
        db.add(TurnRating(user_id=cu.id, session_id=session_id, turn=turn, rating=body.rating))
        try:
            await db.commit()
        except IntegrityError:
            # Same race as save_scenario above: two concurrent ratings for
            # the same turn both see existing=None and both insert. The
            # second violates UniqueConstraint(user_id, session_id, turn);
            # fall back to updating the row the other request just created
            # instead of a raw 500 for what's a completely normal race
            # (e.g. a double-click on a star rating).
            await db.rollback()
            existing = (await db.execute(
                select(TurnRating).where(TurnRating.user_id == cu.id, TurnRating.session_id == session_id,
                                         TurnRating.turn == turn)
            )).scalar_one_or_none()
            existing.rating = body.rating
            await db.commit()
    return TurnRatingOut(session_id=session_id, turn=turn, rating=body.rating)


@app.get("/sessions/{session_id}/media", response_model=List[TurnMediaOut])
async def list_turn_media(session_id: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """Polled by the frontend player to discover per-turn image/audio/video
    as background jobs finish -- status starts 'pending' and flips to
    'ready'/'failed' (see worker.generate_turn_media_job)."""
    rows = (await db.execute(
        select(TurnMedia).where(TurnMedia.session_id == session_id, TurnMedia.user_id == cu.id)
        .order_by(TurnMedia.turn)
    )).scalars().all()
    return rows


@app.post("/sessions/{session_id}/turns/{turn}/regenerate-media", response_model=TurnMediaOut, status_code=202)
async def regenerate_turn_media(session_id: str, turn: int, request: Request,
                                 db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """Re-rolls a turn's image/audio/video with a new seed, reusing the
    stored prompt/narration/voice (see TurnMedia's docstring in models.py)
    -- for when generation quality (eyes especially) came out bad."""
    row = (await db.execute(
        select(TurnMedia).where(TurnMedia.session_id == session_id, TurnMedia.turn == turn, TurnMedia.user_id == cu.id)
    )).scalar_one_or_none()
    if not row or not row.image_prompt:
        raise HTTPException(404, "No prior media for this turn to regenerate")
    row.status = "pending"
    row.error = None
    await db.commit()
    try:
        await request.app.state.arq_pool.enqueue_job("regenerate_turn_media_job", session_id, turn)
    except Exception as e:
        # Unlike the other enqueue_job call sites (generate_turn_media_job
        # off a turn), there's no "primary work" here that already
        # succeeded independently -- media regeneration IS the whole
        # point of this request, so a failed enqueue can't just be logged
        # and ignored, or the row is left showing "pending" forever with
        # nothing actually processing it.
        logger.error(f"Failed to enqueue regenerate-media job for session={session_id[:8]} turn={turn}: {e}")
        row.status = "failed"
        row.error = "Could not queue regeneration job — try again shortly"
        await db.commit()
        raise HTTPException(503, "Could not queue regeneration job — try again shortly")
    return row


EXPORT_MEDIA_ROOT = "/app/media"  # matches this service's docker-compose volume mount


@app.get("/sessions/{session_id}/movie", response_model=SessionMovieOut)
async def get_session_movie(session_id: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """The always-current, whole-session-so-far file kept up to date by
    worker._extend_session_movie -- a plain filesystem check (not a DB
    flag) since that function is the single source of truth for whether
    movie.mp4 reflects the latest ready turns."""
    owned = (await db.execute(
        select(TurnMedia.id).where(TurnMedia.session_id == session_id, TurnMedia.user_id == cu.id).limit(1)
    )).scalar_one_or_none()
    if not owned:
        raise HTTPException(404, "Session not found")
    path = f"{EXPORT_MEDIA_ROOT}/{session_id}/movie.mp4"
    if not os.path.exists(path):
        return SessionMovieOut(available=False, url=None)
    return SessionMovieOut(available=True, url=f"/media/{session_id}/movie.mp4")


@app.post("/sessions/{session_id}/export", response_model=ExportOut)
@limiter.limit("10/minute")
async def export_session_video(session_id: str, request: Request, body: ExportRequest,
                                 db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    return await _export_session_video(session_id, body, db, cu)


async def _export_session_video(session_id: str, body: ExportRequest, db: AsyncSession, cu: User):
    """Concatenates the ready per-turn video segments in [from_turn, to_turn]
    into a single downloadable mp4 -- synchronous (not an arq job) because
    concatenating a handful of already-generated short segments is a
    seconds-long ffmpeg call, not a GPU/CPU-heavy generation job.

    The actual concat work is video_utils.concat_videos, shared with
    worker.py's session-movie extension -- this used to hand-roll its own
    copy of the same ffmpeg flags without that helper's atomic-replace
    safety; consolidated after code review flagged the duplication.

    Split from the route (same pattern as _execute_turn) so it's callable
    directly in tests without slowapi's rate-limit decorator needing a real
    Request/client address.
    """
    if body.from_turn > body.to_turn:
        raise HTTPException(400, "from_turn must be <= to_turn")

    rows = (await db.execute(
        select(TurnMedia).where(
            TurnMedia.session_id == session_id, TurnMedia.user_id == cu.id,
            TurnMedia.turn >= body.from_turn, TurnMedia.turn <= body.to_turn,
            TurnMedia.status == "ready", TurnMedia.video_segment_url.isnot(None),
        ).order_by(TurnMedia.turn)
    )).scalars().all()
    if not rows:
        raise HTTPException(404, "No ready scenes in that turn range")

    session_dir = f"{EXPORT_MEDIA_ROOT}/{session_id}"
    export_id = uuid.uuid4().hex[:12]
    output_name = f"export_{export_id}.mp4"
    output_path = f"{session_dir}/{output_name}"
    input_paths = [f"{session_dir}/{row.turn}.mp4" for row in rows]

    try:
        await concat_videos(input_paths, output_path)
    except RuntimeError as e:
        logger.error(f"Export failed for session {session_id[:8]}: {e}")
        raise HTTPException(500, "Export failed while combining scenes")

    return ExportOut(download_url=f"/media/{session_id}/{output_name}", turns=[r.turn for r in rows])


# ── Billing (mock — see models.py's UserSubscription docstring) ────────
# Gated on mock_billing_enabled: while False, real visitors get 404s, not
# a pricing page that implies a real payment system exists. is_mock is
# always True here regardless of the flag — nothing in this section ever
# claims a real charge happened.

@app.get("/billing/tiers", response_model=List[SubscriptionTierOut])
async def list_tiers(db: AsyncSession = Depends(get_db)):
    if not settings.mock_billing_enabled:
        raise HTTPException(404, "Not found")
    rows = (await db.execute(select(SubscriptionTier).order_by(SubscriptionTier.sort_order))).scalars().all()
    return rows


@app.get("/billing/subscription", response_model=Optional[UserSubscriptionOut])
async def get_my_subscription(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    if not settings.mock_billing_enabled:
        raise HTTPException(404, "Not found")
    return (await db.execute(select(UserSubscription).where(UserSubscription.user_id == cu.id))).scalar_one_or_none()


@app.post("/billing/subscribe/{tier_id}", response_model=UserSubscriptionOut, status_code=201)
async def mock_subscribe(tier_id: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """Flips the user's tier immediately — no payment step exists to wait
    on because there's no real payment happening. Upsert: re-subscribing
    just changes the tier rather than erroring on a duplicate."""
    if not settings.mock_billing_enabled:
        raise HTTPException(404, "Not found")
    tier = (await db.execute(select(SubscriptionTier).where(SubscriptionTier.id == tier_id))).scalar_one_or_none()
    if not tier:
        raise HTTPException(404, "Unknown tier")
    existing = (await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == cu.id)
    )).scalar_one_or_none()
    if existing:
        existing.tier_id = tier_id
        existing.status = "active"
        row = existing
    else:
        row = UserSubscription(user_id=cu.id, tier_id=tier_id, status="active", is_mock=True)
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@app.get("/admin/telemetry", response_model=TelemetryOut)
async def get_telemetry(db: AsyncSession = Depends(get_db), _admin: User = Depends(require_admin)):
    """Real-time cost/capacity snapshot — the numbers that actually decide
    whether the platform is about to hit the Groq TPM wall (see
    rate_limiter.py's docstring), not app-level metrics like signups."""
    from rate_limiter import WINDOW_KEY, TPM_LIMIT
    from worker import PREFAB_JOB_KEY, PREFAB_MAX
    from models import TurnMedia

    redis = await get_redis()
    tpm_used = int(await redis.get(WINDOW_KEY) or 0)
    prefab_running = int(await redis.get(PREFAB_JOB_KEY) or 0)
    active_sessions = 0
    async for _ in redis.scan_iter(match="session:*", count=200):
        active_sessions += 1

    total_scenarios = (await db.execute(select(func.count(Scenario.id)))).scalar_one()
    total_plays = (await db.execute(select(func.coalesce(func.sum(Scenario.plays_count), 0)))).scalar_one()
    media_rows = (await db.execute(select(TurnMedia.status, func.count(TurnMedia.id)).group_by(TurnMedia.status))).all()

    return TelemetryOut(
        groq_tpm_used=tpm_used, groq_tpm_limit=TPM_LIMIT,
        active_sessions=active_sessions,
        prefab_jobs_running=prefab_running, prefab_jobs_max=PREFAB_MAX,
        total_scenarios=total_scenarios, total_plays=total_plays,
        turn_media_by_status={status: count for status, count in media_rows},
    )


@app.post("/sessions/{session_id}/turn-stream")
@limiter.limit("60/minute")
async def play_turn_stream(session_id: str, request: Request, body: PlayTurnWithModel,
                            cu: User = Depends(enforce_user_rate_limit)):
    redis = await get_redis()
    raw = await redis.get(f"session:{session_id}")
    if not raw: raise HTTPException(404, "Session expired or not found")
    ctx = json.loads(raw)
    if ctx["user_id"] != cu.id: raise HTTPException(403, "Forbidden")
    if ctx.get("status") != "ready": raise HTTPException(503, "Engine still initializing")
    if not ctx.get("engine_state"): raise HTTPException(503, "Engine state missing")

    lock_key = f"lock:session:{session_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=120)  # see _execute_turn's matching lock for why 120s
    if not acquired:
        raise HTTPException(409, "A turn is already in progress for this session")

    model = body.engine_model or DEFAULT_ENGINE_MODEL
    if model not in ENGINE_MODELS:
        await redis.delete(lock_key)
        raise HTTPException(400, f"Unknown engine model: {model}")

    async def event_generator():
        try:
            async for chunk in engine_step_stream(ctx["engine_state"], body.input, engine_model=model):
                if chunk.get("type") == "token":
                    yield f"data: {json.dumps(chunk)}\n\n"
                elif chunk.get("type") == "done":
                    redis_local = await get_redis()
                    es = chunk["engine_state"]
                    ctx["engine_state"] = es
                    ctx["history"] = es.get("display_history") or es.get("history", [])
                    ctx["turn"] = chunk["turn"]
                    await redis_local.setex(f"session:{session_id}", SESSION_TTL, json.dumps(ctx))
                    await _upsert_session_log(ctx)
                    if chunk["turn"] == 1 and not ctx.get("preview"):
                        async with AsyncSessionLocal() as db:
                            await db.execute(
                                update(Scenario).where(Scenario.id == ctx["scenario_id"])
                                .values(plays_count=Scenario.plays_count + 1)
                            )
                            await db.commit()
                    media_context = chunk.get("media_context")
                    if media_context:
                        try:
                            await request.app.state.arq_pool.enqueue_job(
                                "generate_turn_media_job", session_id, ctx["scenario_id"], cu.id, chunk["turn"],
                                media_context["image_prompt"], media_context["narration_text"],
                                media_context["voice_id"], media_context["voice_speed"],
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to enqueue turn media job (stream) for "
                                f"session={session_id[:8]} turn={chunk['turn']}: {e}"
                            )
                    yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            redis_local = await get_redis()
            await redis_local.delete(lock_key)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.delete("/sessions/{session_id}", status_code=204)
async def end_session(session_id: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    redis = await get_redis()
    raw   = await redis.get(f"session:{session_id}")
    if not raw: return
    ctx = json.loads(raw)
    if ctx["user_id"] != cu.id: raise HTTPException(403, "Forbidden")

    # Best-effort: avoid checkpointing/deleting while a turn is mid-flight and
    # holding this session's lock, which could otherwise interleave writes to
    # the same SessionLog row. Bounded wait, not a hard block — turns can
    # legitimately take well over a minute, and end_session must not hang that long.
    lock_key = f"lock:session:{session_id}"
    for _ in range(10):
        if not await redis.exists(lock_key):
            break
        await asyncio.sleep(0.3)
    else:
        logger.warning(f"end_session {session_id[:8]} proceeding while a turn may still be in flight")

    # Re-read after the wait -- found live: checkpointing the `ctx` captured
    # before the loop silently reverted the most recently completed turn.
    # _execute_turn writes its fresh state back to this same Redis key
    # before releasing the lock, so if a turn finished while we waited,
    # the pre-wait snapshot is already stale and would overwrite that
    # turn's real Postgres row with older history/engine_state/turn data.
    raw = await redis.get(f"session:{session_id}")
    if raw:
        ctx = json.loads(raw)

    if not ctx.get("preview"):
        await _upsert_session_log(ctx, ended_at=datetime.now(timezone.utc), db=db)
    scenario_id = ctx.get("scenario_id")
    if scenario_id and not ctx.get("preview"):
        await redis.srem(f"scenario_sessions:{scenario_id}", session_id)
    await redis.delete(f"session:{session_id}")


@app.get("/sessions/history")
async def session_history(cursor: str = None, limit: int = 20, db: AsyncSession = Depends(get_db),
                           cu: User = Depends(get_current_user)):
    limit = min(max(limit, 1), 50)
    query = select(SessionLog).where(SessionLog.user_id == cu.id).order_by(SessionLog.started_at.desc())
    if cursor:
        try:
            cursor_time = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid cursor value")
        query = query.where(SessionLog.started_at < cursor_time)
    query = query.limit(limit + 1)
    r = await db.execute(query)
    items = r.scalars().all()
    has_more = len(items) > limit
    items = items[:limit]
    next_cursor = items[-1].started_at.isoformat() if has_more and items else None

    # Card thumbnail = latest ready generated image for the session, if any
    # -- one extra query batched across the whole page rather than N+1.
    thumbnails = {}
    session_ids = [s.session_id for s in items]
    if session_ids:
        media_rows = (await db.execute(
            select(TurnMedia.session_id, TurnMedia.image_url)
            .where(TurnMedia.session_id.in_(session_ids), TurnMedia.status == "ready")
            .order_by(TurnMedia.session_id, TurnMedia.turn.desc())
        )).all()
        for sid, image_url in media_rows:
            thumbnails.setdefault(sid, image_url)

    out = [
        SessionLogOut.model_validate(s).model_copy(update={"thumbnail_url": thumbnails.get(s.session_id)})
        for s in items
    ]
    return {"items": out, "next_cursor": next_cursor, "has_more": has_more}

@app.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str, db: AsyncSession = Depends(get_db),
                               cu: User = Depends(get_current_user)):
    r = await db.execute(select(SessionLog).where(SessionLog.session_id == session_id, SessionLog.user_id == cu.id))
    log = r.scalar_one_or_none()
    if not log: raise HTTPException(404, "Session not found")
    return {"session_id": session_id, "history": log.history, "turns": log.turns_count}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "scenarai-api", "version": "2.0.0"}
