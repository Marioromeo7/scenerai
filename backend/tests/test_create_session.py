"""Tests for main.py's create_session -- had zero coverage before this,
despite being where the persona-scoped resume query lives (the fix for the
get_last_session persona-mismatch bug, see test_last_session.py, exists
specifically to match this route's own resume query exactly). Same
direct-call, mocked-dependency style as test_regenerate_media.py -- no
TestClient/DB fixture exists in this suite (see conftest.py).

create_session carries a slowapi @limiter.limit(...) decorator, which
raises if `request` isn't a real starlette.requests.Request (confirmed
live in test_auth_routes.py) -- called via __wrapped__ here."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import main


def _scenario(id='scn-1', creator_id='u1', is_public=True, is_published=True):
    s = MagicMock(id=id, creator_id=creator_id, is_public=is_public, is_published=is_published)
    s.char_name = 'Iris'
    s.char_pronouns = 'she/her'
    s.greeting = 'The archive is sealed.'
    return s


def _persona(id='p1', user_id='u1'):
    p = MagicMock(id=id, user_id=user_id, pronouns='they/them')
    p.name = 'Alex'  # `name=` is a reserved Mock constructor kwarg
    return p


def _db(execute_results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute_results)
    return db


def _exec(scalar):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    return r


def _redis_with_working_enqueue():
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.sadd = AsyncMock()
    return redis


def _request_with_working_enqueue():
    request = MagicMock()
    request.app.state.arq_pool.enqueue_job = AsyncMock()
    return request


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_404_when_scenario_does_not_exist(self):
        db = _db([_exec(None)])
        from schemas import SessionCreate
        body = SessionCreate(scenario_id='scn-1', persona_id='p1')
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.create_session.__wrapped__(request, body, db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_scenario_private_and_not_owned(self):
        s = _scenario(creator_id='someone-else', is_public=False)
        db = _db([_exec(s)])
        from schemas import SessionCreate
        body = SessionCreate(scenario_id='scn-1', persona_id='p1')
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.create_session.__wrapped__(request, body, db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_preview_requested_by_non_owner(self):
        """Preview mode is for the creator only, even on an otherwise
        publicly-playable scenario."""
        s = _scenario(creator_id='someone-else', is_public=True, is_published=True)
        db = _db([_exec(s)])
        from schemas import SessionCreate
        body = SessionCreate(scenario_id='scn-1', persona_id='p1', preview=True)
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.create_session.__wrapped__(request, body, db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_persona_not_found_or_not_owned(self):
        s = _scenario()
        db = _db([_exec(s), _exec(None)])
        from schemas import SessionCreate
        body = SessionCreate(scenario_id='scn-1', persona_id='not-mine')
        request = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=_redis_with_working_enqueue())):
            with pytest.raises(HTTPException) as exc_info:
                await main.create_session.__wrapped__(request, body, db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_regression_resume_query_scoped_by_persona_not_just_scenario(self):
        """The exact query get_last_session's persona_id fix (see
        test_last_session.py) was written to match. Playing scenario X with
        persona A must never resume from a SessionLog written under persona
        B for the same scenario."""
        s = _scenario()
        p = _persona()
        db = _db([_exec(s), _exec(p), _exec(None)])
        from schemas import SessionCreate
        body = SessionCreate(scenario_id='scn-1', persona_id='p1', preview=False)
        request = _request_with_working_enqueue()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=_redis_with_working_enqueue())):
            await main.create_session.__wrapped__(request, body, db=db, cu=user)

        resume_query_compiled = str(db.execute.await_args_list[2].args[0])
        assert 'persona_id' in resume_query_compiled
        assert 'engine_state IS NOT NULL' in resume_query_compiled

    @pytest.mark.asyncio
    async def test_preview_skips_resume_lookup_entirely(self):
        s = _scenario()
        p = _persona()
        db = _db([_exec(s), _exec(p)])  # only 2 queries -- no resume lookup for preview
        from schemas import SessionCreate
        body = SessionCreate(scenario_id='scn-1', persona_id='p1', preview=True)
        request = _request_with_working_enqueue()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=_redis_with_working_enqueue())):
            await main.create_session.__wrapped__(request, body, db=db, cu=user)

        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_passes_resumed_engine_state_to_init_job(self):
        s = _scenario()
        p = _persona()
        last_log = MagicMock(engine_state={'entities': ['resumed']})
        db = _db([_exec(s), _exec(p), _exec(last_log)])
        from schemas import SessionCreate
        body = SessionCreate(scenario_id='scn-1', persona_id='p1', preview=False)
        request = _request_with_working_enqueue()
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=_redis_with_working_enqueue())):
            await main.create_session.__wrapped__(request, body, db=db, cu=user)

        job_args = request.app.state.arq_pool.enqueue_job.await_args.args
        assert job_args[0] == 'init_engine_job'
        assert job_args[-1] == {'entities': ['resumed']}

    @pytest.mark.asyncio
    async def test_regression_enqueue_failure_marks_placeholder_error_not_stuck_initializing(self):
        """Without this, a failed enqueue call (e.g. Redis briefly
        unreachable) would leave the session's placeholder stuck at status
        'initializing' forever, with no job ever queued to move it forward
        -- the frontend's waitForEngine poll would spin until its own
        10-minute timeout instead of surfacing a clear error quickly."""
        s = _scenario()
        p = _persona()
        db = _db([_exec(s), _exec(p), _exec(None)])
        from schemas import SessionCreate
        body = SessionCreate(scenario_id='scn-1', persona_id='p1', preview=False)
        request = MagicMock()
        request.app.state.arq_pool.enqueue_job = AsyncMock(side_effect=RuntimeError('redis down'))
        user = MagicMock(id='u1')
        redis = _redis_with_working_enqueue()

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            with pytest.raises(HTTPException) as exc_info:
                await main.create_session.__wrapped__(request, body, db=db, cu=user)

        assert exc_info.value.status_code == 503
        # setex called twice: initial placeholder, then the error update
        assert redis.setex.await_count == 2
        second_call_payload = redis.setex.await_args.args[2]
        assert '"status": "error"' in second_call_payload
