# Scenarai

An AI interactive-fiction platform where the hard problems are cost, consistency, and control —
not "can an LLM write a scene."

**A naive implementation spends 15–20 LLM calls assembling narrative context before a player
turn can even begin. Scenarai spends 1.** Seven context layers — world state, entity registry,
behavioural reads, pinned facts — are pre-computed once at publish time and stored as a
`prefab_engine_state`. Starting a session patches the player's persona into that prefab in a
single call instead of rebuilding everything from scratch.

That's the text engine. There's a full generative-media pipeline underneath it too — Stable
Diffusion image generation, inpainting, per-character consistency scoring, and local TTS — with
its own empirical research behind the prompt/generation strategy, not just an API call bolted on.

- **473 backend tests**, all live-verified against a running stack, not just green in CI
- A continuity guard and per-character **sovereignty check** on every turn — an independent
  validator model, not just a system-prompt instruction, blocks the narrator from ever moving,
  feeling, or deciding for the player
- A **judge harness** scoring every generated turn on 5 independent rubric axes, with a real
  statistical stop-condition for run-to-run noise
- A **load-test harness** with a three-category failure taxonomy (`hard_error` /
  `latency_breach` / `silent_bad_state`) — built specifically to catch the failure mode most
  harnesses miss: HTTP 200 over state that's silently wrong

## Stack

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + Redis + Groq LLMs
- **Generative media**: Stable Diffusion 1.5 (diffusers) + inpainting, CLIP + BLIP-VQA for
  consistency scoring, Kokoro-82M local TTS, ffmpeg for per-turn video
- **Frontend**: React + Vite + TanStack Query
- **Infrastructure**: Docker Compose + Nginx + Alembic migrations

## Quick start

```bash
cp .env.example .env
# Fill in GROQ_API_KEY and change the default passwords in .env
docker compose up --build
```

Open http://localhost — register, create a persona, create a scenario, publish it, then play.

## How the text engine works

1. **Scenario creation**: you write a character and opening scene. The AI generates a title, brief, and tags.
2. **Publishing**: triggers a background job that pre-computes 7 context layers (world state, entity registry, behavioral reads, pinned facts) using 15–20 LLM calls. Stored as `prefab_engine_state`.
3. **Session start**: the prefab is patched with your persona in 1 LLM call instead of 15–20.
4. **Turns**: each player input goes through language detection → translation → preprocessing → system prompt assembly → LLM call → continuity guard → sovereignty check → response.
5. **Character integrity**: NPCs carry a "resolve" value per boundary (`never_does`) that only erodes through earned in-scene emotional weight, never through player argument — a rope worn down by behavior, not a rule argued open.
6. **History**: engine state is serialized to Redis (12h TTL). Ended sessions are written to `session_logs` in PostgreSQL for resume.

## Generative media pipeline

Every turn can also render a portrait image, narrated audio, and a stitched video segment of the
scene. The interesting part isn't calling Stable Diffusion — it's the empirical work behind
*how* it's prompted and verified, run at real scale (thousands of generations, not a handful of
manual checks):

- **Prompt field-set testing**: shorter, single-attribute-per-clause prompts measurably beat
  flowing prose on every metric — not assumed, A/B tested.
- **A canonicalization experiment that backfired, and was kept documented rather than hidden**:
  using an LLM to rewrite ambiguous color terms into SD1.5's preferred vocabulary (`"dark green"`
  → `"olive"`) measurably made accuracy *worse*, not better — a real negative result that changed
  the pipeline design (compound colors are now split into separate clauses instead of rewritten).
- **Attribute-level consistency checking via VQA**, not caption diffing: whole-image captioning
  (BLIP) never once mentioned eye color or a character's scar across 3,000 generations in the
  field-set experiment — the wrong tool for the job. Switched to direct VQA questions
  (`blip-vqa-base`) against each attribute individually.
- **Incremental, attribute-locked generation**: SD1.5's known attribute-binding weakness (it
  doesn't reliably satisfy every requested attribute at once) is worked around by locking
  attributes onto a seed one at a time — generate, VQA-check every attribute locked so far (not
  just the newest), advance only on a pass — directly measuring retry cost, regression rate, and
  where chains typically stall.
- **A discovered hard limit, designed around rather than ignored**: multi-character scenes with
  2+ named subjects hit a real ceiling on per-character attribute binding (tested in tiered B1–B4
  experiments isolating background complexity from character count). Scenes needing a crowd are
  routed to a generic crowd descriptor instead of attempting individual likeness — a case where
  the fix was accepting the model's limit and designing the product around it, not fighting it.
- **Inpainting as a targeted second pass**: when a specific attribute check fails, only that
  region gets regenerated (via `face_detect` landmarks for eyes, heuristic regions for
  hair/clothing) instead of re-rolling the whole image — more sample-efficient than whole-image
  rejection sampling, refined after a direct visual audit found the naive fixed-band eye mask
  missing the actual eyes in 5 of 7 sampled failures.
- **Deterministic, reproducible seeds** end to end — every image is regenerable from
  `(prompt, negative_prompt, seed)`, computed from a stable hash (not Python's built-in `hash()`,
  which is randomized per-process and quietly isn't reproducible across restarts — caught live).
- **CLIP-based consistency scoring** (image-image and text-text cosine similarity) as the fully
  local, no-API-key measure of whether a new generation still matches a character's canonical
  reference.
- **Local TTS narration** (Kokoro-82M, CPU-pinned deliberately — the GPU is already contended by
  image generation) and per-turn video assembly via ffmpeg, concatenated into a whole-session
  movie.

## Safety, evaluation & reliability engineering

- **Sovereignty enforcement is two-layered**: a repair pass first tries to fix a drafted turn
  in-character, and if that repair itself still fails validation, a hard-blocked fallback ships
  instead — the player's agency is never silently overridden.
- **Judge harness** (`judge/`): five golden scenarios, each targeting one specific engine edge
  case (collective NPCs, ambiguous shared pronouns, sovereignty-stress player input, long-session
  continuity drift across a compression cycle, content-filter adherence), scored by a *separate*
  model family from the engine's own generation models — avoiding same-model self-grading bias —
  with an explicit statistical stop-condition (not "looks fine, ship it") before trusting a
  measured noise floor.
- **Load-test harness** (`loadtest/`): finds the concurrency level where the backend actually
  breaks, and classifies *how* — hard errors, threshold latency breaches (tracked, not treated as
  fatal, since Groq's own backoff on rate limits is correct behavior), and silent bad state
  (HTTP 200 over a stuck or corrupted session — the category most load-testers never check for).
- **Verified in production, not just in review**: a live-measured login timing side-channel
  (236ms gap between "no such account" and "wrong password," enough to enumerate registered
  emails) was found, fixed, and re-measured end-to-end down to 0.2ms. A backup script's
  `pg_dump | gzip` pipeline was found to silently mask a failed backup as a successful one under
  POSIX shell's pipeline exit-status semantics — confirmed live, fixed before it mattered.

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

## Production checklist

- [ ] Change all default passwords in `.env`
- [ ] Set `JWT_SECRET` to a cryptographically random value
- [ ] Remove exposed `5432` and `6379` ports from `docker-compose.yml`
- [ ] Add HTTPS (Caddy, Certbot, or cloud load balancer in front of nginx)
- [ ] Set `CORS_ORIGINS` to your actual domain

---

Built by Mario George — [LinkedIn](https://www.linkedin.com/in/mario-hossam-mitry-george) · [GitHub](https://github.com/Marioromeo7) · [metrymarioromeo546@gmail.com](mailto:metrymarioromeo546@gmail.com)
