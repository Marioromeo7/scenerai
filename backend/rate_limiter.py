"""
Groq TPM (tokens-per-minute) budget gate — the piece call.py's existing
retry logic doesn't cover. call() already handles a SINGLE request
hitting a 429 gracefully (parses Groq's "try again in Xs", waits,
retries) — but nothing stops multiple concurrent requests from all firing
at once, all exceeding the budget together, and all retrying in lockstep
(the thundering-herd pattern found during Section 3 load testing).

Re-measured live (2026-08-18) against the current model roster, after
llama-3.1-8b-instant (the model the original 6000 TPM figure was measured
against) was decommissioned by Groq -- see ai_service.py's ENGINE_MODELS
comment. Read directly from Groq's own x-ratelimit-limit-tokens response
header (client.chat.completions.with_raw_response.create(...).headers),
not inferred from a 429 or re-run through the load-test harness -- that
harness measures backend concurrency breakpoints, a different and much
slower thing than reading the number Groq already reports on every
response. Checked all three models in ENGINE_MODELS individually (not
assumed to share a limit): openai/gpt-oss-20b, openai/gpt-oss-120b, and
qwen/qwen3.6-27b all report the identical 8000 TPM on this account tier,
so one shared TPM_LIMIT across all of them is still the right design, just
with the current real number. At ~1500-4000 tokens per player turn (system
prompt + history + response + guard call), that's roughly 2-5 turns/minute
across the ENTIRE platform on this tier, not per user.

Design: a Redis-backed sliding-window counter, same incr+expire pattern
already used for worker.py's PREFAB_JOB_KEY concurrency gate. Callers
reserve an estimated token budget BEFORE calling Groq; if that would
exceed the current window's limit, they wait (poll) instead of firing
and getting rejected — turning simultaneous overload into an orderly
queue instead of a stampede of 429s.

Not yet wired into call()/async_call() — this is the standalone,
independently-tested piece, ready to integrate once validated against
the running stack.
"""
import asyncio
import time

TPM_LIMIT = 8000  # re-measured live 2026-08-18 via Groq's x-ratelimit-limit-tokens header
WINDOW_KEY = "groq_tpm_window"
WINDOW_SECONDS = 60
POLL_INTERVAL = 0.5


async def reserve_token_budget(redis, estimated_tokens: int, max_wait: float = 30.0) -> bool:
    """Blocks (polling), waiting for room in the current minute's TPM
    budget, then reserves estimated_tokens atomically. Returns True once
    reserved. Returns False if max_wait elapses first — callers should
    treat that as "queue is full right now", i.e. surface a clear
    capacity message to the user, not silently drop the request.

    Uses increment-then-compensate rather than check-then-increment:
    INCRBY is atomic in Redis, so this avoids a race where two concurrent
    callers both pass a separate GET check and jointly overshoot the
    budget."""
    deadline = time.monotonic() + max_wait
    while True:
        new_total = await redis.incrby(WINDOW_KEY, estimated_tokens)
        if new_total == estimated_tokens:
            # we just created the key this window -- start its TTL
            await redis.expire(WINDOW_KEY, WINDOW_SECONDS)

        if new_total <= TPM_LIMIT:
            return True

        # over budget -- release our reservation and wait for room
        await redis.decrby(WINDOW_KEY, estimated_tokens)
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(POLL_INTERVAL)


def estimate_tokens(system: str, history: list, max_tokens: int) -> int:
    """Rough token estimate for reservation purposes only — doesn't need
    to be exact, Groq's own accounting is the real source of truth. This
    just needs to avoid badly undershooting. ~4 chars/token is the
    standard rough estimate for English text."""
    text_len = len(system) + sum(len(m.get('content', '')) for m in history)
    return (text_len // 4) + max_tokens
