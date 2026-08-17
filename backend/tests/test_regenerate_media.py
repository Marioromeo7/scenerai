"""Tests for the regenerate-media feature: main.py's list/regenerate
endpoints and worker.py's regenerate_turn_media_job / shared pipeline
helper. Same direct-call, mocked-dependency style as test_admin_telemetry.py
-- no TestClient/DB fixture exists in this suite (see conftest.py)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException


def _mock_db(scalar_result=None, scalars_list=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    if scalars_list is not None:
        exec_result.scalars.return_value.all.return_value = scalars_list
    db.execute = AsyncMock(return_value=exec_result)
    db.commit = AsyncMock()
    return db


class TestListTurnMedia:
    @pytest.mark.asyncio
    async def test_returns_rows_for_the_requesting_user(self):
        import main
        rows = [MagicMock(turn=1), MagicMock(turn=2)]
        db = _mock_db(scalars_list=rows)
        user = MagicMock(id='u1')

        result = await main.list_turn_media('sess-1', db=db, cu=user)

        assert result == rows
        db.execute.assert_awaited_once()


class TestRegenerateTurnMediaEndpoint:
    @pytest.mark.asyncio
    async def test_404_when_no_prior_media_row(self):
        import main
        db = _mock_db(scalar_result=None)
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.regenerate_turn_media('sess-1', 3, request, db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_row_exists_but_never_had_a_prompt(self):
        """A TurnMedia row that failed before the prompt/narration/voice
        fields were ever saved has nothing to regenerate from."""
        import main
        row = MagicMock(image_prompt=None)
        db = _mock_db(scalar_result=row)
        request = MagicMock()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main.regenerate_turn_media('sess-1', 3, request, db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_resets_status_and_enqueues_regenerate_job(self):
        import main
        row = MagicMock(image_prompt='a prompt', status='ready', error='old error')
        db = _mock_db(scalar_result=row)
        request = MagicMock()
        request.app.state.arq_pool.enqueue_job = AsyncMock()
        user = MagicMock(id='u1')

        result = await main.regenerate_turn_media('sess-1', 3, request, db=db, cu=user)

        assert row.status == 'pending'
        assert row.error is None
        db.commit.assert_awaited_once()
        request.app.state.arq_pool.enqueue_job.assert_awaited_once_with(
            'regenerate_turn_media_job', 'sess-1', 3
        )
        assert result is row


class TestExportSessionVideo:
    def _rows(self, turns):
        return [MagicMock(turn=t) for t in turns]

    @pytest.mark.asyncio
    async def test_400_when_from_turn_after_to_turn(self):
        import main
        from schemas import ExportRequest
        db = _mock_db()
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._export_session_video('sess-1', ExportRequest(from_turn=5, to_turn=2), db, user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_404_when_no_ready_segments_in_range(self):
        import main
        from schemas import ExportRequest
        db = _mock_db(scalars_list=[])
        user = MagicMock(id='u1')

        with pytest.raises(HTTPException) as exc_info:
            await main._export_session_video('sess-1', ExportRequest(from_turn=1, to_turn=3), db, user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_runs_ffmpeg_concat_and_returns_download_url(self):
        # The actual ffmpeg subprocess/tempfile/atomic-replace mechanics
        # are video_utils.concat_videos's own responsibility now (shared
        # with worker.py's session-movie extension -- see
        # test_video_utils.py) -- this test only needs to confirm
        # _export_session_video calls it with the right paths and turns
        # a success into the right response shape.
        import main
        from schemas import ExportRequest
        db = _mock_db(scalars_list=self._rows([1, 2]))
        user = MagicMock(id='u1')

        with patch('main.concat_videos', new=AsyncMock()) as mock_concat:
            result = await main._export_session_video('sess-1', ExportRequest(from_turn=1, to_turn=2), db, user)

        assert result.download_url.startswith('/media/sess-1/export_')
        assert result.turns == [1, 2]
        mock_concat.assert_awaited_once()
        input_paths, output_path = mock_concat.await_args.args
        assert input_paths == ['/app/media/sess-1/1.mp4', '/app/media/sess-1/2.mp4']
        assert output_path.startswith('/app/media/sess-1/export_') and output_path.endswith('.mp4')

    @pytest.mark.asyncio
    async def test_500_when_ffmpeg_fails(self):
        import main
        from schemas import ExportRequest
        db = _mock_db(scalars_list=self._rows([1]))
        user = MagicMock(id='u1')

        with patch('main.concat_videos', new=AsyncMock(side_effect=RuntimeError('ffmpeg blew up'))):
            with pytest.raises(HTTPException) as exc_info:
                await main._export_session_video('sess-1', ExportRequest(from_turn=1, to_turn=1), db, user)
        assert exc_info.value.status_code == 500


class TestRegenerateTurnMediaJob:
    @pytest.mark.asyncio
    async def test_logs_and_returns_when_no_prior_row(self):
        import worker
        db = _mock_db(scalar_result=None)
        with patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._run_media_pipeline', new=AsyncMock()) as mock_pipeline:
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker.regenerate_turn_media_job(None, 'sess-1', 3)
        mock_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_reuses_stored_context_with_a_different_seed(self):
        import worker
        row = MagicMock(image_prompt='prompt', narration_text='narration',
                         voice_id='af_sarah', voice_speed=1.0, image_seed=42)
        db = _mock_db(scalar_result=row)
        with patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._run_media_pipeline', new=AsyncMock()) as mock_pipeline:
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker.regenerate_turn_media_job(None, 'sess-1', 3)

        assert row.status == 'pending'
        assert row.error is None
        mock_pipeline.assert_awaited_once_with('sess-1', 3, 'prompt', 'narration', 'af_sarah', 1.0, 43)

    @pytest.mark.asyncio
    async def test_falls_back_to_a_computed_seed_when_no_prior_seed_stored(self):
        import worker
        row = MagicMock(image_prompt='prompt', narration_text='narration',
                         voice_id='af_sarah', voice_speed=1.0, image_seed=None)
        db = _mock_db(scalar_result=row)
        with patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._run_media_pipeline', new=AsyncMock()) as mock_pipeline:
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker.regenerate_turn_media_job(None, 'sess-1', 3)

        mock_pipeline.assert_awaited_once()
        called_seed = mock_pipeline.await_args.args[-1]
        assert isinstance(called_seed, int) and called_seed >= 0
