"""Tests for main.py's save_scenario/unsave_scenario -- had zero coverage
before this (found live via code review), which is exactly how a real
double-decrement race in unsave_scenario went unnoticed. Same direct-call,
mocked-dependency style as test_regenerate_media.py/test_admin_telemetry.py
-- no TestClient/DB fixture exists in this suite (see conftest.py)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import main


def _exec_result(scalar=None, rowcount=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    if rowcount is not None:
        r.rowcount = rowcount
    return r


def _mock_db(execute_results=None):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute_results or [])
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _scenario(creator_id='owner-1', is_public=False, is_published=False):
    return MagicMock(id='scn-1', creator_id=creator_id, is_public=is_public, is_published=is_published)


class TestSaveScenario:
    @pytest.mark.asyncio
    async def test_404_when_scenario_not_found(self):
        db = _mock_db([_exec_result(scalar=None)])
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.save_scenario('scn-1', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_not_owned_and_not_public_published(self):
        scenario = _scenario(creator_id='someone-else', is_public=False)
        db = _mock_db([_exec_result(scalar=scenario)])
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.save_scenario('scn-1', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_public_published_scenario_saveable_by_non_owner(self):
        scenario = _scenario(creator_id='someone-else', is_public=True, is_published=True)
        db = _mock_db([_exec_result(scalar=scenario), _exec_result(scalar=None), _exec_result()])
        user = MagicMock(id='u1')

        result = await main.save_scenario('scn-1', db=db, cu=user)
        assert result == {"saved": True}
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_already_saved_returns_success_without_inserting_again(self):
        scenario = _scenario(creator_id='u1')
        existing_save = MagicMock()
        db = _mock_db([_exec_result(scalar=scenario), _exec_result(scalar=existing_save)])
        user = MagicMock(id='u1')

        result = await main.save_scenario('scn-1', db=db, cu=user)

        assert result == {"saved": True}
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_new_save_inserts_row_and_increments_count(self):
        scenario = _scenario(creator_id='u1')
        db = _mock_db([_exec_result(scalar=scenario), _exec_result(scalar=None), _exec_result()])
        user = MagicMock(id='u1')

        result = await main.save_scenario('scn-1', db=db, cu=user)

        assert result == {"saved": True}
        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.user_id == 'u1'
        assert added.scenario_id == 'scn-1'
        # select scenario, select existing save, update saves_count
        assert db.execute.await_count == 3
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_duplicate_save_is_idempotent_not_an_error(self):
        """Two concurrent saves both pass the already_saved=None check and
        both insert -- the second commit hits ScenarioSave's unique
        constraint. Must roll back and still report success, not 500."""
        scenario = _scenario(creator_id='u1')
        db = _mock_db([_exec_result(scalar=scenario), _exec_result(scalar=None), _exec_result()])
        db.commit = AsyncMock(side_effect=IntegrityError('stmt', {}, Exception('dup')))
        user = MagicMock(id='u1')

        result = await main.save_scenario('scn-1', db=db, cu=user)

        assert result == {"saved": True}
        db.rollback.assert_awaited_once()


class TestUnsaveScenario:
    @pytest.mark.asyncio
    async def test_decrements_count_when_a_row_was_actually_deleted(self):
        db = _mock_db([_exec_result(rowcount=1), _exec_result()])
        user = MagicMock(id='u1')

        await main.unsave_scenario('scn-1', db=db, cu=user)

        # delete, then the saves_count update
        assert db.execute.await_count == 2
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_regression_no_double_decrement_when_nothing_was_deleted(self):
        """The race this replaced: two concurrent unsaves both loading the
        row before either committed would both run the saves_count decrement
        below, undercounting relative to the real number of ScenarioSave
        rows removed (one logical unsave, counted twice). A rowcount-gated
        bulk DELETE means the request that finds nothing to delete (because
        a concurrent request already removed it) must not touch the counter
        at all -- confirmed live against real SQLAlchemy that this DELETE
        succeeds silently with rowcount=0, it does not raise."""
        db = _mock_db([_exec_result(rowcount=0)])
        user = MagicMock(id='u1')

        await main.unsave_scenario('scn-1', db=db, cu=user)

        # only the delete -- no saves_count update call at all
        assert db.execute.await_count == 1
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsave_does_not_raise_when_scenario_never_saved(self):
        db = _mock_db([_exec_result(rowcount=0)])
        user = MagicMock(id='u1')

        # must not raise -- unsaving something never saved is a normal no-op
        await main.unsave_scenario('scn-1', db=db, cu=user)
