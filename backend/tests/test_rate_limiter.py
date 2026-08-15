"""Tests for rate_limiter.py — the Groq TPM budget gate."""
import pytest
from unittest.mock import AsyncMock, patch
import rate_limiter


def _mock_redis(incrby_side_effect):
    redis = AsyncMock()
    redis.incrby.side_effect = incrby_side_effect
    return redis


class TestEstimateTokens:
    def test_scales_with_text_length(self):
        short = rate_limiter.estimate_tokens('sys', [], max_tokens=100)
        long = rate_limiter.estimate_tokens('sys' * 100, [], max_tokens=100)
        assert long > short

    def test_includes_history(self):
        no_history = rate_limiter.estimate_tokens('sys', [], max_tokens=100)
        with_history = rate_limiter.estimate_tokens(
            'sys', [{'role': 'user', 'content': 'x' * 400}], max_tokens=100,
        )
        assert with_history > no_history

    def test_includes_max_tokens_floor(self):
        # even with empty system/history, max_tokens still counts
        est = rate_limiter.estimate_tokens('', [], max_tokens=500)
        assert est == 500


class TestReserveTokenBudget:
    @pytest.mark.asyncio
    async def test_reserves_immediately_when_under_budget(self):
        redis = _mock_redis(incrby_side_effect=[1000])
        redis.expire = AsyncMock()

        ok = await rate_limiter.reserve_token_budget(redis, estimated_tokens=1000)

        assert ok is True
        redis.incrby.assert_awaited_once_with(rate_limiter.WINDOW_KEY, 1000)
        redis.decrby.assert_not_called()

    @pytest.mark.asyncio
    async def test_sets_expiry_on_first_reservation_in_window(self):
        # new_total == estimated_tokens means we just created the key
        redis = _mock_redis(incrby_side_effect=[500])
        redis.expire = AsyncMock()

        await rate_limiter.reserve_token_budget(redis, estimated_tokens=500)

        redis.expire.assert_awaited_once_with(rate_limiter.WINDOW_KEY, rate_limiter.WINDOW_SECONDS)

    @pytest.mark.asyncio
    async def test_does_not_reset_expiry_on_subsequent_reservation(self):
        # new_total != estimated_tokens -- key already existed this window
        redis = _mock_redis(incrby_side_effect=[5500])
        redis.expire = AsyncMock()

        await rate_limiter.reserve_token_budget(redis, estimated_tokens=1000)

        redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_compensates_and_waits_when_over_budget_then_succeeds(self):
        # first attempt overshoots (6500 > 6000), second attempt fits
        redis = _mock_redis(incrby_side_effect=[6500, 1000])
        redis.expire = AsyncMock()
        redis.decrby = AsyncMock()

        with patch('rate_limiter.asyncio.sleep', new=AsyncMock()) as mock_sleep:
            ok = await rate_limiter.reserve_token_budget(redis, estimated_tokens=1000, max_wait=5.0)

        assert ok is True
        redis.decrby.assert_awaited_once_with(rate_limiter.WINDOW_KEY, 1000)
        assert redis.incrby.await_count == 2
        mock_sleep.assert_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_when_max_wait_exceeded(self):
        # always overshoots -- never fits within budget
        redis = _mock_redis(incrby_side_effect=[7000] * 100)
        redis.expire = AsyncMock()
        redis.decrby = AsyncMock()

        with patch('rate_limiter.asyncio.sleep', new=AsyncMock()), \
             patch('rate_limiter.time.monotonic', side_effect=[0, 0, 1, 2, 3, 4, 5, 6]):
            ok = await rate_limiter.reserve_token_budget(redis, estimated_tokens=1000, max_wait=5.0)

        assert ok is False
        # every failed attempt must release its reservation
        assert redis.decrby.await_count == redis.incrby.await_count

    @pytest.mark.asyncio
    async def test_never_leaves_budget_reserved_without_success(self):
        """Every incrby that overshoots must be matched by a decrby --
        otherwise the window's counter permanently leaks reserved budget
        that was never actually used, starving later real requests."""
        redis = _mock_redis(incrby_side_effect=[6100, 6050, 999])
        redis.expire = AsyncMock()
        redis.decrby = AsyncMock()

        with patch('rate_limiter.asyncio.sleep', new=AsyncMock()):
            ok = await rate_limiter.reserve_token_budget(redis, estimated_tokens=999, max_wait=5.0)

        assert ok is True
        assert redis.decrby.await_count == 2  # the two overshooting attempts
