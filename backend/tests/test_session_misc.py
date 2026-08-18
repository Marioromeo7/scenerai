"""Tests for main.py's session_status, rate_turn, session_history, and
get_session_history -- had zero coverage before this (found live via code
review: no route in main.py had direct tests). Same direct-call, mocked-
dependency style as test_regenerate_media.py -- no TestClient/DB fixture
exists in this suite (see conftest.py)."""
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import main


class TestSessionStatus:
    @pytest.mark.asyncio
    async def test_404_when_session_not_found(self):
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            with pytest.raises(HTTPException) as exc_info:
                await main.session_status('sess-1', cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_403_when_not_the_owning_user(self):
        ctx = {"user_id": "someone-else", "status": "ready", "turn": 2}
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(ctx).encode())
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            with pytest.raises(HTTPException) as exc_info:
                await main.session_status('sess-1', cu=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_status_and_turn(self):
        ctx = {"user_id": "u1", "status": "ready", "turn": 5}
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(ctx).encode())
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            result = await main.session_status('sess-1', cu=user)
        assert result == {"status": "ready", "turn": 5}

    @pytest.mark.asyncio
    async def test_defaults_when_fields_missing(self):
        ctx = {"user_id": "u1"}
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(ctx).encode())
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            result = await main.session_status('sess-1', cu=user)
        assert result == {"status": "initializing", "turn": 0}


def _mock_db(scalar_result=None, scalars_list=None, all_rows=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    if scalars_list is not None:
        exec_result.scalars.return_value.all.return_value = scalars_list
    if all_rows is not None:
        exec_result.all.return_value = all_rows
    db.execute = AsyncMock(return_value=exec_result)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestRateTurn:
    @pytest.mark.asyncio
    async def test_creates_new_rating(self):
        db = _mock_db(scalar_result=None)
        from schemas import TurnRatingCreate
        user = MagicMock(id='u1')

        result = await main.rate_turn('sess-1', 3, TurnRatingCreate(rating=5), db=db, cu=user)

        added = db.add.call_args[0][0]
        assert added.session_id == 'sess-1'
        assert added.turn == 3
        assert added.rating == 5
        assert result.rating == 5

    @pytest.mark.asyncio
    async def test_updates_existing_rating_in_place_not_a_new_insert(self):
        existing = MagicMock(rating=2)
        db = _mock_db(scalar_result=existing)
        from schemas import TurnRatingCreate
        user = MagicMock(id='u1')

        result = await main.rate_turn('sess-1', 3, TurnRatingCreate(rating=5), db=db, cu=user)

        db.add.assert_not_called()
        assert existing.rating == 5
        assert result.rating == 5

    @pytest.mark.asyncio
    async def test_regression_concurrent_double_click_race_falls_back_to_update_not_500(self):
        """Two concurrent ratings for the same turn both see existing=None
        and both insert -- the second violates
        UniqueConstraint(user_id, session_id, turn). Must roll back and
        update the row the other request just created, not surface a raw
        500 for what's a normal double-click race."""
        existing_after_race = MagicMock(rating=1)
        db = MagicMock()
        first_check = MagicMock(); first_check.scalar_one_or_none.return_value = None
        second_check = MagicMock(); second_check.scalar_one_or_none.return_value = existing_after_race
        db.execute = AsyncMock(side_effect=[first_check, second_check])
        db.add = MagicMock()
        db.commit = AsyncMock(side_effect=[IntegrityError('stmt', {}, Exception('dup')), None])
        db.rollback = AsyncMock()
        from schemas import TurnRatingCreate
        user = MagicMock(id='u1')

        result = await main.rate_turn('sess-1', 3, TurnRatingCreate(rating=4), db=db, cu=user)

        db.rollback.assert_awaited_once()
        assert existing_after_race.rating == 4
        assert result.rating == 4


class TestSessionHistory:
    @pytest.mark.asyncio
    async def test_returns_only_requesting_users_sessions(self):
        db = _mock_db(scalars_list=[], all_rows=[])

        user = MagicMock(id='u1')
        result = await main.session_history(cursor=None, limit=20, db=db, cu=user)

        assert result['items'] == []
        compiled = str(db.execute.call_args_list[0].args[0])
        assert 'user_id' in compiled

    @pytest.mark.asyncio
    async def test_400_on_invalid_cursor(self):
        db = _mock_db()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.session_history(cursor='garbage', limit=20, db=db, cu=user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_attaches_thumbnail_from_latest_ready_media(self):
        # thumbnail_url=None explicitly -- a real SessionLog ORM row has no
        # such column at all (SessionLogOut.model_copy() attaches it after
        # validation), so getattr() genuinely raises AttributeError there
        # and Pydantic falls back to the field's None default. MagicMock
        # auto-generates any attribute access instead of raising, which
        # would otherwise mask that and fail validation on a mock child.
        log = MagicMock(
            id='log-1', session_id='sess-1', scenario_id='scn-1', persona_id='p1',
            turns_count=3, started_at=datetime.now(timezone.utc), ended_at=None,
            thumbnail_url=None,
        )
        db = MagicMock()
        exec_logs = MagicMock()
        exec_logs.scalars.return_value.all.return_value = [log]
        exec_media = MagicMock()
        exec_media.all.return_value = [('sess-1', '/media/sess-1/3.png')]
        db.execute = AsyncMock(side_effect=[exec_logs, exec_media])
        user = MagicMock(id='u1')

        result = await main.session_history(cursor=None, limit=20, db=db, cu=user)

        assert result['items'][0].thumbnail_url == '/media/sess-1/3.png'


class TestGetSessionHistory:
    @pytest.mark.asyncio
    async def test_404_when_not_found_or_not_owned(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.get_session_history('sess-1', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_history_and_turn_count(self):
        log = MagicMock(history=[{'role': 'assistant', 'content': 'hi'}], turns_count=2)
        db = _mock_db(scalar_result=log)
        user = MagicMock(id='u1')

        result = await main.get_session_history('sess-1', db=db, cu=user)

        assert result == {
            "session_id": 'sess-1',
            "history": [{'role': 'assistant', 'content': 'hi'}],
            "turns": 2,
        }
