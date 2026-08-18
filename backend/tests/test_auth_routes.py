"""Tests for main.py's auth routes (register/login/change-password/delete-
account) -- had zero coverage before this (found live via code review: no
route in main.py had direct tests). This is the highest-consequence
remaining gap, security-adjacent by nature. Same direct-call, mocked-
dependency style as test_regenerate_media.py/test_owned_scenario.py -- no
TestClient/DB fixture exists in this suite (see conftest.py).

register/login/change-password/delete-account all carry a slowapi
@limiter.limit(...) decorator, which raises if `request` isn't a real
starlette.requests.Request -- confirmed live. Calling the route's own
__wrapped__ (slowapi preserves it via functools.wraps) tests the route's
actual logic directly without needing a real ASGI request or exercising
slowapi's own already-separately-tested rate-limiting behavior."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import main
from schemas import UserCreate, UserLogin, PasswordChange


def _mock_db(scalar_result=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    db.execute = AsyncMock(return_value=exec_result)
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _user(id='u1', email='test@example.com', hashed_password='hashed', is_admin=False):
    return MagicMock(
        id=id, email=email, hashed_password=hashed_password,
        created_at=datetime.now(timezone.utc), is_admin=is_admin,
    )


class TestRegister:
    @pytest.mark.asyncio
    async def test_409_when_email_already_registered(self):
        db = _mock_db(scalar_result=_user())
        request = MagicMock()
        body = UserCreate(email='test@example.com', password='password123')

        with pytest.raises(HTTPException) as exc_info:
            await main.register.__wrapped__(request, body, db=db)
        assert exc_info.value.status_code == 409
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_user_and_returns_token_when_email_available(self):
        db = _mock_db(scalar_result=None)
        # register() constructs a real User(...) ORM instance and relies on
        # db.refresh() to populate server_default columns (created_at,
        # is_admin) before UserOut.model_validate() reads them -- a no-op
        # mock refresh leaves them None, matching a real UserOut ValidationError
        # this route would never actually hit against a real DB.
        async def fake_refresh(user):
            user.created_at = datetime.now(timezone.utc)
            user.is_admin = False
        db.refresh = AsyncMock(side_effect=fake_refresh)
        request = MagicMock()
        body = UserCreate(email='new@example.com', password='password123')

        with patch('main.hash_password', return_value='hashed-pw') as mock_hash, \
             patch('main.create_token', return_value='access-tok') as mock_token, \
             patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main.create_refresh_token', new=AsyncMock(return_value='refresh-tok')) as mock_refresh:
            result = await main.register.__wrapped__(request, body, db=db)

        mock_hash.assert_called_once_with('password123')
        added = db.add.call_args[0][0]
        assert added.email == 'new@example.com'
        assert added.hashed_password == 'hashed-pw'
        db.commit.assert_awaited_once()
        mock_refresh.assert_awaited_once()
        assert result.access_token == 'access-tok'
        assert result.refresh_token == 'refresh-tok'
        mock_token.assert_called_once_with(added.id)


class TestLogin:
    @pytest.mark.asyncio
    async def test_401_when_email_not_found(self):
        db = _mock_db(scalar_result=None)
        request = MagicMock()
        body = UserLogin(email='ghost@example.com', password='whatever123')

        with patch('main.verify_password', return_value=False) as mock_verify:
            with pytest.raises(HTTPException) as exc_info:
                await main.login.__wrapped__(request, body, db=db)
        assert exc_info.value.status_code == 401
        mock_verify.assert_called_once()

    @pytest.mark.asyncio
    async def test_regression_timing_safe_verify_runs_even_when_email_not_found(self):
        """Before the fix, `not u or not verify_password(...)` short-
        circuited on `not u` and never called verify_password at all when
        the email didn't exist -- skipping bcrypt (~236ms measured) made
        that path return far faster than a wrong-password attempt against a
        real account, letting an attacker enumerate registered emails by
        timing the (identically-worded) 401 response alone. verify_password
        must now always run, against the dummy hash when there's no user."""
        db = _mock_db(scalar_result=None)
        request = MagicMock()
        body = UserLogin(email='ghost@example.com', password='whatever123')

        with patch('main.verify_password', return_value=False) as mock_verify:
            with pytest.raises(HTTPException):
                await main.login.__wrapped__(request, body, db=db)

        mock_verify.assert_called_once_with('whatever123', main._DUMMY_PASSWORD_HASH)

    @pytest.mark.asyncio
    async def test_401_when_password_incorrect(self):
        db = _mock_db(scalar_result=_user())
        request = MagicMock()
        body = UserLogin(email='test@example.com', password='wrongpassword')

        with patch('main.verify_password', return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                await main.login.__wrapped__(request, body, db=db)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_token_when_credentials_valid(self):
        user = _user()
        db = _mock_db(scalar_result=user)
        request = MagicMock()
        body = UserLogin(email='test@example.com', password='correctpassword')

        with patch('main.verify_password', return_value=True), \
             patch('main.create_token', return_value='access-tok'), \
             patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main.create_refresh_token', new=AsyncMock(return_value='refresh-tok')) as mock_refresh:
            result = await main.login.__wrapped__(request, body, db=db)

        assert result.access_token == 'access-tok'
        assert result.refresh_token == 'refresh-tok'
        mock_refresh.assert_awaited_once()
        assert mock_refresh.call_args.args[1] == user.id


class TestChangePassword:
    @pytest.mark.asyncio
    async def test_401_when_current_password_incorrect(self):
        user = _user()
        db = _mock_db()
        request = MagicMock()
        body = PasswordChange(current_password='wrong', new_password='newpassword123')

        with patch('main.verify_password', return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                await main.change_password.__wrapped__(request, body, db=db, cu=user)
        assert exc_info.value.status_code == 401
        db.commit.assert_not_awaited()
        # the current user's hash must be untouched on a rejected attempt
        assert user.hashed_password == 'hashed'

    @pytest.mark.asyncio
    async def test_updates_hashed_password_and_commits_when_correct(self):
        user = _user()
        db = _mock_db()
        request = MagicMock()
        body = PasswordChange(current_password='correct', new_password='newpassword123')

        with patch('main.verify_password', return_value=True), \
             patch('main.hash_password', return_value='new-hashed-pw'):
            await main.change_password.__wrapped__(request, body, db=db, cu=user)

        assert user.hashed_password == 'new-hashed-pw'
        db.commit.assert_awaited_once()


class TestDeleteAccount:
    @pytest.mark.asyncio
    async def test_deletes_the_requesting_users_own_account(self):
        user = _user()
        db = _mock_db()
        request = MagicMock()

        await main.delete_account.__wrapped__(request, db=db, cu=user)

        db.delete.assert_awaited_once_with(user)
        db.commit.assert_awaited_once()


class TestRefresh:
    @pytest.mark.asyncio
    async def test_401_when_refresh_token_invalid_or_expired(self):
        db = _mock_db()
        request = MagicMock()
        from schemas import RefreshRequest
        body = RefreshRequest(refresh_token='bad-token')

        with patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main.rotate_refresh_token', new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await main.refresh.__wrapped__(request, body, db=db)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_401_when_rotated_token_points_to_a_deleted_user(self):
        """The refresh token was valid (rotate succeeded), but the user it
        points to no longer exists (account deleted) -- must not mint a
        token for a nonexistent user."""
        db = _mock_db(scalar_result=None)
        request = MagicMock()
        from schemas import RefreshRequest
        body = RefreshRequest(refresh_token='old-token')

        with patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main.rotate_refresh_token', new=AsyncMock(return_value=('deleted-user-id', 'new-token'))):
            with pytest.raises(HTTPException) as exc_info:
                await main.refresh.__wrapped__(request, body, db=db)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_new_access_token_and_rotated_refresh_token(self):
        user = _user(id='u1')
        db = _mock_db(scalar_result=user)
        request = MagicMock()
        from schemas import RefreshRequest
        body = RefreshRequest(refresh_token='old-token')

        with patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main.rotate_refresh_token', new=AsyncMock(return_value=('u1', 'brand-new-refresh-token'))), \
             patch('main.create_token', return_value='brand-new-access-token'):
            result = await main.refresh.__wrapped__(request, body, db=db)

        assert result.access_token == 'brand-new-access-token'
        assert result.refresh_token == 'brand-new-refresh-token'


class TestLogout:
    @pytest.mark.asyncio
    async def test_revokes_the_refresh_token(self):
        from schemas import LogoutRequest
        body = LogoutRequest(refresh_token='some-token')

        with patch('main.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('main.revoke_refresh_token', new=AsyncMock()) as mock_revoke:
            await main.logout(body)

        mock_revoke.assert_awaited_once()
        assert mock_revoke.await_args.args[1] == 'some-token'


class TestMe:
    @pytest.mark.asyncio
    async def test_returns_the_requesting_user(self):
        user = _user(id='u1', email='me@example.com')
        result = await main.me(cu=user)
        assert result.id == 'u1'
        assert result.email == 'me@example.com'
