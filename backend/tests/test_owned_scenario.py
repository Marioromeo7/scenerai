"""Tests for main.py's _get_owned_scenario -- the shared authorization gate
in front of every scenario-mutating route (update, delete, publish,
retry-prefab, unpublish). Had zero coverage before this: the audit that
found the save/unsave and last-session bugs (see test_scenario_save.py,
test_last_session.py) turned up essentially no direct test coverage
anywhere in main.py -- this is the single highest-leverage gap to close,
since one correct/incorrect behavior here is shared by five endpoints at
once. Same direct-call, mocked-dependency style as test_regenerate_media.py
-- no TestClient/DB fixture exists in this suite (see conftest.py)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

import main


def _mock_db(scalar_result=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    db.execute = AsyncMock(return_value=exec_result)
    return db


class TestGetOwnedScenario:
    @pytest.mark.asyncio
    async def test_returns_scenario_when_owned_by_requesting_user(self):
        scenario = MagicMock(id='scn-1', creator_id='u1')
        db = _mock_db(scalar_result=scenario)
        user = MagicMock(id='u1')

        result = await main._get_owned_scenario(db, 'scn-1', user)
        assert result is scenario

    @pytest.mark.asyncio
    async def test_404_when_scenario_does_not_exist(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._get_owned_scenario(db, 'scn-1', user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_scenario_exists_but_owned_by_someone_else(self):
        """Ownership is filtered at the query level (creator_id == cu.id in
        the WHERE clause), not checked after the fact -- a scenario owned by
        another user must come back as scalar_one_or_none()=None here, the
        same 404 as "doesn't exist at all." Confirms the query actually
        scopes by creator_id, not just by id."""
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='attacker')

        with pytest.raises(HTTPException) as exc_info:
            await main._get_owned_scenario(db, 'someone-elses-scenario', user)
        assert exc_info.value.status_code == 404

        compiled = str(db.execute.call_args.args[0])
        assert 'creator_id' in compiled

    @pytest.mark.asyncio
    async def test_query_filters_by_both_id_and_creator_id(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        try:
            await main._get_owned_scenario(db, 'scn-1', user)
        except HTTPException:
            pass

        compiled = str(db.execute.call_args.args[0])
        assert 'scenarios.id' in compiled
        assert 'creator_id' in compiled
