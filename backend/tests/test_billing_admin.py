"""Tests for main.py's billing (mock), admin telemetry, and /models routes
-- had zero coverage before this (found live via code review: no route in
main.py had direct tests). Same direct-call, mocked-dependency style as
test_regenerate_media.py/test_owned_scenario.py -- no TestClient/DB
fixture exists in this suite (see conftest.py)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import main


def _mock_db(scalar_result=None, scalars_list=None, all_rows=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    exec_result.scalar_one.return_value = scalar_result
    if scalars_list is not None:
        exec_result.scalars.return_value.all.return_value = scalars_list
    if all_rows is not None:
        exec_result.all.return_value = all_rows
    db.execute = AsyncMock(return_value=exec_result)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


class TestListEngineModels:
    @pytest.mark.asyncio
    async def test_returns_id_label_pairs(self):
        result = await main.list_engine_models()
        assert len(result) > 0
        assert all('id' in m and 'label' in m for m in result)


class TestBillingGating:
    @pytest.mark.asyncio
    async def test_list_tiers_404_when_mock_billing_disabled(self):
        db = _mock_db()
        with patch('main.settings') as mock_settings:
            mock_settings.mock_billing_enabled = False
            with pytest.raises(HTTPException) as exc_info:
                await main.list_tiers(db=db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_tiers_returns_rows_when_enabled(self):
        rows = [MagicMock(id='t1'), MagicMock(id='t2')]
        db = _mock_db(scalars_list=rows)
        with patch('main.settings') as mock_settings:
            mock_settings.mock_billing_enabled = True
            result = await main.list_tiers(db=db)
        assert result == rows

    @pytest.mark.asyncio
    async def test_get_my_subscription_404_when_mock_billing_disabled(self):
        db = _mock_db()
        user = MagicMock(id='u1')
        with patch('main.settings') as mock_settings:
            mock_settings.mock_billing_enabled = False
            with pytest.raises(HTTPException) as exc_info:
                await main.get_my_subscription(db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_mock_subscribe_404_when_disabled(self):
        db = _mock_db()
        user = MagicMock(id='u1')
        with patch('main.settings') as mock_settings:
            mock_settings.mock_billing_enabled = False
            with pytest.raises(HTTPException) as exc_info:
                await main.mock_subscribe('tier-1', db=db, cu=user)
        assert exc_info.value.status_code == 404


class TestMockSubscribe:
    @pytest.mark.asyncio
    async def test_404_for_unknown_tier(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')
        with patch('main.settings') as mock_settings:
            mock_settings.mock_billing_enabled = True
            with pytest.raises(HTTPException) as exc_info:
                await main.mock_subscribe('unknown-tier', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_creates_new_subscription_marked_as_mock(self):
        tier = MagicMock(id='tier-1')
        db = MagicMock()
        exec_result_tier = MagicMock()
        exec_result_tier.scalar_one_or_none.return_value = tier
        exec_result_existing = MagicMock()
        exec_result_existing.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(side_effect=[exec_result_tier, exec_result_existing])
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        user = MagicMock(id='u1')

        with patch('main.settings') as mock_settings:
            mock_settings.mock_billing_enabled = True
            await main.mock_subscribe('tier-1', db=db, cu=user)

        added = db.add.call_args[0][0]
        assert added.user_id == 'u1'
        assert added.tier_id == 'tier-1'
        assert added.is_mock is True

    @pytest.mark.asyncio
    async def test_re_subscribing_updates_existing_row_not_a_new_insert(self):
        """Upsert -- re-subscribing (changing tiers) must not error on the
        UserSubscription.user_id unique constraint."""
        tier = MagicMock(id='tier-2')
        existing = MagicMock(tier_id='tier-1', status='cancelled')
        db = MagicMock()
        exec_result_tier = MagicMock()
        exec_result_tier.scalar_one_or_none.return_value = tier
        exec_result_existing = MagicMock()
        exec_result_existing.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(side_effect=[exec_result_tier, exec_result_existing])
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        user = MagicMock(id='u1')

        with patch('main.settings') as mock_settings:
            mock_settings.mock_billing_enabled = True
            result = await main.mock_subscribe('tier-2', db=db, cu=user)

        db.add.assert_not_called()
        assert existing.tier_id == 'tier-2'
        assert existing.status == 'active'
        assert result is existing


class TestGetTelemetry:
    @pytest.mark.asyncio
    async def test_reports_current_tpm_limit_and_counts(self):
        db = _mock_db()
        # total_scenarios (scalar_one), total_plays (scalar_one), media_rows (.all())
        exec_scenarios = MagicMock(); exec_scenarios.scalar_one.return_value = 5
        exec_plays = MagicMock(); exec_plays.scalar_one.return_value = 42
        exec_media = MagicMock(); exec_media.all.return_value = [('ready', 3), ('pending', 1)]
        db.execute = AsyncMock(side_effect=[exec_scenarios, exec_plays, exec_media])

        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=[b'1234', b'1'])  # tpm_used, prefab_running

        async def fake_scan_iter(match=None, count=None):
            for key in ['session:a', 'session:b']:
                yield key
        redis.scan_iter = fake_scan_iter

        admin = MagicMock(is_admin=True)

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            result = await main.get_telemetry(db=db, _admin=admin)

        assert result.groq_tpm_used == 1234
        assert result.groq_tpm_limit == 8000  # rate_limiter.TPM_LIMIT, re-measured live
        assert result.active_sessions == 2
        assert result.prefab_jobs_running == 1
        assert result.total_scenarios == 5
        assert result.total_plays == 42
        assert result.turn_media_by_status == {'ready': 3, 'pending': 1}
