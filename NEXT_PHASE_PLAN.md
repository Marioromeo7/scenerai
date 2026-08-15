# Scenerai — Next Phase Plan

Context for whoever (human or Claude Code) picks this up: the backend turn-loop
(`engine/inference.py` guard/sovereignty pipeline) is solid — fail-closed, tested,
well-reasoned. The scaffolding around it (init reliability, job infra, frontend
tree, image/video) is not at the same bar. This doc is a map of what to fix,
plus three new subsystems to build: a quality judge, a Flutter/Docker-Android
load-test harness, and an image/video consistency experimentation environment.

Work top to bottom. Section 1 is prerequisite hardening — do it before load
testing (Section 3), or the load test will just rediscover Section 1 the hard
way.

---

## 1. Fix Map (repo audit)

### 1.1 Reliability — do first

| # | Issue | Where | Fix |
|---|---|---|---|
| 1 | Prefab failure is silent + permanent. `publish_scenario` commits `is_published=True` *before* the background prefab job runs. If `_prefab_engine_bg` fails (bad classification call, Groq outage, or `PREFAB_MAX` concurrency gate silently returning), `prefab_engine_state` stays `None` forever. Every session creation then hits `RuntimeError("no prefab")` with no creator-facing signal. | `main.py` `create_scenario`/`publish_scenario`, `_prefab_engine_bg` | Add `prefab_status` enum column (`pending`/`ready`/`failed`) to `Scenario`. Surface it in `ScenarioOut`. Add `POST /scenarios/{id}/retry-prefab`. Frontend polls/shows status before allowing publish to be "live." |
| 2 | NPC classification calls in `infer_characters`/`scan_opening_for_npcs` have no fallback. One exception in `_is_collective_group`, `_is_role_descriptor`, etc. propagates via `f.result()` and kills the entire init — unlike the turn-loop, which is fail-closed at every stage. **Confirmed in practice: pronoun inference (`_extract_pronouns`) is the call that actually fails, character description rarely does** — prioritize the fallback there first, not spread evenly across every classification call. | `engine/inference.py` | Wrap each per-NPC classification in try/except with a sane default (e.g. treat as individual NPC, log + continue) instead of letting one bad call abort the whole scenario. |
| 3 | Session state loss on disconnect. `SessionLog` (Postgres) is only written on explicit `DELETE /sessions/{id}`. Until then state lives only in Redis (`SESSION_TTL` = 12h). A crashed client, dropped connection, or Redis eviction under memory pressure loses the entire conversation with no recovery path. | `main.py` `end_session`, session Redis keys | Checkpoint `engine_state` to `SessionLog` incrementally (e.g. every N turns or on each turn via the existing streaming loop), not only on clean disconnect. |
| 4 | `BackgroundTasks` doesn't scale. All async work (prefab, init, image/video later) runs in-process via FastAPI's `BackgroundTasks`, tied to one uvicorn worker's event loop. No retry, no dead-letter, no queue depth visibility. | `main.py` throughout | **Decided: migrate to `arq`** (Redis-based, matches existing stack). This is the change the load test in Section 3 will most directly validate. |
| 5 | Fake streaming. `engine_step_stream` fully buffers the guarded/translated response, then fakes a typewriter effect in fixed 24-char chunks. Docstring implies real token streaming; it isn't. | `ai_service.py` | Either rename to reflect reality, or restructure guard/translate to operate on partial spans so real streaming reaches the client. Lower priority than 1–4. |
| 6 | `guard_response` re-validation uses `validator_payload.replace(f'ASSISTANT DRAFT:\n{response}', ...)` — `str.replace` swaps *every* occurrence. If `response` text coincidentally recurs elsewhere in the payload (echoed pinned fact, player quoting it back), you get an unintended double substitution. | `engine/inference.py` | Rebuild the payload from structured parts instead of string substitution. |

### 1.2 Frontend hygiene

| # | Issue | Fix |
|---|---|---|
| 7 | Three copies of the frontend: `frontend/src/`, `frontend/src/src/`, `frontend/app/src/` — only `frontend/app/` is actually built (`docker-compose.yml` → `frontend/Dockerfile` → `COPY app/ .`). The other two are diverged dead code (`frontend/src/App.jsx` is missing features `frontend/app/src/App.jsx` has). Also a nested `frontend/public/public/`. | `git rm -r frontend/src frontend/public/public`. Delete `frontend/Dockerfile` too if `frontend/app/Dockerfile` becomes the single source, and simplify `docker-compose.yml` build context accordingly. |

### 1.3 Schema/infra gaps (blockers for Section 4)

| # | Issue | Fix |
|---|---|---|
| 8 | `Scenario` has no `image_url`/`video_url` columns. The TODO comment in `main.py` (lines ~168–186) references fields that don't exist. `image_seed` is a random string consumed only by a local deterministic SVG gradient placeholder in `App.jsx` (`CardImage`) — no generation call exists anywhere. | Needs a real migration + storage decision before any generation agent is wired in (see Section 4). |
| 9 | No blob storage layer exists at all (no S3/R2 config, no CDN). Video especially cannot go in a Postgres JSON/text column. | **Decided: Cloudflare R2.** Zero egress fees (this matters — assets get streamed repeatedly, not stored once and forgotten), permanent 10GB/month free tier, S3-compatible API. Env vars: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`. |

**Stop condition for Section 1:** items 1–4 done and load-tested individually (small concurrency, not the full swarm) before moving to Section 3.

---

## 2. Turn-Quality Judge

**Purpose:** a separate, offline evaluation harness — distinct from `guard_response`.
`guard_response` is a hard, binary, production-blocking constraint enforcer (runs
on every real turn, fails closed). The judge is a graded, non-blocking quality
scorer that runs against recorded/scripted transcripts to catch regressions when
`engine.py`/`inference.py`/prompts change. Do not merge these two systems.

### 2.1 Rubric

Score each turn 1–5 on independent axes (avoid a single blended score — you want
to know *which* axis regressed):

- **Continuity fidelity** — does the turn respect pinned facts / character sheet
  established at init? (This overlaps with what the continuity guard already
  checks in production — the judge re-checks it independently as a regression
  signal, since the guard itself could have a bug.)
- **Sovereignty adherence** — same idea, independent re-check of player-agency
  boundaries.
- **Voice consistency** — does the turn match `char_personality`, not drift
  toward generic assistant-voice?
- **Pacing** — is turn length/scene pacing appropriate, not rushed or bloated?
- **Safety/content-filter adherence** — given the scenario's `content_filter`
  setting, did the turn respect it?

### 2.2 Harness design

- Build a fixed set of **golden scenarios** with scripted player-turn
  sequences, covering edge cases: collective NPCs, ambiguous pronouns, player
  attempting to narrate the NPC (sovereignty stress test), long sessions
  (continuity drift stress test). Count and turn-depth per scenario: not
  decided — size this to actual edge-case coverage needed, not a round number.
- Replay each scripted sequence against the real engine (`engine_init` →
  `engine_step` loop), capture every generated turn.
- Run a **separate judge model — decided: `openai/gpt-oss-120b` via Groq**
  (same `GROQ_API_KEY` already in use for the engine, no new setup). Chosen
  over a locally-quantized 70B because 4.9GB VRAM can't hold a 70B model
  without heavy CPU offload — free, fast, materially stronger, and a
  different model family from the engine's generation model, which is what
  actually matters for avoiding self-grading bias. Run this over each turn
  against the rubric above; output structured JSON scores + a short rationale.
- Aggregate per-scenario and per-axis. Store results as timestamped runs
  (markdown or JSON in `docs/eval-runs/`) so you can diff runs across engine
  changes — same "explicit stop conditions, honest negative results" pattern
  you used for BallNet.
- **Stop condition:** define a numeric floor per axis below which a change is
  flagged as a regression before merge. Set the actual floor from the first
  baseline run's real scores, not a guessed number — write it down explicitly
  once you have it, don't leave it as a vibe check.

### 2.3 What NOT to do

- Don't feed judge scores back into the production guard pipeline — different
  purpose (offline quality trend vs. online hard safety).
- Don't use the same model for engine generation and judging on the same
  transcript without at least varying temperature/prompt — same-model self-eval
  is a known blind spot (tends to rate its own output generously).

---

## 3. Flutter / Docker-Android Load Test — Breakpoint Reporting

**Goal:** build a Flutter client that walks the two real user flows end to
end, run growing numbers of it in Docker/Android emulators, and find the exact
concurrency level and failure category where the backend breaks — reported
per virtual user so you know *which* one (and which step) triggered it.

No existing app to extract traffic from — Flutter is being built specifically
to embody these flows, so flow-scripting and load-testing happen together, not
in sequence from a captured baseline.

### 3.1 The two flows to implement

- **Player flow:** login → browse/select a ready (published) scenario → create
  session → poll `/sessions/{id}/status` until ready → open SSE stream → send
  a scripted sequence of turns → end session.
- **Creator flow:** login → create scenario → publish → wait on prefab status
  (ties directly to fix map item 1) → optionally preview.

**Ramp mix — decided: ~90% player / ~10% creator.** This matches realistic
live-app traffic and deliberately stresses the player path harder, since
that's the higher-volume, latency-sensitive path.

### 3.2 Two-tier approach (don't run everything as full emulators)

Full Android emulators are heavy (RAM/CPU per instance) — running hundreds in
Docker for pure load generation is wasteful and will bottleneck the *test
harness* before it bottlenecks the *backend*, muddying results.

- **Tier A — correctness (small N, real emulators):** a handful of real
  Android emulator instances (`docker-android` or similar images) running the
  actual Flutter app, to confirm both flows work correctly against the real
  API under real mobile network conditions (use `tc`/`toxiproxy` inside the
  container to simulate latency/packet loss — mobile clients need this,
  emulated WiFi is too clean).
- **Tier B — scale (large N, headless):** once Tier A confirms both flows are
  implemented correctly, drive the *same* call sequences at scale without full
  UI/emulator overhead — this is what actually finds the breakpoint. Ramp
  virtual-client count over time at 90/10 player/creator mix.

### 3.3 Failure definition — decided: track all three categories, separately

A run is not just pass/fail. Every virtual client reports which category (if
any) it hit, so the aggregate report can show which failure mode dominates at
which concurrency — these are genuinely different problems with different fixes:

1. **Hard error** — any non-2xx response from the API.
2. **Latency breach** — response time crosses a defined threshold (pick the
   threshold per endpoint before the first run — e.g. session init vs. a
   single turn have very different acceptable latencies; don't use one global
   number).
3. **Silent bad state** — the API returned 200 but the session ends up wrong
   (stuck on `"initializing"`, history missing, engine_state lost) — this is
   the category most likely to catch fix-map item 3 (session loss on
   disconnect/Redis eviction) and item 1 (silent prefab failure), since both
   fail *without* an error response.

### 3.4 Per-client structured reporting

```json
{
  "client_id": 47,
  "flow": "player",                        // or "creator"
  "concurrency_at_failure": 140,
  "failure_category": "silent_bad_state",  // hard_error | latency_breach | silent_bad_state
  "failed_step": "session_init_timeout",   // e.g. auth_429, stream_disconnected,
                                            //      prefab_missing, redis_eviction
  "latency_ms_p50_before_failure": 820,
  "timestamp": "...",
  "error_detail": "..."
}
```

Aggregate into a **failure-rate-vs-concurrency curve per category**, and
report the first concurrency tier where each category crosses its threshold,
plus the dominant failure signature at that tier. Expect `hard_error`
(`session_init_timeout`, `prefab_missing`) to surface first if fix-map items
1/2/4 aren't done yet, and `silent_bad_state` to surface if item 3 isn't — the
test should confirm which actually manifests first in practice, not just
which one the fix map predicts.

**Open question, not yet decided:** the latency threshold per endpoint (item
2 above) needs real numbers before the first run. Don't guess these — run a
low-concurrency baseline first (1–5 clients) and set thresholds relative to
that baseline (e.g. "3x baseline p50"), rather than picking an absolute number
out of the air.

**Stop condition:** report is a single artifact — three failure-rate curves
(one per category) plus the failure signature table — not a wall of raw logs.
If you can't answer "at what concurrency does it break, in which category,
and why" in one glance, the harness isn't done.

---

## 4. Image + Audio "Primitive Video" Pipeline (default) — Real Video Generation Deferred

**Problem to solve:** the same character/scene needs to look consistent across
multiple generations without resending a long descriptive prompt (redundant
input tokens) on every call — and given the goal is a real product, cost has
to be sustainable, not a research-budget assumption.

**Decided: real video-generation vendors (Kling/Runway/Veo/etc.) are deferred.**
Reason is cost, confirmed by direct research, not a guess — there is no free
programmatic video generation API anywhere (Kling's free credits are web-app
only, not API-accessible; Veo has no free API tier at all), and even paid
tiers run roughly $0.50–$1.50 for a single 10-second clip, before the 2–3
retries most generations need in practice. That's not viable as a per-scenario
cost on a product with unpredictable scenario volume. The default production
path is Section 4.3 below — real video generation becomes an optional future
upgrade (Section 4.5) only once there's revenue or budget to justify it.

### 4.1 Core design: compute once, reuse cheaply (still applies)

Mirror the existing prefab pattern (compute expensive stuff once at publish
time, reuse cheaply per-turn) — this holds regardless of the video decision,
since it governs *image* consistency, which the primitive pipeline still needs:

1. **One-time "visual profile" generation at scenario publish** (alongside
   `engine_prefab`): one LLM call produces a **compact structured descriptor**
   — fixed schema, not prose (hair, eyes, build, clothing, palette, art style,
   distinguishing feature). Store as new `Scenario.visual_profile` JSON column
   (see fix map item 8). Target token count for this schema is itself an
   experimental question (see 4.2), not a fixed number.
2. **Deterministic reconstruction, no LLM call needed per-image:** build the
   actual image-generation prompt by template-formatting the structured fields
   — plain string concatenation, zero token cost beyond what the image model
   itself charges for its prompt.
3. **Consistency from seed + reference image, not longer text** (SD 1.5 supports
   this locally — see 4.3):
   - Fixed numeric `seed` per scenario, stored alongside `visual_profile`.
   - Generate one **canonical reference image** at publish time; if a scene
     needs more than one image (a short beat sequence, see 4.3), use
     image-to-image conditioning off the canonical image instead of
     re-describing the character in text each time.

### 4.2 Experimentation harness (build this before wiring into production)

Purpose: find the *minimum* structured-profile length that still holds visual
identity, so you're not guessing at the token/consistency tradeoff.

- Script: generate the same character N times (e.g. N=20) varying only
  seed/temperature, using a candidate profile length (start short, e.g. 6
  fields).
- Measure **consistency** two ways, since you're running this locally at low
  VRAM and don't need to pick just one:
  - Free/local-friendly: run each generated image through Gemini's free-tier
    image understanding (same call as the hallucination check in 4.3) and
    diff the description against the intended `visual_profile` fields —
    readable, debuggable, costs nothing.
  - Optional, more numeric: CLIP image-embedding cosine similarity between
    each generation and the canonical reference, if you want a plottable
    curve rather than reading diffs by hand.
- Measure **cost** via prompt token count per generation.
- Repeat across a few profile lengths/detail levels, and pick the point where
  consistency plateaus — i.e. where adding more descriptive detail stops
  improving results. That plateau point is your production profile schema.
- Write this up with the same explicit-stop-condition, honest-negative-result
  format as the BallNet scaling docs — e.g. "profile length beyond N fields
  produced no visible consistency gain, not worth the token cost" is a valid,
  useful conclusion even if it's not the one you hoped for.

### 4.3 Default production path: local image + local audio + ffmpeg stitch

No video-generation model involved. A still image with a slow pan/zoom,
voiced narration, and audio-driven duration — the standard visual-novel
convention, not a downgrade from real video. It also has a consistency
advantage real video generation doesn't: there's only one image, so there's
no frame-to-frame drift to manage.

**Components, all free and local at 4.9GB VRAM:**

| Component | Tool | VRAM | License | Notes |
|---|---|---|---|---|
| Image | Local Stable Diffusion 1.5 | fits comfortably | **Resolved:** CreativeML OpenRAIL-M — confirmed commercial-use permitting, no revenue threshold (unlike SD 3/3.5, which added a $1M enterprise threshold). Only restriction is behavioral (no illegal/harmful content generation), and if redistributed as a service you must pass the license notice to your users. Safe to ship. | `stable-diffusion-v1-5/stable-diffusion-v1-5` on HF — the old `runwayml/` path is deprecated |
| Narration audio | Kokoro-82M | ~2-3GB or CPU | Apache 2.0, fully commercial | `pip install kokoro-onnx`, 54 preset voices, no cloning |
| Stitching | ffmpeg (`zoompan`/`xfade`) | none (CPU) | — | No ML dependency at all |
| Hallucination check | Gemini API (free tier, image-in/text-out) | — | — | See below |

**Pipeline:**

1. Prefab/publish completes → generate canonical image(s) locally (SD 1.5,
   fixed seed from `visual_profile`).
2. Generate narration audio locally (Kokoro) from the scenario's greeting/key
   dialogue text. No hallucination risk here — Kokoro renders exactly the text
   you give it, there's nothing to fact-check.
3. Image → text via Gemini API (free tier): describe what was actually
   generated, diff against `visual_profile` to flag hallucinated or missing
   attributes before the image ships.
4. `ffmpeg` stitches image(s) + audio → `.mp4`, duration driven by the audio
   track length:
   ```bash
   ffmpeg -loop 1 -i scene.png -i narration.wav \
     -filter_complex "[0:v]scale=1920:1080,zoompan=z='min(zoom+0.0005,1.1)':d=125:s=1920x1080[v]" \
     -map "[v]" -map 1:a -shortest -c:v libx264 -c:a aac output.mp4
   ```
5. Store the result once in **Cloudflare R2** (fix-map item 9 — decided,
   see setup below), stream it on every subsequent session — same
   generate-once, reuse-forever pattern as the engine prefab.
6. **Trials/retest:** regenerate N times with the same seed/profile, rerun
   step 3 each time, and confirm the *descriptions* stay consistent across
   runs. This is your consistency check for production images, not just the
   4.2 experimentation harness — same mechanism, reused.

**Free-tier setup reference:**

| Service | Covers | Sign-up | Env var |
|---|---|---|---|
| Google AI Studio / Gemini API | Image-to-text hallucination check; optional cloud image backup (Nano Banana, ~500/day free) | `aistudio.google.com` → "Get API key" → "Create API key" (no card) | `GEMINI_API_KEY` |
| Hugging Face | Downloading SD 1.5 weights; optional Flux Schnell fallback | `huggingface.co/join` → Settings → Access Tokens → New token (Read scope) | `HF_TOKEN` |
| Local SD 1.5 | Primary image generation | `pip install diffusers transformers accelerate torch` | none |
| Kokoro | Narration audio | `pip install kokoro-onnx` | none |
| ffmpeg | Stitching | system package install | none |
| Cloudflare R2 | Storing generated image/audio/video output | Cloudflare dashboard → R2 → Create bucket → Manage API tokens → Create API token | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` |

### 4.4 Multi-image sequences (optional extension, not required to ship)

If a single static shot feels too thin for a given scenario, generate 2-4
images across story beats and crossfade between them (`xfade` in ffmpeg)
instead of one `zoompan`. This is where 4.1's seed + reference-image
consistency mechanism actually earns its keep — each image in the sequence
needs to look like the same character, which is exactly what that mechanism
is for. Don't build this before the single-image version is shipped and
validated; it's strictly more complexity for a nicer-to-have.

### 4.5 Future, cost-gated: real video generation

Deferred, not cancelled. Revisit only when there's a concrete reason (revenue,
user demand for actual motion beyond pan/zoom) to justify the per-generation
cost. When that time comes, the evaluation criteria are unchanged from the
earlier research pass:

- Seed control / image-to-image conditioning support (rules out closed APIs
  like DALL-E-style vendors for this purpose).
- Cost per generation at realistic scenario volume, including the 2-3x retry
  overhead most generations need in practice.
- Whether the vendor has a dedicated character-consistency feature (Kling has
  been noted for this specifically) that could reduce reliance on the
  seed/reference mechanism.

Nothing about the 4.3 pipeline needs to change to support this later — it's
an additive upgrade path (swap the ffmpeg-stitched output for a vendor-generated
clip on a per-scenario basis), not a rewrite.

### 1.4 Product-readiness gaps (surfaced in final review pass)

**Quick fixes — no decision needed, just implement:**

| # | Fix |
|---|---|
| 10 | Add `.github/workflows/tests.yml` running `pytest` on every push/PR. Nothing currently runs the 181 existing tests automatically. |
| 11 | Add a `worker:` service to `docker-compose.yml` running the arq worker process. The arq migration (item 4) is code-only until this exists — without it, nothing picks jobs off the queue. |
| 12 | New pipeline code (image/audio/stitch, R2 upload, judge harness) is held to the same test-coverage standard as the existing 181 tests. Not optional just because it's new. |

**Decisions — resolved:**

- **Image moderation (decided: built-in safety_checker + Gemini second pass).**
  Keep SD 1.5's default `safety_checker` enabled — do not strip it for speed,
  which many quickstart tutorials do. After a generation passes that check,
  send the image to Gemini (existing `GEMINI_API_KEY`, free tier) with a
  content-policy classification prompt as a second, more contextual pass.
  Flag/block/regenerate on either check failing before the image is written
  to R2. Adds one network call of latency per generation — acceptable since
  generation already happens once at publish time, not per-request.
- **Failure alerting (decided: DIY log + webhook, no new vendor).**
  On any failure event (prefab failure, silent bad state, moderation block),
  emit a structured log line, and add a scheduled job — arq supports cron
  jobs natively, so this can piggyback on the same worker from item 11 —
  that queries for `failed`/flagged rows on an interval and posts to a
  Discord or Slack webhook. New env var: `ALERT_WEBHOOK_URL`. No new
  third-party account beyond whichever chat tool you already use.
- **Rate limits on expensive endpoints (decided: 5 creates/hour, 3
  publishes/hour per user).** Tighten `slowapi` limits specifically on
  `create_scenario` and `publish_scenario` below the general API default —
  these are the endpoints that now trigger real compute (SD 1.5 + Kokoro +
  ffmpeg) and R2 writes. Easy to adjust later if it turns out too tight or
  too loose in practice.
- **Data retention: deferred, not blocking.** Indefinite `SessionLog` storage
  for now — revisit once there's real usage data to decide against, not
  before.
- **LLM observability/cost tracking (decided: Langfuse, self-hosted).**
  MIT-licensed, genuinely unlimited (no request cap, unlike Helicone's
  10k/month free tier), and fits the existing `docker-compose` pattern as
  another service alongside Postgres/Redis — chosen over Helicone's faster
  but capped/proxy-only setup because it's the option with no future ceiling,
  consistent with every other cost decision in this doc (R2 for zero egress,
  local SD1.5/Kokoro to avoid per-call API cost). Wrap the Groq calls in
  `call.py`/`ai_service.py` with the Langfuse SDK; it auto-computes $ cost
  from token counts against known model pricing. Accepted tradeoff: an hour
  or two of SDK integration versus Helicone's one-line proxy swap — worth it
  given there's no launch deadline forcing the faster option.

---

## 5. Product-strategy decisions (open, bigger than a single fix)

These surfaced from a self/interviewer/investor rehearsal pass and are
deliberately not resolved here — they're bigger than an engineering fix and
need your own further thought, not a default I pick for you. Recording the
current state of thinking so it's not lost.

### 5.1 Production compute for image/audio generation

Current plan (§4.3) runs SD 1.5 + Kokoro locally — correct for prototyping,
not a production architecture once real creators are publishing concurrently.
**Two real paths, not yet chosen between:**

- **Self-hosted GPU server** with a real concurrency strategy (queue depth
  limits, request batching) — keeps the zero-per-call-cost property of local
  generation, but you own scaling and uptime.
- **Switch to a paid API** (fal.ai, established earlier) once there's revenue
  to justify it — trades the concurrency-engineering problem for a per-call
  cost, cleanly solved, but reintroduces the cost concern this whole plan has
  been avoiding.

No deadline forcing this now — flag it as the thing to decide once concurrent
creators are an actual scenario, not a hypothetical one, likely informed by
whatever the load-test in Section 3 actually shows about real concurrency
needs.

### 5.2 Adult content as an intentional feature, not just an edge case

Current design: opt-in filter, user-consent-gated. Filter on → suggestive
content resolves as a scene fade rather than generating it; filter off →
permitted, though testing so far hasn't produced explicit output even with it
off, since the underlying models aren't specialized for it. That's a
reasonable content-moderation *mechanism*.

**What it doesn't yet resolve:** if adult content is an intentional product
feature (not just tolerated at the edges), that's a materially bigger
decision than the filter toggle, because it constrains:
- **Payment processing** — Stripe/PayPal have a documented history of
  restricting or dropping platforms in this category, opt-in or not. The
  realistic alternative is a high-risk-category processor (e.g. CCBill,
  Segpay), which comes with different fees and integration work than a
  standard Stripe setup.
- **Distribution** — this rules out major app stores (Apple's App Store in
  particular) as a distribution channel regardless of how the filter is
  implemented.
- **Age verification** — "user consent via a toggle" is different from what
  most jurisdictions actually require for adult-content platforms; likely
  needs real age-verification infrastructure, not a checkbox, before this is
  a stated feature rather than a tolerated possibility.

Not resolving this now — flagging that the current filter design answers
"how do we moderate content," not "what payment/distribution/legal
infrastructure does supporting this feature require," and those are
different questions with different owners.

### 5.3 Monetization (stated, not yet implemented)

Freemium: free tier caps context/session length, paid tier (subscription)
raises or removes the cap — standard SaaS pattern, matches models you
already use as a consumer. Not yet in code. Worth writing down now so it's
not an afterthought once §4.3 and billing infrastructure both exist and need
to talk to each other.

---

## Suggested execution order

1. Section 1.1 (items 1–4) — reliability hardening.
2. Section 1.2 (item 7) — frontend cleanup, cheap and immediate.
3. Section 1.4 quick fixes (CI, arq worker service) — cheap, do alongside 1.1
   since the worker service is a prerequisite for item 4 to actually run.
4. Section 3, Tier B only, small scale — validate 1.1 actually fixed what it
   claims to, before investing in Tier A emulators.
5. Section 2 — judge harness, run against current engine as a baseline before
   further engine changes.
6. Section 1.3 (schema/storage decisions) + Section 4.1/4.2 (visual profile +
   experimentation harness) before any production wiring.
7. Section 4.3 — ship the local image + Kokoro + ffmpeg pipeline, including
   the 1.4 moderation/alerting/rate-limit decisions baked in from the start
   rather than bolted on after. Section 4.4 (multi-image sequences) and 4.5
   (real video vendor) stay parked until there's a concrete reason to spend.
8. Section 3, full Tier A + Tier B — full breakpoint report once the above is
   in place, so the report reflects the fixed system, not rediscovers Section 1.

---

## 6. Free Deployment Plan

Pre-implementation checks worth doing first, surfaced during this pass:

- **No health check endpoint exists** (`/health` or `/healthz`) — checked
  `main.py` directly, it's not there. Every host below needs one to know the
  service is up and to auto-restart it if it dies. Cheap, add before deploy.
- **No TLS anywhere** — `nginx.conf` is `listen 80` only. Not something to
  hand-build; Cloudflare (below) handles this for free without touching nginx.

**Beyond the plan, once real users are involved:** a domain (the one
genuinely non-free line item here, ~$10-15/year), Terms of Service + Privacy
Policy (need to actually reflect §5.2's content stance and §5.3's freemium
billing, not be generic boilerplate), a monitored support email, and backups
(handled by managed Postgres below — self-hosting Postgres wouldn't give you
this for free).

No single free platform covers this stack — split across services. Note:
Fly.io no longer offers a free tier for new users as of this year, excluded
below for that reason.

| Component | Where | Why | Real limit to know |
|---|---|---|---|
| Frontend (React build) | **Cloudflare Pages** | Static hosting, genuinely free, unlimited bandwidth, free custom domain + HTTPS | None significant at this scale |
| Backend API (FastAPI) | **Render**, free web service | No credit card, real Docker support | Sleeps after 15 min idle, 30-50s cold start on first request after — real UX cost, not hidden |
| Postgres | **Supabase**, free tier | Real managed Postgres, 500MB DB, backups handled | Project pauses after 1 week of inactivity (tightened Feb 2026) — needs a periodic ping/cron to stay alive |
| Redis | **Upstash**, free tier | Serverless Redis, stable free tier, no card | 500K commands/month — watch this once arq job queuing adds to session-cache traffic; likely the first thing to need upgrading |
| Object storage | **Cloudflare R2** | Already decided (fix-map item 9) — 10GB free, zero egress forever | — |
| HTTPS/TLS | **Cloudflare, proxying the domain** | Automatic free TLS the moment DNS points through Cloudflare — no certbot, no nginx TLS config needed | Resolves the nginx gap above without touching nginx |
| Image/audio generation (SD1.5 + Kokoro + ffmpeg) | **Your own machine**, polling the job queue | No cloud free tier has GPU — consistent with §5.1's conclusion. Reaches out to Upstash/R2, so no inbound access or static IP needed | Only runs while your machine is on — acceptable for prelaunch, real constraint for uptime later |
| Observability | **Langfuse Cloud free tier** (not self-hosted) for the deployed environment | Self-hosting was right for local dev with no ceiling concerns (§1.4) — but on Render's free tier, another self-hosted service competes for the same limited resources. Cloud free tier (50K observations/month) fits the deployed environment better | Keep self-hosted for local dev, cloud free tier once actually deployed — different environments, different answer |

**Structural note:** `nginx` currently proxies both frontend and backend
together for local Docker Compose. In this deployment shape it drops out
entirely — Cloudflare Pages serves the frontend directly, Render serves the
backend directly with its own HTTPS. `nginx` stays relevant for local
development only, not the deployed topology — don't force one
`docker-compose.yml` to serve both purposes.

Net result: a real, publicly-reachable product at $0/month except the
domain, with the tradeoffs (cold starts, weekly DB pings, your own machine
gating generation uptime) stated plainly rather than glossed over.
