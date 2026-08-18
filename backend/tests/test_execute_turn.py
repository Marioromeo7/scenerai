"""Tests for main.py's _execute_turn -- the shared lock/checkpoint/
plays_count core behind play_turn/continue_turn/regenerate_turn. Had zero
coverage before this (found live via code review: no route in main.py had
direct tests), despite carrying a documented, previously-live bug fix (the
turn lock TTL unified to 120s to match play_turn_stream's, after being
found live at a mismatched 60s with no explanation for the asymmetry).
Same direct-call, mocked-dependency style as test_session_checkpoint.py
for the AsyncSessionLocal context-manager pattern -- no TestClient/DB
fixture exists in this suite (see conftest.py)."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import main


def _redis(lock_acquired=True):
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=lock_acquired)
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    return redis


def _ctx(**overrides):
    base = {
        "session_id": "sess-1", "user_id": "u1", "scenario_id": "scn-1",
        "status": "ready", "engine_state": {"turn": 0}, "preview": False,
    }
    base.update(overrides)
    return base


def _mock_session_local(db=None):
    db = db or MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    session_local = MagicMock()
    session_local.return_value.__aenter__ = AsyncMock(return_value=db)
    session_local.return_value.__aexit__ = AsyncMock(return_value=False)
    return session_local, db


def _engine_result(turn=1, media_context=None):
    return {
        "response": "The room is quiet.", "sovereign": True, "violations": [],
        "turn": turn, "engine_state": {"turn": turn, "display_history": []},
        "media_context": media_context,
    }


class TestExecuteTurnGuards:
    @pytest.mark.asyncio
    async def test_404_when_session_not_found(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=None)
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_403_when_wrong_user(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx(user_id='someone-else')).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_503_when_engine_not_ready(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx(status='initializing')).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_503_when_engine_state_missing(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx(engine_state=None)).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_409_when_a_turn_is_already_in_progress(self):
        """The lock this exists to protect -- two concurrent requests for
        the same session must not both proceed."""
        redis = _redis(lock_acquired=False)
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_400_for_unknown_engine_model(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', 'not-a-real-model', 'turn')
        assert exc_info.value.status_code == 400
        # This check runs inside the try block, after the lock is already
        # acquired -- the finally clause must still release it.
        redis.delete.assert_awaited_once_with('lock:session:sess-1')


class TestExecuteTurnLockRelease:
    @pytest.mark.asyncio
    async def test_lock_released_on_success(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        user = MagicMock(id='u1')
        session_local, _ = _mock_session_local()

        with patch('main.engine_step', new=AsyncMock(return_value=_engine_result(turn=2))), \
             patch('main.AsyncSessionLocal', session_local), \
             patch('main._upsert_session_log', new=AsyncMock()):
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')

        redis.delete.assert_awaited_once_with('lock:session:sess-1')

    @pytest.mark.asyncio
    async def test_lock_released_even_when_engine_step_raises(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.engine_step', new=AsyncMock(side_effect=RuntimeError('Groq capacity is fully booked'))):
            with pytest.raises(HTTPException) as exc_info:
                await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')

        assert exc_info.value.status_code == 503
        redis.delete.assert_awaited_once_with('lock:session:sess-1')


class TestExecuteTurnErrorMapping:
    @pytest.mark.asyncio
    async def test_engine_regenerate_value_error_becomes_400(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.engine_regenerate', new=AsyncMock(side_effect=ValueError('nothing to regenerate'))):
            with pytest.raises(HTTPException) as exc_info:
                await main._execute_turn(request, 'sess-1', user, redis, '', None, 'regenerate')
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_engine_regenerate_runtime_error_becomes_503(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.engine_regenerate', new=AsyncMock(side_effect=RuntimeError('capacity'))):
            with pytest.raises(HTTPException) as exc_info:
                await main._execute_turn(request, 'sess-1', user, redis, '', None, 'regenerate')
        assert exc_info.value.status_code == 503


class TestExecuteTurnPlaysCount:
    @pytest.mark.asyncio
    async def test_turn_1_non_preview_non_regenerate_increments_plays_count(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx(preview=False)).encode())
        request = MagicMock()
        user = MagicMock(id='u1')
        session_local, db = _mock_session_local()

        with patch('main.engine_step', new=AsyncMock(return_value=_engine_result(turn=1))), \
             patch('main.AsyncSessionLocal', session_local), \
             patch('main._upsert_session_log', new=AsyncMock()):
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')

        db.execute.assert_awaited_once()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_regression_regenerate_never_increments_plays_count_even_at_turn_1(self):
        """regenerate reuses the same turn number -- must not double-count
        a play that already happened, or re-bill turn 1 as a second
        "first play." """
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx(preview=False)).encode())
        request = MagicMock()
        user = MagicMock(id='u1')
        session_local, db = _mock_session_local()

        with patch('main.engine_regenerate', new=AsyncMock(return_value=_engine_result(turn=1))), \
             patch('main.AsyncSessionLocal', session_local), \
             patch('main._upsert_session_log', new=AsyncMock()):
            await main._execute_turn(request, 'sess-1', user, redis, '', None, 'regenerate')

        # AsyncSessionLocal is still used once here -- for the rating-cleanup
        # delete, NOT the plays_count increment. Confirm no plays_count update ran.
        executed_sql = [str(c.args[0]) for c in db.execute.await_args_list]
        assert not any('plays_count' in sql for sql in executed_sql)

    @pytest.mark.asyncio
    async def test_preview_session_never_increments_plays_count(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx(preview=True)).encode())
        request = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.engine_step', new=AsyncMock(return_value=_engine_result(turn=1))), \
             patch('main.AsyncSessionLocal') as session_local, \
             patch('main._upsert_session_log', new=AsyncMock()):
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')

        session_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_regenerate_deletes_the_old_rating_for_that_turn(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        user = MagicMock(id='u1')
        session_local, db = _mock_session_local()

        with patch('main.engine_regenerate', new=AsyncMock(return_value=_engine_result(turn=3))), \
             patch('main.AsyncSessionLocal', session_local), \
             patch('main._upsert_session_log', new=AsyncMock()):
            await main._execute_turn(request, 'sess-1', user, redis, '', None, 'regenerate')

        executed_sql = [str(c.args[0]) for c in db.execute.await_args_list]
        assert any('turn_ratings' in sql for sql in executed_sql)


class TestExecuteTurnMediaEnqueue:
    @pytest.mark.asyncio
    async def test_enqueues_media_job_when_media_context_present(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        request.app.state.arq_pool.enqueue_job = AsyncMock()
        user = MagicMock(id='u1')
        media_context = {
            "image_prompt": "a portrait", "narration_text": "hello",
            "voice_id": "af_sarah", "voice_speed": 1.0,
        }

        with patch('main.engine_step', new=AsyncMock(return_value=_engine_result(turn=2, media_context=media_context))), \
             patch('main.AsyncSessionLocal'), \
             patch('main._upsert_session_log', new=AsyncMock()):
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')

        request.app.state.arq_pool.enqueue_job.assert_awaited_once()
        assert request.app.state.arq_pool.enqueue_job.await_args.args[0] == 'generate_turn_media_job'

    @pytest.mark.asyncio
    async def test_regression_media_enqueue_failure_does_not_fail_the_turn(self):
        """The text response already succeeded and matters more than the
        illustration -- a Redis hiccup enqueuing the media job must not
        turn a successful turn into a 500."""
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        request.app.state.arq_pool.enqueue_job = AsyncMock(side_effect=RuntimeError('redis down'))
        user = MagicMock(id='u1')
        media_context = {
            "image_prompt": "a portrait", "narration_text": "hello",
            "voice_id": "af_sarah", "voice_speed": 1.0,
        }

        with patch('main.engine_step', new=AsyncMock(return_value=_engine_result(turn=2, media_context=media_context))), \
             patch('main.AsyncSessionLocal'), \
             patch('main._upsert_session_log', new=AsyncMock()):
            result = await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')

        assert result.response == 'The room is quiet.'
        redis.delete.assert_awaited_once_with('lock:session:sess-1')

    @pytest.mark.asyncio
    async def test_no_enqueue_when_media_context_is_none(self):
        redis = _redis()
        redis.get = AsyncMock(return_value=json.dumps(_ctx()).encode())
        request = MagicMock()
        request.app.state.arq_pool.enqueue_job = AsyncMock()
        user = MagicMock(id='u1')

        with patch('main.engine_step', new=AsyncMock(return_value=_engine_result(turn=2, media_context=None))), \
             patch('main.AsyncSessionLocal'), \
             patch('main._upsert_session_log', new=AsyncMock()):
            await main._execute_turn(request, 'sess-1', user, redis, 'hi', None, 'turn')

        request.app.state.arq_pool.enqueue_job.assert_not_awaited()


class TestThinWrapperRoutes:
    @pytest.mark.asyncio
    async def test_play_turn_calls_execute_turn_with_mode_turn(self):
        request = MagicMock()
        body = MagicMock(input='hello', engine_model=None)
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main._execute_turn', new=AsyncMock(return_value='ok')) as mock_execute:
            result = await main.play_turn.__wrapped__('sess-1', request, body, cu=user)

        assert result == 'ok'
        assert mock_execute.await_args.kwargs.get('mode', mock_execute.await_args.args[-1]) == 'turn'

    @pytest.mark.asyncio
    async def test_continue_turn_calls_execute_turn_with_mode_continue(self):
        request = MagicMock()
        body = MagicMock(engine_model=None)
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main._execute_turn', new=AsyncMock(return_value='ok')) as mock_execute:
            await main.continue_turn.__wrapped__('sess-1', request, body, cu=user)

        assert mock_execute.await_args.kwargs.get('mode', mock_execute.await_args.args[-1]) == 'continue'

    @pytest.mark.asyncio
    async def test_regenerate_turn_calls_execute_turn_with_mode_regenerate(self):
        request = MagicMock()
        body = MagicMock(engine_model=None)
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main._execute_turn', new=AsyncMock(return_value='ok')) as mock_execute:
            await main.regenerate_turn.__wrapped__('sess-1', request, body, cu=user)

        assert mock_execute.await_args.kwargs.get('mode', mock_execute.await_args.args[-1]) == 'regenerate'
