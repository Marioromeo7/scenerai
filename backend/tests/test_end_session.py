"""Tests for main.py's end_session -- had zero coverage before this, despite
carrying a documented, previously-live bug fix (re-reading ctx from Redis
after the lock-wait loop, since a turn that completes mid-wait writes its
fresh state back to the same Redis key before releasing the lock -- the
pre-wait ctx snapshot would otherwise silently overwrite that turn's real
Postgres row with stale history/engine_state/turn data). Same direct-call,
mocked-dependency style as test_regenerate_media.py -- no TestClient/DB
fixture exists in this suite (see conftest.py)."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import main


def _redis(get_side_effect, exists_return=False):
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=get_side_effect)
    redis.exists = AsyncMock(return_value=exists_return)
    redis.srem = AsyncMock()
    redis.delete = AsyncMock()
    return redis


class TestEndSession:
    @pytest.mark.asyncio
    async def test_no_op_when_session_already_gone(self):
        redis = _redis(get_side_effect=[None])
        db = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)), \
             patch('main._upsert_session_log', new=AsyncMock()) as mock_upsert:
            await main.end_session('sess-1', db=db, cu=user)

        mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_403_when_session_belongs_to_another_user(self):
        ctx = {"user_id": "someone-else", "scenario_id": "scn-1", "turn": 1,
               "history": [], "preview": False}
        redis = _redis(get_side_effect=[json.dumps(ctx).encode()])
        db = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            with pytest.raises(HTTPException) as exc_info:
                await main.end_session('sess-1', db=db, cu=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_regression_uses_fresh_ctx_reread_after_lock_wait_not_stale_snapshot(self):
        """The core fix: a turn completing mid-wait writes fresh state back
        to the same Redis key before releasing its lock. Checkpointing the
        ctx captured BEFORE the wait would silently revert that turn --
        this test simulates exactly that race and asserts the upsert uses
        the post-wait (turn=3) data, not the pre-wait (turn=2) snapshot."""
        stale_ctx = {"user_id": "u1", "scenario_id": "scn-1", "turn": 2,
                     "history": [{"role": "assistant", "content": "stale"}], "preview": False}
        fresh_ctx = {"user_id": "u1", "scenario_id": "scn-1", "turn": 3,
                     "history": [{"role": "assistant", "content": "fresh"}], "preview": False}
        # First get(): pre-wait snapshot. Lock held once (exists=True) then
        # released (exists=False) -- simulates a turn finishing mid-wait.
        # Second get(): post-wait re-read, must reflect the now-finished turn.
        redis = _redis(get_side_effect=[
            json.dumps(stale_ctx).encode(), json.dumps(fresh_ctx).encode(),
        ])
        redis.exists = AsyncMock(side_effect=[True, False])
        db = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)), \
             patch('main.asyncio.sleep', new=AsyncMock()), \
             patch('main._upsert_session_log', new=AsyncMock()) as mock_upsert:
            await main.end_session('sess-1', db=db, cu=user)

        upserted_ctx = mock_upsert.await_args.args[0]
        assert upserted_ctx['turn'] == 3
        assert upserted_ctx['history'][0]['content'] == 'fresh'

    @pytest.mark.asyncio
    async def test_preview_session_skips_upsert_and_scenario_sessions_cleanup(self):
        ctx = {"user_id": "u1", "scenario_id": "scn-1", "turn": 1,
               "history": [], "preview": True}
        redis = _redis(get_side_effect=[json.dumps(ctx).encode(), json.dumps(ctx).encode()])
        db = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)), \
             patch('main._upsert_session_log', new=AsyncMock()) as mock_upsert:
            await main.end_session('sess-1', db=db, cu=user)

        mock_upsert.assert_not_called()
        redis.srem.assert_not_called()
        redis.delete.assert_awaited_once_with('session:sess-1')

    @pytest.mark.asyncio
    async def test_non_preview_session_upserts_and_cleans_up_scenario_sessions_set(self):
        ctx = {"user_id": "u1", "scenario_id": "scn-1", "turn": 4,
               "history": [], "preview": False}
        redis = _redis(get_side_effect=[json.dumps(ctx).encode(), json.dumps(ctx).encode()])
        db = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)), \
             patch('main._upsert_session_log', new=AsyncMock()) as mock_upsert:
            await main.end_session('sess-1', db=db, cu=user)

        mock_upsert.assert_awaited_once()
        redis.srem.assert_awaited_once_with('scenario_sessions:scn-1', 'sess-1')
        redis.delete.assert_awaited_once_with('session:sess-1')
