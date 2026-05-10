import asyncio
import logging

PREFAB_JOB_KEY = "prefab:running_count"
PREFAB_MAX     = 2

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from datetime import datetime, timezone
import uuid, json

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import settings
from database import get_db, get_redis, close_connections, AsyncSessionLocal
from models import User, Persona, Scenario, ScenarioSave, SessionLog
from schemas import (
    UserCreate, UserLogin, UserOut, Token,
    PersonaCreate, PersonaOut, PersonaUpdate,
    ScenarioCreate, ScenarioOut, ScenarioUpdate,
    SessionCreate, SessionOut,
    PlayTurnWithModel, PlayResponse,
    SessionLogOut,
    PasswordChange, ScenarioReport,
)
from auth import hash_password, verify_password, create_token, get_current_user
from ai_service import (
    generate_scenario_metadata, engine_step, engine_step_stream,
    ENGINE_MODELS, DEFAULT_ENGINE_MODEL,
    engine_prefab, engine_init_from_prefab, engine_init,
)
from engine.inference import sanitize_input

SESSION_TTL = 60 * 60 * 12  # 12 hours

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic (runs in Dockerfile before uvicorn starts).
    # create_all is intentionally removed to avoid conflicts with migration tracking.
    if settings.jwt_secret == "dev_secret":
        logger.warning("SECURITY: jwt_secret is using the insecure default. Set JWT_SECRET in .env before any non-local deployment.")
    if "changeme" in settings.database_url:
        logger.warning("SECURITY: database_url contains the default 'changeme' password. Set DATABASE_URL in .env.")
    if not settings.groq_api_key:
        logger.warning("SECURITY: GROQ_API_KEY is not set — all LLM calls will fail.")
    yield
    await close_connections()


app = FastAPI(title="Scenarai API", version="2.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded. Please slow down."})


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
    return Token(access_token=create_token(user.id), user=UserOut.model_validate(user))

@app.post("/auth/login", response_model=Token)
@limiter.limit("10/minute")
async def login(request: Request, body: UserLogin, db: AsyncSession = Depends(get_db)):
    u = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if not u or not verify_password(body.password, u.hashed_password):
        raise HTTPException(401, "Invalid credentials")
    return Token(access_token=create_token(u.id), user=UserOut.model_validate(u))

@app.get("/auth/me", response_model=UserOut)
async def me(cu: User = Depends(get_current_user)):
    return UserOut.model_validate(cu)

@app.post("/auth/change-password", status_code=204)
@limiter.limit("5/minute")
async def change_password(request: Request, body: PasswordChange, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
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
async def list_personas(cursor: str = None, limit: int = 20, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
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
async def update_persona(pid: str, body: PersonaUpdate, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
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

# ── Image / Video Generation ────────────────────────────────
# TODO: Integrate an AI image generation agent to produce scene card images.
#      When a scenario is created, call an image generation model (e.g. Stable Diffusion,
#      DALL-E, or Flux) using the scenario's title, brief, tags, and char_personality
#      as the prompt. Store the resulting image URL or base64 in Scenario.image_url.
#
# TODO: Integrate a video generation agent for scenario trailers or key scene animations.
#      Use models like Runway Gen-2, Pika, or Kling to generate short video clips
#      from scene descriptions. Store in Scenario.video_url.
#
# Example agent interface:
#   async def generate_scenario_image(char_name, title, brief, tags, personality) -> str:
#       """Call image generation API, return image URL or base64."""
#       ...
#
#   async def generate_scenario_video(description, duration=5) -> str:
#       """Call video generation API, return video URL."""
#       ...

@app.post("/scenarios", response_model=ScenarioOut, status_code=201)
@limiter.limit("10/minute")
async def create_scenario(request: Request, body: ScenarioCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
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


async def _prefab_engine_bg(
    scenario_id: str,
    char_name: str, char_pronouns: str, char_title: str,
    char_personality: str, greeting: str,
):
    """Background task: pre-compute engine at scenario creation and persist it."""
    redis = await get_redis()
    count = await redis.incr(PREFAB_JOB_KEY)
    if count > PREFAB_MAX:
        await redis.decr(PREFAB_JOB_KEY)
        logger.warning(f'Prefab skipped for {scenario_id[:8]}: too many concurrent jobs ({count - 1})')
        return
    try:
        prefab = await engine_prefab(char_name, char_pronouns, char_title, char_personality, greeting)
        if prefab:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(Scenario).where(Scenario.id == scenario_id).values(prefab_engine_state=prefab)
                )
                await db.commit()
            logger.info(f'Scenario {scenario_id[:8]} prefab ready')
    except Exception as e:
        logger.error(f'Prefab failed for scenario {scenario_id[:8]}: {e}')
    finally:
        await redis.decr(PREFAB_JOB_KEY)

@app.get("/scenarios")
async def list_scenarios(cursor: str = None, limit: int = 20, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
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

@app.get("/scenarios/{sid}", response_model=ScenarioOut)
async def get_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(select(Scenario).where(Scenario.id == sid))).scalar_one_or_none()
    if not s: raise HTTPException(404, "Scenario not found")
    if s.creator_id != cu.id and not (s.is_public and s.is_published):
        raise HTTPException(404, "Scenario not found")
    return ScenarioOut.model_validate(s)

@app.put("/scenarios/{sid}", response_model=ScenarioOut)
async def update_scenario(sid: str, body: ScenarioUpdate, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(select(Scenario).where(Scenario.id == sid, Scenario.creator_id == cu.id))).scalar_one_or_none()
    if not s: raise HTTPException(404, "Scenario not found")
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
    s = (await db.execute(select(Scenario).where(Scenario.id == sid, Scenario.creator_id == cu.id))).scalar_one_or_none()
    if not s: raise HTTPException(404, "Scenario not found")
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

@app.post("/scenarios/{sid}/publish", response_model=ScenarioOut)
async def publish_scenario(sid: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(select(Scenario).where(Scenario.id == sid, Scenario.creator_id == cu.id))).scalar_one_or_none()
    if not s: raise HTTPException(404, "Scenario not found")
    if s.is_published: raise HTTPException(400, "Scenario already published")
    s.is_published = True
    await db.commit(); await db.refresh(s)
    background_tasks.add_task(
        _prefab_engine_bg, s.id,
        s.char_name, s.char_pronouns, s.char_title, s.char_personality, s.greeting,
    )
    return ScenarioOut.model_validate(s)

@app.post("/scenarios/{sid}/unpublish", response_model=ScenarioOut)
async def unpublish_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(select(Scenario).where(Scenario.id == sid, Scenario.creator_id == cu.id))).scalar_one_or_none()
    if not s: raise HTTPException(404, "Scenario not found")
    if not s.is_published: raise HTTPException(400, "Scenario is not published")
    s.is_published = False
    s.prefab_engine_state = None
    await db.commit(); await db.refresh(s)
    return ScenarioOut.model_validate(s)

@app.post("/scenarios/{sid}/report", status_code=201)
@limiter.limit("10/minute")
async def report_scenario(request: Request, sid: str, body: ScenarioReport, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(select(Scenario).where(Scenario.id == sid, Scenario.is_published == True))).scalar_one_or_none()  # noqa: E712
    if not s: raise HTTPException(404, "Scenario not found")
    redis = await get_redis()
    report = {"scenario_id": sid, "reporter_id": cu.id, "reason": body.reason, "created_at": datetime.now(timezone.utc).isoformat()}
    await redis.lpush("scenario_reports", json.dumps(report))
    await redis.ltrim("scenario_reports", 0, 9999)
    return {"reported": True}

@app.post("/scenarios/{sid}/save", status_code=201)
async def save_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    s = (await db.execute(select(Scenario).where(Scenario.id == sid))).scalar_one_or_none()
    if not s or (s.creator_id != cu.id and not (s.is_public and s.is_published)):
        raise HTTPException(404, "Scenario not found")
    if (await db.execute(select(ScenarioSave).where(ScenarioSave.user_id == cu.id, ScenarioSave.scenario_id == sid))).scalar_one_or_none():
        return {"saved": True}
    db.add(ScenarioSave(id=str(uuid.uuid4()), user_id=cu.id, scenario_id=sid))
    await db.execute(update(Scenario).where(Scenario.id == sid).values(saves_count=Scenario.saves_count + 1))
    await db.commit(); return {"saved": True}

@app.delete("/scenarios/{sid}/save", status_code=204)
async def unsave_scenario(sid: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    sv = (await db.execute(select(ScenarioSave).where(ScenarioSave.user_id == cu.id, ScenarioSave.scenario_id == sid))).scalar_one_or_none()
    if sv:
        await db.delete(sv)
        await db.execute(update(Scenario).where(Scenario.id == sid).values(saves_count=func.greatest(Scenario.saves_count - 1, 0)))
        await db.commit()


# ── Sessions ──────────────────────────────────────────────────

@app.get("/scenarios/{scenario_id}/last-session")
async def get_last_session(scenario_id: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    r = await db.execute(
        select(SessionLog)
        .where(SessionLog.user_id == cu.id, SessionLog.scenario_id == scenario_id)
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
    background_tasks: BackgroundTasks,
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

    background_tasks.add_task(
        _init_engine_bg, session_id, sc, p, redis, body.content_filter, last_log, body.preview,
    )

    return SessionOut(session_id=session_id, scenario_id=body.scenario_id, persona_id=body.persona_id, started_at=now)


async def _init_engine_bg(session_id, sc, p, redis, content_filter="off", last_log=None, preview=False):
    """Background task: initialize engine and store result in Redis."""
    try:
        if last_log and last_log.engine_state:
            logger.info(f'Session {session_id[:8]} restoring from previous session')
            engine_state = last_log.engine_state
        else:
            async with AsyncSessionLocal() as db:
                fresh = (await db.execute(select(Scenario).where(Scenario.id == sc.id))).scalar_one_or_none()
                prefab = fresh.prefab_engine_state if fresh else None

            if not prefab:
                if not preview:
                    raise RuntimeError(f'Scenario {sc.id} has no prefab — only published scenarios can be played')
                logger.info(f'Session {session_id[:8]} full init for draft preview (15-20 LLM calls)')
                engine_state = await engine_init(
                    char_name=sc.char_name,
                    char_pronouns=sc.char_pronouns,
                    char_title=sc.char_title,
                    char_personality=sc.char_personality,
                    greeting=sc.greeting,
                    player_name=p.name,
                    player_pronouns=p.pronouns,
                    content_filter=content_filter,
                )
            else:
                logger.info(f'Session {session_id[:8]} using prefab (fast init — 1 LLM call)')
                engine_state = await engine_init_from_prefab(
                    prefab,
                    player_name=p.name,
                    player_pronouns=p.pronouns,
                    greeting=sc.greeting,
                    content_filter=content_filter,
                )
        raw = await redis.get(f"session:{session_id}")
        if raw:
            ctx = json.loads(raw)
            ctx["engine_state"] = engine_state
            ctx["status"]       = "ready"
            ctx["history"] = engine_state.get("display_history") or engine_state.get("history", [])
            await redis.setex(f"session:{session_id}", SESSION_TTL, json.dumps(ctx))
            logger.info(f'Session {session_id[:8]} initialized and ready')
    except Exception as e:
        logger.error(f'Engine init failed for {session_id[:8]}: {e}')
        raw = await redis.get(f"session:{session_id}")
        if raw:
            ctx = json.loads(raw)
            ctx["status"] = "error"
            ctx["error"]  = str(e)
            await redis.setex(f"session:{session_id}", SESSION_TTL, json.dumps(ctx))


@app.get("/sessions/{session_id}/status")
async def session_status(session_id: str, cu: User = Depends(get_current_user)):
    """Polls engine initialization status. Frontend shows loading until 'ready'."""
    redis = await get_redis()
    raw   = await redis.get(f"session:{session_id}")
    if not raw: raise HTTPException(404, "Session not found")
    ctx = json.loads(raw)
    if ctx["user_id"] != cu.id: raise HTTPException(403, "Forbidden")
    return {"status": ctx.get("status", "initializing"), "turn": ctx.get("turn", 0)}


@app.post("/sessions/{session_id}/turn", response_model=PlayResponse)
@limiter.limit("60/minute")
async def play_turn(session_id: str, request: Request, body: PlayTurnWithModel, cu: User = Depends(get_current_user)):
    redis = await get_redis()
    raw   = await redis.get(f"session:{session_id}")
    if not raw: raise HTTPException(404, "Session expired or not found")

    ctx = json.loads(raw)
    if ctx["user_id"] != cu.id: raise HTTPException(403, "Forbidden")
    if ctx.get("status") != "ready": raise HTTPException(503, "Engine still initializing")
    if not ctx.get("engine_state"): raise HTTPException(503, "Engine state missing")

    lock_key = f"lock:session:{session_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=60)
    if not acquired:
        raise HTTPException(409, "A turn is already in progress for this session")

    try:
        model  = body.engine_model or DEFAULT_ENGINE_MODEL
        if model not in ENGINE_MODELS:
            raise HTTPException(400, f"Unknown engine model: {model}")
        result = await engine_step(ctx["engine_state"], body.input, engine_model=model)

        ctx["engine_state"] = result["engine_state"]
        ctx["history"]      = result["engine_state"].get("display_history") or result["engine_state"].get("history", [])
        ctx["turn"]         = result["turn"]

        await redis.setex(f"session:{session_id}", SESSION_TTL, json.dumps(ctx))

        if result["turn"] == 1 and not ctx.get("preview"):
            async with AsyncSessionLocal() as db:
                await db.execute(update(Scenario).where(Scenario.id == ctx["scenario_id"]).values(plays_count=Scenario.plays_count + 1))
                await db.commit()
    finally:
        await redis.delete(lock_key)

    return PlayResponse(
        response=result["response"],
        session_id=session_id,
        turn=result["turn"],
        sovereign=result["sovereign"],
        violations=result["violations"],
    )

@app.post("/sessions/{session_id}/turn-stream")
@limiter.limit("60/minute")
async def play_turn_stream(session_id: str, request: Request, body: PlayTurnWithModel, cu: User = Depends(get_current_user)):
    redis = await get_redis()
    raw = await redis.get(f"session:{session_id}")
    if not raw: raise HTTPException(404, "Session expired or not found")
    ctx = json.loads(raw)
    if ctx["user_id"] != cu.id: raise HTTPException(403, "Forbidden")
    if ctx.get("status") != "ready": raise HTTPException(503, "Engine still initializing")
    if not ctx.get("engine_state"): raise HTTPException(503, "Engine state missing")

    lock_key = f"lock:session:{session_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=120)
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
                    ctx["engine_state"] = chunk["engine_state"]
                    ctx["history"] = chunk["engine_state"].get("display_history") or chunk["engine_state"].get("history", [])
                    ctx["turn"] = chunk["turn"]
                    await redis_local.setex(f"session:{session_id}", SESSION_TTL, json.dumps(ctx))
                    if chunk["turn"] == 1 and not ctx.get("preview"):
                        async with AsyncSessionLocal() as db:
                            await db.execute(update(Scenario).where(Scenario.id == ctx["scenario_id"]).values(plays_count=Scenario.plays_count + 1))
                            await db.commit()
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

    if not ctx.get("preview"):
        clean_history = [
            {"role": m["role"], "content": m["content"]}
            for m in ctx.get("history", [])
            if m.get("role") in ("user", "assistant")
        ]
        log = SessionLog(
            id=str(uuid.uuid4()),
            session_id=session_id,
            user_id=cu.id,
            scenario_id=ctx.get("scenario_id"),
            persona_id=ctx.get("persona_id"),
            history=clean_history,
            turns_count=ctx.get("turn", 0),
            ended_at=datetime.now(timezone.utc),
            engine_state=ctx.get("engine_state"),
        )
        db.add(log); await db.commit()
    scenario_id = ctx.get("scenario_id")
    if scenario_id and not ctx.get("preview"):
        await redis.srem(f"scenario_sessions:{scenario_id}", session_id)
    await redis.delete(f"session:{session_id}")


@app.get("/sessions/history")
async def session_history(cursor: str = None, limit: int = 20, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
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
    return {"items": [SessionLogOut.model_validate(s) for s in items], "next_cursor": next_cursor, "has_more": has_more}

@app.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    r = await db.execute(select(SessionLog).where(SessionLog.session_id == session_id, SessionLog.user_id == cu.id))
    log = r.scalar_one_or_none()
    if not log: raise HTTPException(404, "Session not found")
    return {"session_id": session_id, "history": log.history, "turns": log.turns_count}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "scenarai-api", "version": "2.0.0"}
