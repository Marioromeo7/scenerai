"""
Refresh-token issuance, rotation, and revocation — the piece that was
entirely missing before. The access token (auth.py's create_token) is a
stateless JWT: it can't be individually invalidated before it expires,
which is exactly why its lifetime was cut from 7 days to 30 minutes
(see config.py). Session continuity across that short window comes from
this refresh token instead, which IS revocable because it's a Redis
lookup, not a self-contained signed claim.

Opaque random tokens rather than JWTs, deliberately — a refresh token's
only job is "look up who this belongs to," and using a stateless JWT for
something meant to be revocable on demand would defeat the point.

Rotation on every use (old token deleted, new one issued) rather than a
static long-lived refresh token: if a refresh token is ever used twice,
that's a signal of theft (attacker and legitimate user both holding a
copy), not just normal reuse — this codebase doesn't yet act on that
signal (no theft-detection alerting), but rotation at least closes the
window instead of leaving one refresh token valid, unchanged, for 30
days.

"Revoke everywhere" (all of one user's sessions) isn't implemented here
-- tokens are keyed by their own random string, not indexed by user_id,
so that would need a secondary per-user set. Left as a known gap rather
than built speculatively; single-token revoke (logout on this device)
is the piece that was actually asked for.
"""
import secrets

from redis.asyncio import Redis

from config import settings

_KEY_PREFIX = "refresh_token:"


def _key(token: str) -> str:
    return f"{_KEY_PREFIX}{token}"


async def create_refresh_token(redis: Redis, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    await redis.set(_key(token), user_id, ex=settings.refresh_token_expire_days * 24 * 60 * 60)
    return token


async def rotate_refresh_token(redis: Redis, token: str) -> tuple[str, str] | None:
    """Validates `token`, then atomically retires it and issues a
    replacement. Returns (user_id, new_token), or None if the token is
    invalid/expired/already used.

    GETDEL (not GET-then-DELETE) is load-bearing, not a style choice --
    found live via code review that the previous GET/DELETE pair wasn't
    actually atomic despite this docstring's claim: two concurrent
    /auth/refresh calls with the same token (a client retry racing the
    original request) could both read the still-present key before either
    delete ran, both minting a new token from one old one. That silently
    defeats the single-use/rotation invariant this whole module exists
    for -- a reused refresh token is supposed to be a detectable theft
    signal, not something that just works twice."""
    user_id = await redis.getdel(_key(token))
    if user_id is None:
        return None
    new_token = await create_refresh_token(redis, user_id)
    return user_id, new_token


async def revoke_refresh_token(redis: Redis, token: str) -> None:
    await redis.delete(_key(token))
