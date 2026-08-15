"""Tests for user_rate_limit.py — per-account abuse protection."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException
import user_rate_limit


def _mock_user(user_id="user-123"):
    user = MagicMock()
    user.id = user_id
    return user


def _mock_redis(incr_return):
    redis = AsyncMock()
    redis.incr.return_value = incr_return
    return redis


class TestEnforceUserRateLimit:
    @pytest.mark.asyncio
    async def test_allows_first_request(self):
        user = _mock_user()
        redis = _mock_redis(incr_return=1)

        result = await user_rate_limit.enforce_user_rate_limit(user=user, redis=redis)

        assert result is user
        redis.expire.assert_awaited_once_with("user_rl:user-123", user_rate_limit.WINDOW_SECONDS)

    @pytest.mark.asyncio
    async def test_allows_requests_under_the_limit(self):
        user = _mock_user()
        redis = _mock_redis(incr_return=user_rate_limit.MAX_REQUESTS_PER_WINDOW)

        result = await user_rate_limit.enforce_user_rate_limit(user=user, redis=redis)

        assert result is user

    @pytest.mark.asyncio
    async def test_blocks_requests_over_the_limit(self):
        user = _mock_user()
        redis = _mock_redis(incr_return=user_rate_limit.MAX_REQUESTS_PER_WINDOW + 1)

        with pytest.raises(HTTPException) as exc_info:
            await user_rate_limit.enforce_user_rate_limit(user=user, redis=redis)

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_does_not_reset_expiry_on_repeat_requests(self):
        user = _mock_user()
        redis = _mock_redis(incr_return=5)  # not the first request this window

        await user_rate_limit.enforce_user_rate_limit(user=user, redis=redis)

        redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_keys_are_isolated_per_user(self):
        redis = _mock_redis(incr_return=1)

        await user_rate_limit.enforce_user_rate_limit(user=_mock_user("user-a"), redis=redis)
        await user_rate_limit.enforce_user_rate_limit(user=_mock_user("user-b"), redis=redis)

        called_keys = [call.args[0] for call in redis.incr.await_args_list]
        assert called_keys == ["user_rl:user-a", "user_rl:user-b"]
