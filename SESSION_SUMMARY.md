# Scenarai — Session Summary & Contribution Record

Covers the full session: engine/backend reliability work, new gameplay features, and the visual-consistency (image generation) research track. Written at session end as a handoff record.

---

## Final Todo List

### Done this session

**Engine & backend reliability**
- Fixed `check_sovereignty` failing *open* on exceptions (was returning "clean" on a crash — now fails closed)
- Fixed a fire-and-forget threading bug racing against `serialize_engine()`
- Replaced `.replace()`-based validator payload construction with a proper closure
- Added near-duplicate response detection (`_similar_response`, difflib-based) to the guard's repair/fallback path
- Added the "rope loosened by behaviour" resolve mechanism (INTACT/TESTED/WORN/BROKEN integrity bands) to ease guard strictness based on in-fiction emotional consequence
- Replaced passlib (broken on bcrypt>=4.0) with direct bcrypt calls
- Moved `extract_and_save_assumptions`/`summarize_chunk` outside the engine's lock to avoid reentrant deadlock; made both synchronous
- Migrated background jobs from FastAPI `BackgroundTasks` to arq (Redis-backed queue); added `worker.py`, backend healthcheck, one-shot `migrate` service in Docker Compose

**New gameplay features**
- "Continue" — let the AI narrate forward with no player input (`Engine.step(..., continue_narrative=True)`, new endpoint)
- "Regenerate" — reroll the last AI response if a turn came out wrong (`Engine.regenerate()`, new endpoint, stale-rating cleanup)
- Turn rating (1-5 stars, upsert endpoint, frontend `RatingStars` component)
- Frontend: regenerate button + rating stars on the last message, "↻ Let it continue" button

**Testing & infra**
- Test suite grew from 181 → 245+ tests across the session
- Cloudflare R2 storage wrapper (`storage.py`) — written, not yet wired into a route (needs credentials)
- Kokoro TTS narration module (`narration.py`) and ffmpeg image+audio video stitching (`stitch.py`) — written, not yet installed/tested
- Freed ~11GB on a nearly-full C: drive (Docker vhdx + orphaned conda envs), with explicit per-item approval before any deletion

**Visual-consistency research (imagepipe/, local SD1.5 + BLIP + CLIP)**
- Built the field-set experiment: compared short/medium/long character-profile prompts at N=1000 generations each
  - Result: **short (4 fields) wins on every metric** — more prompt detail monotonically *hurt* consistency (image-sim mean 0.85→0.82, stdev 0.047→0.061 from short→long)
- Found and fixed two real bugs discovered only by running at scale: a GPU-memory leak in the model-unload functions (caused a CUDA OOM), and a transformers-version API mismatch in CLIP feature extraction
- Built a session-consistency harness (fixed character+action+place, within-run vs. between-run metrics) — revealed that whole-image CLIP similarity is the wrong metric once pose is intentionally held fixed vs. varying
- Built VQA-based attribute checking (does eye color / a scar actually match, not just "does the image look similar") — found the naive version is unreliable for small facial features (defaults to "black" regardless of actual color; will confidently answer even when eyes are literally closed)
- Built incremental/attribute-locked generation (rejection sampling: lock in one attribute at a time, retry with a new seed until each holds) — quantified real per-attribute success rates: hair 100%, cloak 90% (avg 2.6 tries), eye color ~5.5%
- Diagnosed root cause via direct visual inspection of generated images (not just automated scores): SD1.5 attribute-binding failures — colors literally swapping between garments (green migrated from cloak to hair in one sample)
- Built and verified a Groq-based prompt-canonicalization step (simplifies compound colors like "dark green" → "olive", separates colors sharing one clause) — fixes exactly the swap failure mode above
- Researched and priced the path to production: Oracle Cloud "Always Free" VPS (2 OCPU/12GB, real but CPU-only), Cloudflare Workers AI (10k free neurons/day, hosts SDXL/Flux — also likely to independently improve attribute-binding vs. SD1.5), Ollama Cloud free tier (experimental image support)
- Flagged a load-bearing business finding: **Stripe prohibits adult/NSFW content with zero tolerance** — relevant given the guard's content-easing mechanism; a mature-content decision needs to be made before picking a payment processor
- Recommended against adding true AI video generation (Cloudflare now offers it free) — the existing plan's still-image + Kokoro narration + ffmpeg pan/zoom approach is lower-risk and doesn't compound the attribute-consistency problems found above across many frames

### Not done / next steps, roughly in order

1. **Image-reliability Phase A** (cheap, no new downloads): pose-visibility prompt constraint, crop-and-zoom before VQA-checking small features, wire the (already-built) Groq canonicalization into the real pipeline, re-validate at pilot scale
2. **Evaluate switching the generation backend** to Cloudflare Workers AI (SDXL/Flux) instead of local SD1.5 — likely fixes part of the attribute-binding problem as a side effect, and removes the local-GPU dependency entirely for production
3. Kokoro TTS: install, test end-to-end, integrate as a route
4. ffmpeg video stitching: install, test end-to-end, integrate as a route
5. R2 storage: wire into routes (blocked on your Cloudflare account/credentials)
6. Deployment: Oracle Cloud Always Free VPS — Docker Compose stack, domain, TLS (blocked on your Oracle account)
7. Groq capacity/rate-limiting for real concurrent users (today's testing already found real TPM/TPD limits solo)
8. Per-user abuse/rate limiting
9. JWT refresh/revocation
10. Legal basics: ToS, Privacy Policy, age gate (can draft, not legally certify — needs real review given mature-content capability)
11. Basic error monitoring (e.g. Sentry), DB backup strategy, frontend error-state pass
12. Payments — blocked on the mature-content decision above; Stripe likely isn't viable, would need an adult-friendly processor (CCBill/Segpay/Epoch or similar)
13. Image-reliability Phase B/C (inpainting-based correction; separate validation pass for a manhua-style checkpoint) — only if Phase A / the Cloudflare switch isn't sufficient
14. Full statistical-scale validation once the pipeline is actually solid (the original "1000 runs × 100 turns" ask is ~8 days of GPU time taken literally — scope down once Phase A results are in)

---

## Contribution Sheet

### What Claude Code did
- Wrote and fixed all code changes: engine/backend reliability fixes, new feature implementation (continue/regenerate/rating), test suite growth, Docker/infra changes
- Designed and built the entire `imagepipe/` experimental harness from scratch (10 new modules)
- Ran, monitored, and diagnosed every GPU experiment this session; found and root-caused two real bugs that only appeared at scale (VRAM leak, transformers API mismatch)
- Performed direct visual inspection of generated images to sanity-check automated metrics, catching that the eye-color VQA check was unreliable (defaulting to "black," answering confidently even with closed eyes) rather than reporting a misleading "0% consistency" as a real finding
- Computed and interpreted the detail-vs-determinism correlation analysis
- Implemented the incremental attribute-locking system and the Groq canonicalization fix
- Researched and fact-checked current (2026) infrastructure options via live web search rather than relying on possibly-stale assumptions: Oracle Cloud specs/pricing, Stripe's content policy, Cloudflare Workers AI and Ollama Cloud free tiers, video-generation API landscape
- Gave direct, unhedged technical and strategic assessments when asked (e.g., why more prompt detail hurt consistency, why cloak color specifically failed, the Stripe/NSFW conflict, the recommendation against adding video generation now)

### What you did
- Set direction and priorities at every checkpoint — what to test next, when to scale up vs. checkpoint first, when to stop and change approach
- **Proposed the incremental one-attribute-at-a-time generation strategy** — the core idea behind the rejection-sampling harness; Claude Code formalized and implemented it
- **Proposed the crop/zoom-then-verify idea** for fixing the small-feature VQA problem — Claude Code mapped it to the concrete technique (crop-and-zoom for verification, inpainting for correction) and identified which parts of the idea would and wouldn't work
- Caught a real analysis error mid-session (questioned why stdev looked like it was narrowing when it wasn't), which corrected the record before it went further
- Pushed for root-cause explanations rather than accepting surface-level results ("why does it not follow the prompt," "why the cloak specifically") — this line of questioning is what led to the attribute-binding diagnosis and the Groq-fix idea
- Set the product/business direction: manhua + realistic dual-style support, "distribution ready for real users" as the concrete target (distinct from "impressive demo" or "monetizable business"), zero-budget constraint
- Made the final calls on scope and sequencing, including deciding to stop and document rather than keep expanding scope indefinitely
- Approved every GPU run and every deletion explicitly before it happened, per your standing instruction
- Will need to personally handle every external account/credential step no amount of session time can shortcut: Oracle Cloud signup, Cloudflare/R2 signup, any payment-processor decision and verification, and legal review of drafted policy documents
