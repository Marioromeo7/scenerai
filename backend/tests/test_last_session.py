"""Tests for main.py's get_last_session -- had zero coverage before this
(found live via code review). This endpoint's history is rendered as the
visible chat transcript immediately on session start, before the engine
finishes initializing, so it must match exactly the row create_session's
own resume query would pick -- a persona-agnostic version of this query
previously let it show a different persona's conversation glued onto a
freshly-initialized engine that had no memory of it. Same direct-call,
mocked-dependency style as test_regenerate_media.py -- no TestClient/DB
fixture exists in this suite (see conftest.py)."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import main


def _mock_db(scalar_result=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    db.execute = AsyncMock(return_value=exec_result)
    return db


class TestGetLastSession:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_matching_log(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        result = await main.get_last_session('scn-1', 'persona-1', db=db, cu=user)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_session_data_when_found(self):
        started = datetime(2026, 1, 1, tzinfo=timezone.utc)
        log = MagicMock(
            session_id='sess-1', scenario_id='scn-1', turns_count=4,
            started_at=started, ended_at=None,
            history=[{'role': 'assistant', 'content': 'The room is quiet.'}],
        )
        db = _mock_db(scalar_result=log)
        user = MagicMock(id='u1')

        result = await main.get_last_session('scn-1', 'persona-1', db=db, cu=user)

        assert result['session_id'] == 'sess-1'
        assert result['turns_count'] == 4
        assert result['ended_at'] is None
        assert result['history'] == [{'role': 'assistant', 'content': 'The room is quiet.'}]

    @pytest.mark.asyncio
    async def test_regression_query_filters_by_persona_id(self):
        """Before the fix, this query filtered only on user_id + scenario_id
        -- persona-agnostic -- while create_session's actual resume query
        was always persona-scoped. Playing scenario X with persona A then
        persona B would show persona A's history glued onto persona B's
        freshly-initialized (turn-0) engine. Inspecting the compiled
        statement (not just that db.execute was called) is what actually
        catches a regression here -- a looser mock would pass either way."""
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        await main.get_last_session('scn-1', 'persona-1', db=db, cu=user)

        compiled = str(db.execute.call_args.args[0])
        assert 'persona_id' in compiled

    @pytest.mark.asyncio
    async def test_regression_query_requires_engine_state_present(self):
        """A row this endpoint shows must be one create_session could
        actually resume from -- create_session's own resume query already
        required engine_state IS NOT NULL; this must match it exactly."""
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        await main.get_last_session('scn-1', 'persona-1', db=db, cu=user)

        compiled = str(db.execute.call_args.args[0])
        assert 'engine_state IS NOT NULL' in compiled
