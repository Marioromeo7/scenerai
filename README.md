# Scenarai

An AI interactive-fiction platform where the hard problem is cost, not storytelling.

**A naive implementation spends 15–20 LLM calls assembling narrative context before a player
turn can even begin. Scenarai spends 1.** Seven context layers — world state, entity registry,
behavioural reads, pinned facts — are pre-computed once at publish time and stored as a
`prefab_engine_state`. Starting a session patches the player's persona into that prefab in a
single call instead of rebuilding everything from scratch.

That is the whole design. Everything else follows from it.

- **178 tests** across the engine, guards, serializer, and async helpers
- Async FastAPI + SQLAlchemy, PostgreSQL, Redis, Nginx, Docker Compose, Alembic migrations
- A continuity guard and per-character **sovereignty check** on every turn, so characters
  can't reference knowledge they were never given

## Stack

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + Redis + Groq LLMs
- **Frontend**: React + Vite + TanStack Query
- **Infrastructure**: Docker Compose + Nginx

## Quick start

```bash
cp .env.example .env
# Fill in GROQ_API_KEY and change the default passwords in .env
docker compose up --build
```

Open http://localhost — register, create a persona, create a scenario, publish it, then play.

## Services

| Service  | Port | Description              |
|----------|------|--------------------------|
| nginx    | 80   | Reverse proxy (entry)    |
| frontend | 3000 | React + Vite dev server  |
| backend  | 9000 | FastAPI                  |
| postgres | 5432 | Database                 |
| redis    | 6379 | Session cache + engine state |

## Environment variables

See `.env.example` for all required variables. The most important:

- `GROQ_API_KEY` — required for all LLM calls. Get one at [console.groq.com](https://console.groq.com).
- `JWT_SECRET` — must be a long random string in production.
- Database/Redis passwords — change from defaults before any real deployment.

## How it works

1. **Scenario creation**: You write a character and opening scene. The AI generates a title, brief, and tags.
2. **Publishing**: Triggers a background job that pre-computes 7 context layers (world state, entity registry, behavioral reads, pinned facts) using 15–20 LLM calls. Stored in the DB as `prefab_engine_state`.
3. **Session start**: The prefab is patched with your persona in 1 LLM call instead of 15–20.
4. **Turns**: Each player input goes through language detection → translation → preprocessing → system prompt assembly → LLM call → continuity guard → sovereignty check → response.
5. **History**: Engine state is serialized to Redis (12h TTL). Ended sessions are written to `session_logs` in PostgreSQL for resume.

## Production checklist

- [ ] Change all default passwords in `.env`
- [ ] Set `JWT_SECRET` to a cryptographically random value
- [ ] Remove exposed `5432` and `6379` ports from `docker-compose.yml`
- [ ] Add HTTPS (Caddy, Certbot, or cloud load balancer in front of nginx)
- [ ] Set `CORS_ORIGINS` to your actual domain
