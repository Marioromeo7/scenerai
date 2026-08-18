"""Tests for main.py's scenario publish/retry-prefab/unpublish/report
routes -- had zero coverage before this (found live via code review: no
route in main.py had direct tests). Same direct-call, mocked-dependency
style as test_regenerate_media.py/test_owned_scenario.py -- no
TestClient/DB fixture exists in this suite (see conftest.py).

retry_prefab/report_scenario carry a slowapi @limiter.limit(...) decorator,
which raises if `request` isn't a real starlette.requests.Request
(confirmed live in test_auth_routes.py) -- called via __wrapped__ here."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import main


def _mock_db(scalar_result=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    db.execute = AsyncMock(return_value=exec_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _scenario(id='scn-1', creator_id='u1', is_public=True, is_published=False,
              prefab_status='pending'):
    return MagicMock(
        id=id, creator_id=creator_id,
        char_name='Iris', char_pronouns='she/her', char_title='Archivist',
        char_personality='guarded', greeting='The archive is sealed.',
        title='The Sealed Archive', brief='A locked room.', tags=['tension'],
        intensity=3, image_seed='abcd1234', saves_count=0, plays_count=0,
        is_public=is_public, is_published=is_published,
        created_at=datetime.now(timezone.utc),
        prefab_engine_state=None, prefab_status=prefab_status,
        visual_profile=None, visual_seed=None, visual_status='pending',
        image_url=None, video_url=None,
    )


def _request_with_working_enqueue():
    request = MagicMock()
    request.app.state.arq_pool.enqueue_job = AsyncMock()
    return request


class TestPublishScenario:
    @pytest.mark.asyncio
    async def test_404_when_not_owned(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.publish_scenario('scn-1', MagicMock(), db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_400_when_already_published(self):
        s = _scenario(is_published=True)
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.publish_scenario('scn-1', MagicMock(), db=db, cu=user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_publishes_and_enqueues_prefab_job(self):
        s = _scenario(is_published=False)
        db = _mock_db(scalar_result=s)
        request = _request_with_working_enqueue()
        user = MagicMock(id='u1')

        result = await main.publish_scenario('scn-1', request, db=db, cu=user)

        assert s.is_published is True
        assert s.prefab_status == 'pending'
        request.app.state.arq_pool.enqueue_job.assert_awaited_once()
        job_args = request.app.state.arq_pool.enqueue_job.await_args.args
        assert job_args[0] == 'prefab_engine_job'
        assert job_args[1] == 'scn-1'
        assert result.id == 'scn-1'

    @pytest.mark.asyncio
    async def test_regression_enqueue_failure_marks_prefab_failed_not_stuck_pending(self):
        """_enqueue_prefab_job must not leave prefab_status stuck at
        'pending' with no job ever queued if the enqueue call itself blows
        up (e.g. Redis briefly unreachable) -- distinct from the job
        failing once it's running, which worker.py already handles."""
        s = _scenario(is_published=False)
        db = _mock_db(scalar_result=s)
        request = MagicMock()
        request.app.state.arq_pool.enqueue_job = AsyncMock(side_effect=RuntimeError('redis down'))
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.publish_scenario('scn-1', request, db=db, cu=user)

        assert exc_info.value.status_code == 503
        assert s.prefab_status == 'failed'


class TestRetryPrefab:
    @pytest.mark.asyncio
    async def test_404_when_not_owned(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.retry_prefab.__wrapped__('scn-1', MagicMock(), db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_400_when_not_published(self):
        s = _scenario(is_published=False)
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.retry_prefab.__wrapped__('scn-1', MagicMock(), db=db, cu=user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_400_when_prefab_already_ready(self):
        s = _scenario(is_published=True, prefab_status='ready')
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.retry_prefab.__wrapped__('scn-1', MagicMock(), db=db, cu=user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_re_enqueues_when_prefab_failed(self):
        s = _scenario(is_published=True, prefab_status='failed')
        db = _mock_db(scalar_result=s)
        request = _request_with_working_enqueue()
        user = MagicMock(id='u1')

        await main.retry_prefab.__wrapped__('scn-1', request, db=db, cu=user)

        assert s.prefab_status == 'pending'
        request.app.state.arq_pool.enqueue_job.assert_awaited_once()


class TestUnpublishScenario:
    @pytest.mark.asyncio
    async def test_404_when_not_owned(self):
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.unpublish_scenario('scn-1', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_400_when_not_published(self):
        s = _scenario(is_published=False)
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.unpublish_scenario('scn-1', db=db, cu=user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unpublishes_and_clears_prefab_state(self):
        s = _scenario(is_published=True, prefab_status='ready')
        s.prefab_engine_state = {'entities': ['stale']}
        db = _mock_db(scalar_result=s)
        user = MagicMock(id='u1')

        await main.unpublish_scenario('scn-1', db=db, cu=user)

        assert s.is_published is False
        assert s.prefab_engine_state is None
        assert s.prefab_status == 'pending'
        db.commit.assert_awaited_once()


class TestReportScenario:
    @pytest.mark.asyncio
    async def test_404_when_scenario_not_published(self):
        db = _mock_db(scalar_result=None)
        from schemas import ScenarioReport
        user = MagicMock(id='u1')

        with patch('main.get_redis', new=AsyncMock()):
            with pytest.raises(HTTPException) as exc_info:
                await main.report_scenario.__wrapped__(
                    MagicMock(), 'scn-1', ScenarioReport(reason='spam'), db=db, cu=user,
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_pushes_report_to_redis_and_trims_list(self):
        s = _scenario(is_published=True)
        db = _mock_db(scalar_result=s)
        from schemas import ScenarioReport
        user = MagicMock(id='u1')
        redis = AsyncMock()

        with patch('main.get_redis', new=AsyncMock(return_value=redis)):
            result = await main.report_scenario.__wrapped__(
                MagicMock(), 'scn-1', ScenarioReport(reason='inappropriate content'), db=db, cu=user,
            )

        assert result == {"reported": True}
        redis.lpush.assert_awaited_once()
        list_key, payload = redis.lpush.await_args.args
        assert list_key == 'scenario_reports'
        assert 'inappropriate content' in payload
        redis.ltrim.assert_awaited_once_with('scenario_reports', 0, 9999)
