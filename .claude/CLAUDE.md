# Scenarai — Claude Project Context

## What This Is
AI-powered interactive fiction / roleplay platform. Users create scenarios (character + opening scene), publish them, then play them as a persona in a multi-turn chat with an LLM narrator.

## Stack
- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + Redis + Groq LLMs (LLaMA, Gemma, Mixtral)
- **Frontend**: React + Vite + TanStack Query (single file: `frontend/app/src/App.jsx`)
- **Infrastructure**: Docker Compose + Nginx reverse proxy
- **Migrations**: Alembic

## Architecture: The Engine
The core product is the narrative engine in `backend/engine/`. On first play:
1. `scenario_to_engine()` runs 15-20 LLM calls to build a layered world model (layer1 = scene context, world state, entity registry, memory, behavioral reads)
2. Engine state serialized to JSON and stored in Redis (`session:{uuid}`, 12h TTL)
3. Each turn: deserialize → `engine.step()` → re-serialize to Redis

**Prefab optimization**: On `POST /scenarios/{sid}/publish`, the heavy 15-20 LLM call init is pre-computed and stored in `Scenario.prefab_engine_state`. Session start then only needs 1 LLM call (player appearance) instead of 15-20.

## Key Files
| File | Role |
|------|------|
| `backend/main.py` | All API routes (1100+ lines) |
| `backend/engine/engine.py` | Engine class + `scenario_to_engine()` |
| `backend/engine/inference.py` | LLM inference: language, NPCs, system prompt, guard |
| `backend/engine/call.py` | Groq client wrapper with retry logic |
| `backend/engine/types.py` | Dataclasses: Entity, WorldState, Memory, ContentFilter |
| `backend/ai_service.py` | Async bridge between FastAPI and sync engine |
| `backend/engine/serializer.py` | Engine ↔ dict serialization for Redis |
| `backend/worker.py` | arq background jobs: prefab, session init, per-turn media, session movie |
| `backend/video_utils.py` | Shared ffmpeg concat helper (export + session movie) |
| `frontend/app/src/App.jsx` | Entire frontend (1700+ lines, one file) |
| `frontend/app/src/api.js` | API client |

## Canonical Frontend
`frontend/app/` is the real frontend. `frontend/src/` and `frontend/src/src/` are duplicates/leftovers — do not edit them.

## Environment / Secrets
All secrets are in `.env` at the project root. The Groq API key, JWT secret, and DB passwords are there. **Never commit `.env`.**

## Running Locally
```
docker compose up --build
```
Backend at `:9000`, frontend at `:3000`, nginx at `:80`.

## Known Architecture Decisions
- `time.sleep()` inside `call()` for rate-limit backoff — acceptable because called via `asyncio.to_thread()`
- SSE streaming is "fake-streaming": Groq tokens are buffered internally, guard runs on the full response, then re-chunked in 24-char pieces for the client. This is intentional because the guard requires the complete response.
- Prefab concurrency is capped by `worker.py`'s `PREFAB_JOB_KEY` — a Redis `incr`/`decr` counter with `PREFAB_MAX = 2`, not an in-process `asyncio.Semaphore`. Deliberately cross-process (works across multiple worker replicas, not just within one), unlike a semaphore.
- JWT access tokens are short-lived (30 min, `config.py`'s `jwt_expire_minutes`), backed by a full refresh-token system (`refresh_tokens.py`: issuance, single-use rotation via Redis `GETDEL`, revocation) — not the original 7-day-no-refresh design.

## Do Not Touch
- `backend/alembic/` — migrations are append-only
- `postgres.env`, `redis.env`, `backend.env` — generated/legacy files, do not use as config sources anywhere in the repo, `codex/` included (`config.py`'s `Settings(env_file=".env")` is the one real source of truth).
