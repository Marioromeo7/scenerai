"""Tests for main.py's _upsert_session_log — the fix-map item 3 checkpoint
helper that persists session state to Postgres on every turn, not just on
clean end_session. Only exercised live before this."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import main


def _run(coro):
    return asyncio.run(coro)


def _mock_db():
    """db.add() is sync in real SQLAlchemy — AsyncMock's attribute cascading
    would otherwise turn it into an unawaited coroutine when called."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _mock_session_local(db=None):
    """The db=None path in _upsert_session_log calls `db = AsyncSessionLocal()`
    directly (not `async with ... as db`) — plain instantiation, not a context
    manager, so the mock just needs to return db when called."""
    db = db or _mock_db()
    session_local = MagicMock(return_value=db)
    return session_local, db


def _set_scalar_result(db, value):
    """(await db.execute(...)).scalar_one_or_none() is sync on the awaited
    result — must be forced to plain MagicMock, see test_worker.py for why."""
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=value)


def _ctx(**overrides):
    base = {
        "session_id": "session-1",
        "user_id": "user-1",
        "scenario_id": "scenario-1",
        "persona_id": "persona-1",
        "engine_state": {"entities": []},
        "turn": 3,
        "preview": False,
        "history": [
            {"role": "user", "content": "I look around."},
            {"role": "assistant", "content": "The room is quiet."},
            {"role": "system", "content": "internal note — must be filtered out"},
        ],
    }
    base.update(overrides)
    return base


class TestUpsertSessionLog:
    def test_preview_session_skips_everything(self):
        session_local, db = _mock_session_local()
        with patch('main.AsyncSessionLocal', session_local):
            _run(main._upsert_session_log(_ctx(preview=True)))
        db.execute.assert_not_called()
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_new_session_inserts_with_filtered_history(self):
        session_local, db = _mock_session_local()
        _set_scalar_result(db, None)  # no existing row

        with patch('main.AsyncSessionLocal', session_local):
            _run(main._upsert_session_log(_ctx()))

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.session_id == "session-1"
        assert added.user_id == "user-1"
        assert added.turns_count == 3
        # system-role message must be filtered out; only role/content kept
        assert added.history == [
            {"role": "user", "content": "I look around."},
            {"role": "assistant", "content": "The room is quiet."},
        ]
        db.commit.assert_awaited_once()

    def test_new_session_without_ended_at_has_none(self):
        session_local, db = _mock_session_local()
        _set_scalar_result(db, None)

        with patch('main.AsyncSessionLocal', session_local):
            _run(main._upsert_session_log(_ctx()))

        added = db.add.call_args[0][0]
        assert added.ended_at is None

    def test_existing_session_updates_in_place_not_inserted(self):
        session_local, db = _mock_session_local()
        existing = MagicMock(history=[], turns_count=0, engine_state=None, ended_at=None)
        _set_scalar_result(db, existing)

        with patch('main.AsyncSessionLocal', session_local):
            _run(main._upsert_session_log(_ctx(turn=5)))

        db.add.assert_not_called()
        assert existing.turns_count == 5
        assert existing.engine_state == {"entities": []}
        db.commit.assert_awaited_once()

    def test_existing_session_ended_at_not_touched_when_not_passed(self):
        """A mid-session turn checkpoint must never clear an already-set
        ended_at, and must not set one when not asked to."""
        session_local, db = _mock_session_local()
        existing = MagicMock(history=[], turns_count=0, engine_state=None, ended_at=None)
        _set_scalar_result(db, existing)

        with patch('main.AsyncSessionLocal', session_local):
            _run(main._upsert_session_log(_ctx()))

        assert existing.ended_at is None

    def test_ended_at_set_when_passed(self):
        session_local, db = _mock_session_local()
        existing = MagicMock(history=[], turns_count=0, engine_state=None, ended_at=None)
        _set_scalar_result(db, existing)
        when = datetime.now(timezone.utc)

        with patch('main.AsyncSessionLocal', session_local):
            _run(main._upsert_session_log(_ctx(), ended_at=when))

        assert existing.ended_at == when

    def test_own_session_closes_db_when_none_passed(self):
        session_local, db = _mock_session_local()
        _set_scalar_result(db, None)

        with patch('main.AsyncSessionLocal', session_local):
            _run(main._upsert_session_log(_ctx()))

        db.close.assert_awaited_once()

    def test_passed_db_is_reused_not_closed(self):
        """end_session passes its own request-scoped db — _upsert_session_log
        must use it directly, not open (or close) a second session."""
        db = _mock_db()
        _set_scalar_result(db, None)
        session_local = MagicMock()  # must never be constructed

        with patch('main.AsyncSessionLocal', session_local):
            _run(main._upsert_session_log(_ctx(), db=db))

        session_local.assert_not_called()
        db.add.assert_called_once()
        db.commit.assert_awaited_once()
        db.close.assert_not_called()
