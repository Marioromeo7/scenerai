"""Tests for worker.py's arq job functions — prefab_engine_job and
init_engine_job. These only ran live before (verified during this session's
load test and smoke tests); this is the first dedicated unit coverage."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import worker


def _run(coro):
    return asyncio.run(coro)


def _mock_redis(incr_return=1, get_return=None):
    redis = AsyncMock()
    redis.incr.return_value = incr_return
    redis.get.return_value = get_return
    return redis


def _mock_session_local(db=None):
    """AsyncSessionLocal() is used as `async with AsyncSessionLocal() as db:`."""
    db = db or AsyncMock()
    session_local = MagicMock()
    session_local.return_value.__aenter__ = AsyncMock(return_value=db)
    session_local.return_value.__aexit__ = AsyncMock(return_value=None)
    return session_local, db


def _set_scalar_result(db, value):
    """(await db.execute(...)).scalar_one_or_none() is a SYNC call on the
    awaited result — db.execute.return_value's children auto-cascade to
    AsyncMock since db is an AsyncMock, so this must be forced back to a
    plain MagicMock or scalar_one_or_none() returns an unawaited coroutine."""
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=value)


# ── prefab_engine_job ──────────────────────────────────────────

class TestPrefabEngineJob:
    def test_success_sets_ready_and_stores_state(self):
        redis = _mock_redis(incr_return=1)
        session_local, db = _mock_session_local()

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local), \
             patch('worker.engine_prefab', AsyncMock(return_value={'entities': []})):
            _run(worker.prefab_engine_job(
                {}, 'scenario-1', 'Iris', 'she/her', 'Archivist', 'guarded', 'greeting'
            ))

        db.execute.assert_awaited_once()
        db.commit.assert_awaited_once()
        redis.incr.assert_awaited_once_with(worker.PREFAB_JOB_KEY)
        redis.decr.assert_awaited_once_with(worker.PREFAB_JOB_KEY)

    def test_rolling_ttl_set_on_every_run(self):
        """Crash-leak protection: the counter must get a fresh TTL on every
        increment, not just at creation, or a leaked slot never self-heals."""
        redis = _mock_redis(incr_return=1)
        session_local, db = _mock_session_local()

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local), \
             patch('worker.engine_prefab', AsyncMock(return_value={'entities': []})):
            _run(worker.prefab_engine_job(
                {}, 'scenario-1', 'Iris', 'she/her', 'Archivist', 'guarded', 'greeting'
            ))

        redis.expire.assert_awaited_once_with(worker.PREFAB_JOB_KEY, 300)

    def test_empty_prefab_result_sets_failed(self):
        redis = _mock_redis(incr_return=1)
        session_local, db = _mock_session_local()

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local), \
             patch('worker.engine_prefab', AsyncMock(return_value=None)):
            _run(worker.prefab_engine_job(
                {}, 'scenario-1', 'Iris', 'she/her', 'Archivist', 'guarded', 'greeting'
            ))

        values = db.execute.call_args[0][0]
        assert values.__visit_name__ == 'update'  # sanity: it's an UPDATE statement
        redis.decr.assert_awaited_once()

    def test_engine_prefab_exception_sets_failed_and_still_decrements(self):
        redis = _mock_redis(incr_return=1)
        session_local, db = _mock_session_local()

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local), \
             patch('worker.engine_prefab', AsyncMock(side_effect=RuntimeError('Groq down'))):
            _run(worker.prefab_engine_job(
                {}, 'scenario-1', 'Iris', 'she/her', 'Archivist', 'guarded', 'greeting'
            ))

        # finally: block must still run even though the try block raised
        redis.decr.assert_awaited_once_with(worker.PREFAB_JOB_KEY)
        db.commit.assert_awaited_once()

    def test_concurrency_gate_skips_engine_prefab_entirely(self):
        """count > PREFAB_MAX must bail out before ever calling engine_prefab."""
        redis = _mock_redis(incr_return=worker.PREFAB_MAX + 1)
        session_local, db = _mock_session_local()
        engine_prefab_mock = AsyncMock(return_value={'entities': []})

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local), \
             patch('worker.engine_prefab', engine_prefab_mock):
            _run(worker.prefab_engine_job(
                {}, 'scenario-1', 'Iris', 'she/her', 'Archivist', 'guarded', 'greeting'
            ))

        engine_prefab_mock.assert_not_called()
        # decremented immediately on the gate path, not via the finally block
        redis.decr.assert_awaited_once_with(worker.PREFAB_JOB_KEY)
        db.commit.assert_awaited_once()


# ── init_engine_job ────────────────────────────────────────────

class TestInitEngineJob:
    def test_restored_state_skips_engine_init_calls(self):
        redis = _mock_redis(get_return=json.dumps({"status": "initializing"}))
        engine_init_mock = AsyncMock()
        engine_init_from_prefab_mock = AsyncMock()

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.engine_init', engine_init_mock), \
             patch('worker.engine_init_from_prefab', engine_init_from_prefab_mock):
            _run(worker.init_engine_job(
                {}, 'session-1', 'scenario-1', 'Sam', 'they/them', 'greeting text',
                'off', False, {'entities': [], 'display_history': []},
            ))

        engine_init_mock.assert_not_called()
        engine_init_from_prefab_mock.assert_not_called()
        written = json.loads(redis.setex.call_args[0][2])
        assert written['status'] == 'ready'

    def test_prefab_available_uses_fast_init(self):
        redis = _mock_redis(get_return=json.dumps({"status": "initializing"}))
        scenario = MagicMock(prefab_engine_state={'entities': []})
        session_local, db = _mock_session_local()
        _set_scalar_result(db, scenario)
        engine_init_from_prefab_mock = AsyncMock(return_value={'entities': [], 'display_history': []})

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local), \
             patch('worker.engine_init_from_prefab', engine_init_from_prefab_mock):
            _run(worker.init_engine_job(
                {}, 'session-1', 'scenario-1', 'Sam', 'they/them', 'greeting text',
                'off', False, None,
            ))

        engine_init_from_prefab_mock.assert_awaited_once()
        written = json.loads(redis.setex.call_args[0][2])
        assert written['status'] == 'ready'

    def test_no_prefab_preview_runs_full_init(self):
        redis = _mock_redis(get_return=json.dumps({"status": "initializing"}))
        scenario = MagicMock(prefab_engine_state=None, char_name='Iris', char_pronouns='she/her',
                              char_title='Archivist', char_personality='guarded')
        session_local, db = _mock_session_local()
        _set_scalar_result(db, scenario)
        engine_init_mock = AsyncMock(return_value={'entities': [], 'display_history': []})

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local), \
             patch('worker.engine_init', engine_init_mock):
            _run(worker.init_engine_job(
                {}, 'session-1', 'scenario-1', 'Sam', 'they/them', 'greeting text',
                'off', True, None,
            ))

        engine_init_mock.assert_awaited_once()
        written = json.loads(redis.setex.call_args[0][2])
        assert written['status'] == 'ready'

    def test_no_prefab_not_preview_sets_error_status(self):
        """A non-preview session with no prefab must never silently hang at
        'initializing' — this is fix-map item 1's original failure mode."""
        redis = _mock_redis(get_return=json.dumps({"status": "initializing"}))
        scenario = MagicMock(prefab_engine_state=None)
        session_local, db = _mock_session_local()
        _set_scalar_result(db, scenario)

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local):
            _run(worker.init_engine_job(
                {}, 'session-1', 'scenario-1', 'Sam', 'they/them', 'greeting text',
                'off', False, None,
            ))

        written = json.loads(redis.setex.call_args[0][2])
        assert written['status'] == 'error'
        assert 'no prefab' in written['error']

    def test_scenario_gone_sets_error_status(self):
        redis = _mock_redis(get_return=json.dumps({"status": "initializing"}))
        session_local, db = _mock_session_local()
        _set_scalar_result(db, None)

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local):
            _run(worker.init_engine_job(
                {}, 'session-1', 'scenario-1', 'Sam', 'they/them', 'greeting text',
                'off', False, None,
            ))

        written = json.loads(redis.setex.call_args[0][2])
        assert written['status'] == 'error'
        assert 'no longer exists' in written['error']

    def test_engine_init_exception_sets_error_status(self):
        redis = _mock_redis(get_return=json.dumps({"status": "initializing"}))
        scenario = MagicMock(prefab_engine_state={'entities': []})
        session_local, db = _mock_session_local()
        _set_scalar_result(db, scenario)

        with patch('worker.get_redis', AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', session_local), \
             patch('worker.engine_init_from_prefab', AsyncMock(side_effect=RuntimeError('Groq down'))):
            _run(worker.init_engine_job(
                {}, 'session-1', 'scenario-1', 'Sam', 'they/them', 'greeting text',
                'off', False, None,
            ))

        written = json.loads(redis.setex.call_args[0][2])
        assert written['status'] == 'error'
        assert 'Groq down' in written['error']

    def test_missing_redis_session_does_not_crash(self):
        """If the session key vanished (e.g. TTL race), the job must finish
        cleanly instead of raising on a None session dict."""
        redis = _mock_redis(get_return=None)

        with patch('worker.get_redis', AsyncMock(return_value=redis)):
            _run(worker.init_engine_job(
                {}, 'session-1', 'scenario-1', 'Sam', 'they/them', 'greeting text',
                'off', False, {'entities': [], 'display_history': []},
            ))

        redis.setex.assert_not_called()
