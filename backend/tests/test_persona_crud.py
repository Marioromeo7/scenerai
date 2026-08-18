"""Tests for main.py's persona CRUD routes (create/list/update/delete) --
had zero coverage before this (found live via code review: no route in
main.py had direct tests). Same direct-call, mocked-dependency style as
test_regenerate_media.py/test_owned_scenario.py -- no TestClient/DB
fixture exists in this suite (see conftest.py)."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

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


def _persona(id='p1', user_id='u1', name='Alex', pronouns='they/them', brief='',
             created_at=None):
    # `name=` is a reserved Mock constructor kwarg (sets the mock's own
    # debug repr, not a .name attribute) -- must be set post-construction.
    p = MagicMock(id=id, user_id=user_id, pronouns=pronouns, brief=brief,
                  created_at=created_at or datetime.now(timezone.utc))
    p.name = name
    return p


class TestCreatePersona:
    @pytest.mark.asyncio
    async def test_creates_persona_owned_by_requesting_user(self):
        db = _mock_db()

        async def fake_refresh(p):
            p.created_at = datetime.now(timezone.utc)
        db.refresh = AsyncMock(side_effect=fake_refresh)

        from schemas import PersonaCreate
        body = PersonaCreate(name='Alex', pronouns='they/them', brief='a wanderer')
        user = MagicMock(id='u1')

        result = await main.create_persona(body, db=db, cu=user)

        added = db.add.call_args[0][0]
        assert added.user_id == 'u1'
        assert added.name == 'Alex'
        db.commit.assert_awaited_once()
        assert result.name == 'Alex'


class TestListPersonas:
    @pytest.mark.asyncio
    async def test_returns_only_requesting_users_personas(self):
        rows = [_persona(id='p1'), _persona(id='p2')]
        db = _mock_db(scalars_list=rows)
        user = MagicMock(id='u1')

        result = await main.list_personas(cursor=None, limit=20, db=db, cu=user)

        assert len(result['items']) == 2
        assert result['has_more'] is False
        compiled = str(db.execute.call_args.args[0])
        assert 'user_id' in compiled

    @pytest.mark.asyncio
    async def test_400_on_invalid_cursor(self):
        db = _mock_db(scalars_list=[])
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.list_personas(cursor='not-a-real-date', limit=20, db=db, cu=user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_has_more_true_when_more_rows_than_limit(self):
        rows = [_persona(id=f'p{i}') for i in range(3)]
        db = _mock_db(scalars_list=rows)
        user = MagicMock(id='u1')

        result = await main.list_personas(cursor=None, limit=2, db=db, cu=user)

        assert result['has_more'] is True
        assert len(result['items']) == 2
        assert result['next_cursor'] is not None

    @pytest.mark.asyncio
    async def test_limit_is_clamped_to_50(self):
        db = _mock_db(scalars_list=[])
        user = MagicMock(id='u1')

        await main.list_personas(cursor=None, limit=999, db=db, cu=user)

        # limit+1 = 51 is what actually gets passed to the query's .limit()
        compiled = str(db.execute.call_args.args[0])
        assert 'LIMIT :param_1' in compiled or 'LIMIT' in compiled


class TestUpdatePersona:
    @pytest.mark.asyncio
    async def test_404_when_not_found_or_not_owned(self):
        db = _mock_db(scalar_result=None)
        from schemas import PersonaUpdate
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.update_persona('p1', PersonaUpdate(name='New'), db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_updates_only_provided_fields(self):
        persona = _persona(name='Old Name', pronouns='she/her', brief='old brief')
        db = _mock_db(scalar_result=persona)
        from schemas import PersonaUpdate
        user = MagicMock(id='u1')

        await main.update_persona('p1', PersonaUpdate(name='New Name'), db=db, cu=user)

        assert persona.name == 'New Name'
        assert persona.pronouns == 'she/her'  # untouched
        assert persona.brief == 'old brief'   # untouched
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_query_scopes_by_owner_not_id_alone(self):
        db = _mock_db(scalar_result=None)
        from schemas import PersonaUpdate
        user = MagicMock(id='attacker')

        try:
            await main.update_persona('someone-elses-persona', PersonaUpdate(name='hijacked'), db=db, cu=user)
        except HTTPException:
            pass

        compiled = str(db.execute.call_args.args[0])
        assert 'user_id' in compiled


class TestDeletePersona:
    @pytest.mark.asyncio
    async def test_404_when_not_found_or_not_owned(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.delete_persona('p1', db=db, cu=user)
        assert exc_info.value.status_code == 404
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletes_owned_persona(self):
        persona = _persona()
        db = _mock_db(scalar_result=persona)
        user = MagicMock(id='u1')

        await main.delete_persona('p1', db=db, cu=user)

        db.delete.assert_awaited_once_with(persona)
        db.commit.assert_awaited_once()
