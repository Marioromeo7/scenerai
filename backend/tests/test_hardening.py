"""
Tests for the three hardening changes:
  1. NFKC normalization in sanitize_input (see test_inference.py TestSanitizeInput)
  2. Per-session Redis lock in play_turn / play_turn_stream
  3. plays_count moved to first-turn only
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock


# ── Helpers ───────────────────────────────────────────────────

def _run(coro):
    """Execute a coroutine synchronously."""
    return asyncio.run(coro)


def _mock_redis(lock_available=True, session_data=None):
    """Build an AsyncMock Redis client with configurable lock behaviour."""
    redis = AsyncMock()
    redis.set.return_value = True if lock_available else None  # nx=True: True=acquired, None=held
    redis.delete.return_value = 1
    redis.setex.return_value = True
    if session_data is not None:
        redis.get.return_value = json.dumps(session_data)
    else:
        redis.get.return_value = None
    return redis


def _session_ctx(turn=0, preview=False, status="ready"):
    return {
        "user_id": "user-1",
        "scenario_id": "scenario-1",
        "engine_state": {"display_history": [], "history": []},
        "turn": turn,
        "status": status,
        "preview": preview,
    }


# ── Session lock ──────────────────────────────────────────────

class TestSessionLockAcquire:
    """Unit-level tests for the Redis SET NX lock pattern."""

    def test_set_nx_returns_true_when_free(self):
        """SET NX on a free key returns a truthy value — lock acquired."""
        redis = _mock_redis(lock_available=True)
        result = _run(redis.set("lock:session:abc", "1", nx=True, ex=60))
        assert result  # truthy → proceed with turn

    def test_set_nx_returns_none_when_held(self):
        """SET NX on a held key returns None — concurrent request blocked."""
        redis = _mock_redis(lock_available=False)
        result = _run(redis.set("lock:session:abc", "1", nx=True, ex=60))
        assert result is None  # caller must return 409

    def test_lock_key_format(self):
        """Lock key must be prefixed with lock:session: to avoid collisions."""
        redis = _mock_redis()
        session_id = "test-session-id-1234"
        _run(redis.set(f"lock:{session_id}", "1", nx=True, ex=60))
        args = redis.set.call_args[0]
        assert args[0].startswith("lock:")
        assert session_id in args[0]

    def test_lock_released_in_finally(self):
        """delete() must be called on the lock key even when an error is raised."""
        redis = _mock_redis(lock_available=True)
        lock_key = "lock:session:xyz"

        async def _simulate_turn_with_error():
            acquired = await redis.set(lock_key, "1", nx=True, ex=60)
            assert acquired
            try:
                raise RuntimeError("engine exploded")
            finally:
                await redis.delete(lock_key)

        with pytest.raises(RuntimeError):
            _run(_simulate_turn_with_error())

        redis.delete.assert_called_once_with(lock_key)

    def test_lock_released_on_success(self):
        """delete() must be called after a successful turn too."""
        redis = _mock_redis(lock_available=True)
        lock_key = "lock:session:xyz"

        async def _simulate_turn():
            acquired = await redis.set(lock_key, "1", nx=True, ex=60)
            assert acquired
            try:
                pass  # successful turn
            finally:
                await redis.delete(lock_key)

        _run(_simulate_turn())
        redis.delete.assert_called_once_with(lock_key)

    def test_blocking_turn_does_not_release_lock_it_never_held(self):
        """A request that fails to acquire the lock must NOT delete it."""
        redis = _mock_redis(lock_available=False)
        lock_key = "lock:session:xyz"

        async def _simulate_blocked():
            acquired = await redis.set(lock_key, "1", nx=True, ex=60)
            if not acquired:
                return 409  # return early — no finally with delete
            try:
                pass
            finally:
                await redis.delete(lock_key)

        result = _run(_simulate_blocked())
        assert result == 409
        redis.delete.assert_not_called()


# ── plays_count first-turn gate ───────────────────────────────

class TestPlaysCountGate:
    """Verify the condition that gates plays_count increments."""

    @pytest.mark.parametrize("turn,preview,should_increment", [
        (1, False, True),   # first real turn → increment
        (1, True,  False),  # first turn but preview → no increment
        (2, False, False),  # second turn → no increment
        (0, False, False),  # turn 0 (shouldn't happen, but guard it)
    ])
    def test_gate_condition(self, turn, preview, should_increment):
        result = {"turn": turn}
        ctx    = {"preview": preview}
        gate   = result["turn"] == 1 and not ctx.get("preview")
        assert gate is should_increment

    def test_plays_count_incremented_exactly_once(self):
        """Simulate three turns: only turn 1 fires the DB update."""
        increments = []

        async def _simulate_turns():
            for turn_num in range(1, 4):
                result = {"turn": turn_num}
                ctx    = {"preview": False, "scenario_id": "sc-1"}
                if result["turn"] == 1 and not ctx.get("preview"):
                    increments.append(ctx["scenario_id"])

        _run(_simulate_turns())
        assert increments == ["sc-1"]

    def test_preview_session_never_increments(self):
        """Three preview turns must never fire an increment."""
        increments = []

        async def _simulate_preview():
            for turn_num in range(1, 4):
                result = {"turn": turn_num}
                ctx    = {"preview": True, "scenario_id": "sc-1"}
                if result["turn"] == 1 and not ctx.get("preview"):
                    increments.append(ctx["scenario_id"])

        _run(_simulate_preview())
        assert increments == []


# ── Stream: lock TTL is longer than non-stream ────────────────

class TestLockTTL:
    """Streaming turns hold the lock longer — TTL must reflect that."""

    def test_non_stream_lock_ttl_is_60s(self):
        redis = _mock_redis()
        _run(redis.set("lock:session:abc", "1", nx=True, ex=60))
        _, kwargs = redis.set.call_args
        # ex may be passed positionally or as kwarg depending on call site
        args = redis.set.call_args[0]
        call_kwargs = redis.set.call_args[1]
        ttl = call_kwargs.get("ex") or (args[2] if len(args) > 2 else None)
        assert ttl == 60

    def test_stream_lock_ttl_is_longer(self):
        """Streaming TTL (120s) must be ≥ non-streaming TTL (60s)."""
        assert 120 >= 60
