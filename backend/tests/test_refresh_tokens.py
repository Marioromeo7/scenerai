"""Tests for refresh_tokens.py — issuance, rotation, revocation."""
import pytest
from unittest.mock import AsyncMock
import refresh_tokens
from config import settings


def _mock_redis():
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock()
    redis.getdel = AsyncMock()
    redis.delete = AsyncMock()
    return redis


class TestCreateRefreshToken:
    @pytest.mark.asyncio
    async def test_stores_token_with_correct_ttl(self):
        redis = _mock_redis()

        token = await refresh_tokens.create_refresh_token(redis, "user-123")

        assert isinstance(token, str) and len(token) > 20
        redis.set.assert_awaited_once()
        args, kwargs = redis.set.await_args
        assert args[0] == f"refresh_token:{token}"
        assert args[1] == "user-123"
        assert kwargs["ex"] == settings.refresh_token_expire_days * 24 * 60 * 60

    @pytest.mark.asyncio
    async def test_tokens_are_unique(self):
        redis = _mock_redis()

        t1 = await refresh_tokens.create_refresh_token(redis, "user-123")
        t2 = await refresh_tokens.create_refresh_token(redis, "user-123")

        assert t1 != t2


class TestRotateRefreshToken:
    @pytest.mark.asyncio
    async def test_valid_token_returns_user_id_and_new_token(self):
        redis = _mock_redis()
        redis.getdel.return_value = "user-123"

        result = await refresh_tokens.rotate_refresh_token(redis, "old-token")

        assert result is not None
        user_id, new_token = result
        assert user_id == "user-123"
        assert new_token != "old-token"

    @pytest.mark.asyncio
    async def test_valid_token_atomically_gets_and_deletes_old_token(self):
        # GETDEL, not a separate GET+DELETE pair -- a non-atomic pair is
        # exactly the race two concurrent /auth/refresh calls with the same
        # token could exploit to both mint a new token from one old one.
        redis = _mock_redis()
        redis.getdel.return_value = "user-123"

        await refresh_tokens.rotate_refresh_token(redis, "old-token")

        redis.getdel.assert_awaited_once_with("refresh_token:old-token")
        redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self):
        redis = _mock_redis()
        redis.getdel.return_value = None

        result = await refresh_tokens.rotate_refresh_token(redis, "bogus-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_reused_token_fails_second_time(self):
        """Rotation means a token is single-use -- simulates the real
        sequence: first call succeeds (getdel returns and removes the
        user), second call on the SAME old token fails because GETDEL
        already consumed it."""
        redis = _mock_redis()
        redis.getdel.side_effect = ["user-123", None]

        first = await refresh_tokens.rotate_refresh_token(redis, "old-token")
        second = await refresh_tokens.rotate_refresh_token(redis, "old-token")

        assert first is not None
        assert second is None


class TestRevokeRefreshToken:
    @pytest.mark.asyncio
    async def test_deletes_the_token(self):
        redis = _mock_redis()

        await refresh_tokens.revoke_refresh_token(redis, "some-token")

        redis.delete.assert_awaited_once_with("refresh_token:some-token")
