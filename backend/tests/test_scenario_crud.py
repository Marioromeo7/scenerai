"""Tests for main.py's scenario CRUD/list routes (create/list/get/update/
delete/saved-ids/public) -- had zero coverage before this (found live via
code review: no route in main.py had direct tests). Same direct-call,
mocked-dependency style as test_regenerate_media.py/test_owned_scenario.py
-- no TestClient/DB fixture exists in this suite (see conftest.py).

create_scenario/report_scenario carry a slowapi @limiter.limit(...)
decorator, which raises if `request` isn't a real starlette.requests.Request
(confirmed live in test_auth_routes.py) -- called via __wrapped__ here for
the same reason."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import main


def _mock_db(scalar_result=None, scalars_list=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    if scalars_list is not None:
        exec_result.scalars.return_value.all.return_value = scalars_list
    db.execute = AsyncMock(return_value=exec_result)
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _scenario(id='scn-1', creator_id='u1', is_public=True, is_published=False,
              prefab_status='pending', created_at=None):
    return MagicMock(
        id=id, creator_id=creator_id,
        char_name='Iris', char_pronouns='she/her', char_title='Archivist',
        char_personality='guarded', greeting='The archive is sealed.',
        title='The Sealed Archive', brief='A locked room.', tags=['tension'],
        intensity=3, image_seed='abcd1234', saves_count=0, plays_count=0,
        is_public=is_public, is_published=is_published,
        created_at=created_at or datetime.now(timezone.utc),
        prefab_engine_state=None, prefab_status=prefab_status,
        visual_profile=None, visual_seed=None, visual_status='pending',
        image_url=None, video_url=None,
    )


class TestGetSavedScenarioIds:
    @pytest.mark.asyncio
    async def test_returns_ids_for_requesting_user(self):
        db = _mock_db(scalars_list=['scn-1', 'scn-2'])
        user = MagicMock(id='u1')

        result = await main.get_saved_scenario_ids(db=db, cu=user)

        assert result == {"ids": ['scn-1', 'scn-2']}


class TestGetScenario:
    @pytest.mark.asyncio
    async def test_404_when_scenario_does_not_exist(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.get_scenario('scn-1', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_private_and_not_owned(self):
        s = _scenario(creator_id='someone-else', is_public=False, is_published=False)
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.get_scenario('scn-1', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_owner_can_read_their_own_unpublished_scenario(self):
        s = _scenario(creator_id='u1', is_public=False, is_published=False)
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        result = await main.get_scenario('scn-1', db=db, cu=user)
        assert result.id == 'scn-1'

    @pytest.mark.asyncio
    async def test_non_owner_can_read_public_published_scenario(self):
        s = _scenario(creator_id='someone-else', is_public=True, is_published=True)
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        result = await main.get_scenario('scn-1', db=db, cu=user)
        assert result.id == 'scn-1'


class TestUpdateScenario:
    @pytest.mark.asyncio
    async def test_404_when_not_owned(self):
        db = _mock_db(scalar_result=None)
        from schemas import ScenarioUpdate
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.update_scenario('scn-1', ScenarioUpdate(char_name='New'), db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_403_when_scenario_already_published(self):
        s = _scenario(is_published=True)
        db = _mock_db(scalar_result=s)
        from schemas import ScenarioUpdate
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.update_scenario('scn-1', ScenarioUpdate(char_name='New'), db=db, cu=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_updates_only_provided_fields_and_sanitizes_text(self):
        s = _scenario(is_published=False)
        s.char_personality = 'old personality'
        db = _mock_db(scalar_result=s)
        from schemas import ScenarioUpdate
        user = MagicMock(id='u1')

        with patch('main.sanitize_input', side_effect=lambda t: f'clean:{t}') as mock_sanitize:
            await main.update_scenario(
                'scn-1', ScenarioUpdate(char_personality='new personality'), db=db, cu=user,
            )

        assert s.char_personality == 'clean:new personality'
        mock_sanitize.assert_called_once_with('new personality')
        db.commit.assert_awaited_once()


class TestDeleteScenario:
    @pytest.mark.asyncio
    async def test_404_when_not_owned(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.delete_scenario('scn-1', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_400_when_published(self):
        s = _scenario(is_published=True)
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock()):
            with pytest.raises(HTTPException) as exc_info:
                await main.delete_scenario('scn-1', db=db, cu=user)
        assert exc_info.value.status_code == 400
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletes_unpublished_scenario_and_cleans_up_redis_sessions(self):
        s = _scenario(is_published=False)
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value=[b'session-1', b'session-2'])

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            await main.delete_scenario('scn-1', db=db, cu=user)

        assert redis.delete.await_count == 3  # 2 sessions + the set itself
        db.delete.assert_awaited_once_with(s)
        db.commit.assert_awaited_once()


class TestCreateScenario:
    @pytest.mark.asyncio
    async def test_creates_scenario_owned_by_requesting_user(self):
        db = _mock_db()

        # create_scenario constructs a real Scenario(...) ORM instance and
        # relies on db.refresh() to populate server_default columns (same
        # wrinkle as register()'s User(...) in test_auth_routes.py) before
        # ScenarioOut.model_validate() reads them.
        async def fake_refresh(s):
            s.created_at = datetime.now(timezone.utc)
            s.saves_count = 0
            s.plays_count = 0
            s.is_public = True
            s.is_published = False
            s.prefab_status = 'pending'
            s.visual_status = 'pending'
        db.refresh = AsyncMock(side_effect=fake_refresh)

        from schemas import ScenarioCreate
        body = ScenarioCreate(
            char_name='Iris', char_title='Archivist',
            char_personality='guarded', greeting='The archive is sealed.',
        )
        request = MagicMock()
        user = MagicMock(id='u1')

        with patch('main.generate_scenario_metadata', new=AsyncMock(return_value={
            'title': 'The Sealed Archive', 'brief': 'A locked room.',
            'tags': ['tension'], 'intensity': 3,
        })), patch('main.sanitize_input', side_effect=lambda t: t):
            result = await main.create_scenario.__wrapped__(request, body, db=db, cu=user)

        added = db.add.call_args[0][0]
        assert added.creator_id == 'u1'
        assert added.char_name == 'Iris'
        db.commit.assert_awaited_once()
        assert result.title == 'The Sealed Archive'


class TestListScenarios:
    @pytest.mark.asyncio
    async def test_returns_only_requesting_users_scenarios(self):
        rows = [_scenario(id='scn-1'), _scenario(id='scn-2')]
        db = _mock_db(scalars_list=rows)
        user = MagicMock(id='u1')

        result = await main.list_scenarios(cursor=None, limit=20, db=db, cu=user)

        assert len(result['items']) == 2
        compiled = str(db.execute.call_args.args[0])
        assert 'creator_id' in compiled

    @pytest.mark.asyncio
    async def test_400_on_invalid_cursor(self):
        db = _mock_db(scalars_list=[])
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.list_scenarios(cursor='garbage', limit=20, db=db, cu=user)
        assert exc_info.value.status_code == 400


class TestListPublic:
    @pytest.mark.asyncio
    async def test_query_filters_by_public_and_published(self):
        db = _mock_db(scalars_list=[])

        await main.list_public(cursor=None, limit=20, db=db)

        compiled = str(db.execute.call_args.args[0])
        assert 'is_public' in compiled
        assert 'is_published' in compiled

    @pytest.mark.asyncio
    async def test_no_auth_required(self):
        """list_public takes no `cu` dependency at all -- must work for an
        anonymous/logged-out browse."""
        db = _mock_db(scalars_list=[_scenario(is_public=True, is_published=True)])

        result = await main.list_public(cursor=None, limit=20, db=db)
        assert len(result['items']) == 1
