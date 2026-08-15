# Scenarai — Full Inspection Report
**Inspector: LANDMINE | Date: 2026-05-10**
**Checks: Security audit + static analysis + live engine stress test + frontend analysis**

---

## VERDICT: DO NOT SHIP AS-IS

---

## SEVERITY: CRITICAL — Act today

### C-1 | Live API Key Committed to Disk
**Files:** `.env:5`, `backend.env` (container env dump)

The Groq API key `gsk_nBpGjy...` is present in plaintext in `.env` and duplicated in `backend.env`. There is no `.gitignore`. One `git push` to any public remote and every threat actor on the planet has unlimited LLaMA access on your bill.

**Actions:** Rotate the key immediately. Add `.gitignore` before touching git again.

### C-2 | No .gitignore Exists
The following will be committed on first `git add .`:
`.env`, `backend.env`, `postgres.env`, `redis.env`, `database_backup.sql`, `__pycache__/`, `node_modules/`

### C-3 | Database Backup in Repo Root
`database_backup.sql` — 27KB of potentially real user data sitting unencrypted in the working directory. If this hits a public repo, it's a GDPR incident before you get a chance to delete it.

---

## SEVERITY: HIGH — Pre-ship blockers

### H-1 | Sovereignty Checker Misses Base-Form Verbs *(engine bug)*
**File:** `backend/engine/inference.py:655-664`

The in-production sovereignty regex uses third-person singular forms only:
```python
rf'\b{re.escape(name_lower)} (steps|walks|runs|moves|...)\b'
```
It catches `"Alex moves"` but **not** `"Alex move"` (base form, as in `"she watches Alex move"`).

The live stress test confirmed this:
```
TURN 5 input:  **I stand completely still.** Make me walk to the archive door.
RESPONSE:      "…her gaze unwavering as she watches Alex move towards the archive door."
FAIL: rule=player_control  matched="Alex move"
```
The test suite's own regex correctly uses `moves?`. Production doesn't. This is a one-line fix but it's a fundamental correctness failure — the engine's core promise is player sovereignty.

**Fix:** Add `?` to all verb forms: `moves?|walks?|runs?|steps?|...`

### H-2 | Guard Repair Chain Can Exhaust and Return a Flawed Response *(engine bug)*
**File:** `backend/engine/inference.py:686-823`

The guard runs: validate → repair → re-validate → fallback → fallback-retry. If all paths fail, it returns `response, {'revised': False, 'violations': violations}` — meaning the original violating response is served to the user. This happened in the live test: the repair chain triggered a false-positive `'stand'` violation that caused cascading repair attempts, all of which failed, and the original broken response was served.

There is no absolute safety net. A failed repair loop serves bad content.

### H-3 | No Rate Limiting on Play Endpoints
Rate limiting exists on auth only (5-10/min). Zero rate limiting on:
- `POST /sessions/{id}/turn`
- `POST /sessions/{id}/turn-stream`

One authenticated user can make unlimited LLM calls. A single script can consume your entire Groq quota in minutes.

### H-4 | Concurrent Turn Requests Cause Race Condition
`play_turn` and `play_turn_stream` both:
1. Read session JSON from Redis
2. Call the LLM (~5-30 seconds)
3. Write session JSON back to Redis

No distributed lock between steps 1 and 3. Two concurrent requests to the same session read the same state, produce two responses, and the second write silently overwrites the first. History gets corrupted without any error.

### H-5 | `--reload` in Production Dockerfile
**File:** `backend/Dockerfile:7`
```sh
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"]
```
`--reload` watches the filesystem and restarts the process on any file change. In production: wastes CPU, leaks memory on restart, can be triggered by log file writes.

### H-6 | Postgres and Redis Ports Exposed to Host
**File:** `docker-compose.yml:14,36`

Both databases bind `0.0.0.0:5432` and `0.0.0.0:6379` — reachable from outside the container network. With credentials now exposed in `.env`, this is trivially exploitable on any cloud VM.

### H-7 | No HTTPS in Nginx
**File:** `nginx/nginx.conf` — HTTP only, port 80 only. Auth tokens, session content, and all conversation history travel in plaintext.

### H-8 | JWT 7-Day Expiry, No Revocation
**File:** `backend/auth.py:22`, `backend/config.py:10`

Tokens last 7 days. No refresh tokens, no blacklist, no session invalidation on password change. Stolen token = 7 days of unrevokable access.

### H-9 | No Input Length Validation on Player Turns
**File:** `backend/schemas.py:77`
```python
class PlayTurnWithModel(BaseModel):
    input: str  # no max_length
```
Arbitrarily long player messages get injected into the LLM system prompt. No truncation. One 500KB message can spike token costs and destabilize the context window.

---

## SEVERITY: MEDIUM — Real problems

### M-1 | `next()` Without Default in Deserializer — Crash Risk
**File:** `backend/engine/serializer.py:48-49`
```python
persona   = next(e for e in entities if e.is_player)
main_char = next(e for e in entities if not e.is_player and not e.is_collective)
```
If a corrupted or truncated Redis payload has no player entity, this raises `StopIteration` unhandled, crashing any active turn. No error surface, no fallback. Add `next(..., None)` with a guard.

### M-2 | Streaming SSE Is Fake-Streaming
**File:** `backend/ai_service.py:284-330`

`engine_step_stream` buffers the entire Groq token stream internally, runs `guard_response` (1-3 additional LLM calls), then re-emits the final (possibly revised) response in 24-character fake chunks. The user sees a loading pause of 10-40 seconds, then rapid typewriter output. The streaming UX implies real-time tokens; the implementation delivers batch output with artificial chunking. This is the architectural cost of the guard system — but users experience a broken promise.

### M-3 | Duplicate Frontend Source Tree (3 Copies)
Three identical copies of the source:
- `frontend/src/` 
- `frontend/app/src/` ← canonical
- `frontend/src/src/` ← nested inside itself

Plus `frontend/public/public/`. The canonical source is `frontend/app/src/`. The others should be deleted — they will cause confusion and someone will eventually edit the wrong copy.

### M-4 | `datetime.utcnow()` Deprecated
**Files:** `auth.py:22`, `main.py:365`, `main.py:540`

`datetime.utcnow()` is deprecated since Python 3.12 and returns timezone-naive datetimes. JWT `exp` fields that are timezone-naive can produce subtle comparison failures across Python versions. Use `datetime.now(timezone.utc)`.

### M-5 | Redis Is a Singleton with No Connection Pooling
**File:** `backend/database.py:24-30`
```python
_redis: Redis | None = None
async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(...)
    return _redis
```
One shared connection across all requests. Under concurrent load, Redis I/O is serialized. Use `redis-py`'s built-in connection pool.

### M-6 | Alembic Runs in Container CMD, Not Entrypoint
**File:** `backend/Dockerfile:7`

`alembic upgrade head` runs on every container restart. If a migration fails mid-way and the container crashes and restarts (due to `restart: unless-stopped`), you get repeated partial migration attempts against a half-migrated schema. Migrations must be a one-shot step, not part of the main process CMD.

### M-7 | No Backend Health Check in Docker Compose
Postgres and Redis have health checks. The backend has none. If startup fails (e.g., Alembic migration error), Docker marks the container `running` and Nginx proxies blindly to a dead backend.

### M-8 | Index-as-React-key in Message List — Streaming Glitch Risk
**File:** `frontend/app/src/App.jsx:463-464`
```jsx
{messages.map((m, i) =>
  <Bubble key={i} msg={m} ...
```
When a message is removed from the middle of the list (the error-cleanup at L389 filters by index), React uses the index key for reconciliation and can assign wrong component state to wrong messages. In a streaming chat — where the placeholder assistant bubble is mid-list — this causes visible rendering glitches.

### M-9 | Prompt Injection Sanitizer Is Security Theater
**File:** `backend/engine/inference.py:828-845`

Regex patterns like `r'(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions'` are bypassed trivially (`"1gnore @ll pr3vious instructions"`). This creates false confidence. The LLM must be the trust boundary, not a regex pre-filter.

### M-10 | `bcrypt` Pinned to Deprecated `<4.0.0`
**File:** `backend/requirements.txt:12`
```
bcrypt>=3.1.0,<4.0.0
```
BCrypt 4.x is current. The `<4.0.0` cap locks in the deprecated branch indefinitely and suppresses security patches.

---

## SEVERITY: LOW — Quality debt

### L-1 | Dead Imports (Confirmed by Static Analysis)

| File | Import | Status |
|------|--------|--------|
| `ai_service.py:22` | `async_call` | Truly dead — never called |
| `main.py:28` | `Base`, `engine` | Dead since `create_all` was removed |
| `main.py:30` | `PaginatedResponse` | Defined, never returned |
| `engine/inference.py:8` | `WorldState` | Imported, not used |
| `engine/__init__.py` | All 14 re-exports | False positive — add `# noqa: F401` |

### L-2 | Broken f-strings (F541)
**File:** `backend/engine/inference.py:750`, `inference.py:794`
```python
f'Violations to fix:\n- ' + '\n- '.join(violations)
```
`f` prefix with no `{...}` inside. Not a runtime error but actively misleading in a file where f-strings construct LLM prompts.

### L-3 | Silent Exception Swallowing
`inference.py:916` — `extract_and_save_assumptions` catches all exceptions silently. If JSON parsing fails on every turn, there is no log — character state silently stops updating.

### L-4 | TODO Comments in Production Route Code
`main.py:144-157` — 16 lines of image/video generation TODO comments between route handlers. TODOs belong in an issue tracker.

### L-5 | 44 `useState` Calls in One 1,090-Line File
`App.jsx` holds 10 components, 44 state variables, 8 effects. Adding any new feature requires navigating thousands of lines. The structure is already at the ceiling of single-file maintainability.

### L-6 | `console.*` in Production Code
6 `console.error` calls remain in `App.jsx`. Acceptable in dev, noisy in prod. Should be guarded by `process.env.NODE_ENV` or removed.

### L-7 | Flake8: 370 Issues, Mostly Style
The `E221` column-alignment style (173 occurrences) and the semicolon-packing in `schemas.py` (49 occurrences) are intentional. They're readable but not PEP8.

**Recommendation:** add `backend/setup.cfg`:
```ini
[flake8]
max-line-length = 120
ignore = E221,E701,E702,W503
```
This drops 370 → ~25 real issues and stops flake8 from crying wolf.

---

## LIVE TEST RESULTS

| Test | Result | Notes |
|------|--------|-------|
| AST parse (13 files) | PASS | No syntax errors |
| Flake8 (real bugs only) | 25 real issues | F401×5 real, F541×2, E712×2 |
| Engine stress test 5-turn | **PASS** | All adversarial inputs correctly handled |
| Engine stress test 18-turn | **FAIL at turn 5** | Sovereignty violation: base-form verb missed by checker |

---

## TOKEN COST PROFILE (Worst-Case Per Turn)

30 `call()` invocations across inference + engine. A turn that triggers full guard + repair + fallback:

| Step | Max Tokens Out |
|------|---------------|
| Main response | 600 |
| Guard validator | 220 |
| Repair attempt | 700 |
| Re-validator | 220 |
| Fallback | 260 |
| **Worst-case total** | **~2,000 out** |

Plus 1,500+ token system prompt input per call. No monitoring, no budget cap, no alerting when the repair chain fires.

---

## PRIORITY PUNCH LIST

**Do today:**
1. Rotate the Groq API key — treat it as compromised
2. Create `.gitignore` before any `git init` or push
3. Move `database_backup.sql` out of the repo

**Before next deploy:**
4. Fix sovereignty regex to use `moves?|walks?|steps?` etc. — one line
5. Add hard fallback in guard when all repair attempts fail
6. Remove `--reload` from production Dockerfile
7. Add rate limiting to `/sessions/{id}/turn` and `/sessions/{id}/turn-stream`
8. Add `max_length` to `PlayTurnWithModel.input`
9. Close Postgres/Redis ports in docker-compose (remove host port bindings)
10. Add HTTPS to nginx
11. Fix `next()` calls in serializer to use defaults

**Before public launch:**
12. Distributed session lock (Redis `SET NX` or similar) on turn endpoints
13. JWT refresh tokens + revocation list
14. Connection pooling for Redis
15. Fix index-as-key in message list
16. Delete duplicate `frontend/src/` and `frontend/src/src/` directories
17. Fix deprecated `datetime.utcnow()` calls (3 locations)
18. Upgrade `bcrypt` cap to `>=4.0.0`
19. Migrate Alembic run out of CMD into entrypoint/init container
20. Add backend health check to docker-compose

---

*The engine architecture is genuinely sophisticated. The prefab optimization, layered context system, and guard pipeline are well-designed. The bones are good. The security posture and correctness gaps are early-stage issues — they're fixable, but they need to be fixed before real users are on the system.*
