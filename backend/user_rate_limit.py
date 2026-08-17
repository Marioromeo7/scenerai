"""
Per-user abuse/rate limiting — distinct from rate_limiter.py's Groq TPM
budget gate. That one protects the platform's *shared* Groq quota from
being exhausted by anyone; this one stops a *single* account from
hammering the API and unfairly consuming that shared quota (or just
degrading their own session with runaway retries/bugs in a client).

Deliberately a hard reject (429), not a queue like the TPM gate — abuse
protection should tell the caller to slow down immediately, not make
them wait silently, which would mask what's actually happening.

Same incr+expire-once pattern used throughout this codebase (worker.py's
PREFAB_JOB_KEY, rate_limiter.py's TPM window).
"""
from fastapi import Depends, HTTPException
from redis.asyncio import Redis

from auth import get_current_user
from database import get_redis
from models import User

MAX_REQUESTS_PER_WINDOW = 10
WINDOW_SECONDS = 60


async def enforce_user_rate_limit(
    user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
) -> User:
    """Route dependency — drop-in alongside get_current_user wherever a
    route should be rate-limited per account (turn/generation endpoints,
    not read-only ones). Returns the same User on success so it can
    directly replace get_current_user in a route signature."""
    key = f"user_rl:{user.id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, WINDOW_SECONDS)

    if count > MAX_REQUESTS_PER_WINDOW:
        raise HTTPException(
            status_code=429,
            detail=(f"Too many requests — max {MAX_REQUESTS_PER_WINDOW} per {WINDOW_SECONDS}s. "
                    "Slow down and try again shortly."),
        )
    return user
