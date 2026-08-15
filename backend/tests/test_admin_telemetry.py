"""Tests for auth.require_admin — the gate in front of /admin/telemetry
and any future admin-only endpoint."""
import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException
from auth import require_admin


class TestRequireAdmin:
    @pytest.mark.asyncio
    async def test_admin_user_passes_through(self):
        admin = MagicMock(is_admin=True)
        result = await require_admin(user=admin)
        assert result is admin

    @pytest.mark.asyncio
    async def test_non_admin_user_gets_403_not_404(self):
        """403, not 404 -- deliberately not hiding that the endpoint
        exists, only its data (see auth.py's docstring)."""
        regular_user = MagicMock(is_admin=False)
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(user=regular_user)
        assert exc_info.value.status_code == 403
