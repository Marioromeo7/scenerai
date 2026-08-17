"""Tests for the always-current session movie feature: worker.py's
_extend_session_movie/_concat_videos and main.py's GET .../movie endpoint.
Same direct-call, mocked-dependency style as test_regenerate_media.py."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from fastapi import HTTPException


def _mock_db(scalars_list=None, scalar_result=None):
    db = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    if scalars_list is not None:
        exec_result.scalars.return_value.all.return_value = scalars_list
    db.execute = AsyncMock(return_value=exec_result)
    return db


def _mock_redis(lock_acquired=True):
    redis = MagicMock()
    redis.set = AsyncMock(return_value=lock_acquired)
    redis.delete = AsyncMock()
    return redis


def _open_side_effect_no_state_file():
    """open() side_effect: read attempts (no explicit 'w' mode) raise
    FileNotFoundError (simulating no movie_state.json yet), writes succeed
    via a real mock_open handle."""
    writer = mock_open()

    def _side_effect(path, mode='r', *args, **kwargs):
        if 'w' not in mode:
            raise FileNotFoundError()
        return writer(path, mode, *args, **kwargs)
    return _side_effect


class TestExtendSessionMovie:
    @pytest.mark.asyncio
    async def test_skips_when_lock_not_acquired(self):
        import worker
        redis = _mock_redis(lock_acquired=False)
        with patch('worker.get_redis', new=AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._concat_videos', new=AsyncMock()) as mock_concat:
            await worker._extend_session_movie('sess-1')
        session_factory.assert_not_called()
        mock_concat.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_no_ready_turns(self):
        import worker
        redis = _mock_redis()
        db = _mock_db(scalars_list=[])
        with patch('worker.get_redis', new=AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._concat_videos', new=AsyncMock()) as mock_concat:
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker._extend_session_movie('sess-1')
        mock_concat.assert_not_called()
        redis.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_first_segment_builds_from_scratch(self):
        import worker
        redis = _mock_redis()
        db = _mock_db(scalars_list=[1])
        with patch('worker.get_redis', new=AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._concat_videos', new=AsyncMock()) as mock_concat, \
             patch('builtins.open', side_effect=_open_side_effect_no_state_file()), \
             patch('worker.json.dump') as mock_dump:
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker._extend_session_movie('sess-1')
        mock_concat.assert_awaited_once_with(
            ['/app/media/sess-1/1.mp4'], '/app/media/sess-1/movie.mp4'
        )
        mock_dump.assert_called_once()
        assert mock_dump.call_args.args[0] == {"turns": [1]}

    @pytest.mark.asyncio
    async def test_extension_appends_only_new_turns_one_at_a_time(self):
        import worker
        redis = _mock_redis()
        db = _mock_db(scalars_list=[1, 2, 3])
        state_json = json.dumps({"turns": [1]})
        with patch('worker.get_redis', new=AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._concat_videos', new=AsyncMock()) as mock_concat, \
             patch('builtins.open', mock_open(read_data=state_json)):
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker._extend_session_movie('sess-1')

        assert mock_concat.await_count == 2
        mock_concat.assert_any_await(
            ['/app/media/sess-1/movie.mp4', '/app/media/sess-1/2.mp4'], '/app/media/sess-1/movie.mp4'
        )
        mock_concat.assert_any_await(
            ['/app/media/sess-1/movie.mp4', '/app/media/sess-1/3.mp4'], '/app/media/sess-1/movie.mp4'
        )

    @pytest.mark.asyncio
    async def test_non_extension_triggers_full_rebuild(self):
        """A regenerate on turn 2 (already baked into the movie) means the
        stored state [1,2,3] no longer matches reality -- must rebuild,
        not append."""
        import worker
        redis = _mock_redis()
        db = _mock_db(scalars_list=[1, 2, 3])
        with patch('worker.get_redis', new=AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._concat_videos', new=AsyncMock()) as mock_concat, \
             patch('builtins.open', mock_open(read_data=json.dumps({"turns": [1, 5]}))):
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker._extend_session_movie('sess-1')

        mock_concat.assert_awaited_once_with(
            ['/app/media/sess-1/1.mp4', '/app/media/sess-1/2.mp4', '/app/media/sess-1/3.mp4'],
            '/app/media/sess-1/movie.mp4',
        )

    @pytest.mark.asyncio
    async def test_already_up_to_date_is_a_noop(self):
        import worker
        redis = _mock_redis()
        db = _mock_db(scalars_list=[1, 2])
        with patch('worker.get_redis', new=AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._concat_videos', new=AsyncMock()) as mock_concat, \
             patch('builtins.open', mock_open(read_data=json.dumps({"turns": [1, 2]}))):
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker._extend_session_movie('sess-1')
        mock_concat.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_always_released_even_on_error(self):
        import worker
        redis = _mock_redis()
        with patch('worker.get_redis', new=AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal', side_effect=RuntimeError('db down')):
            await worker._extend_session_movie('sess-1')  # must not raise
        redis.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gap_in_ready_turns_stops_at_the_gap(self):
        """Turn 3 still pending/failed -- movie can extend through turn 2
        but must not skip ahead to turn 4."""
        import worker
        redis = _mock_redis()
        db = _mock_db(scalars_list=[1, 2, 4])
        with patch('worker.get_redis', new=AsyncMock(return_value=redis)), \
             patch('worker.AsyncSessionLocal') as session_factory, \
             patch('worker._concat_videos', new=AsyncMock()) as mock_concat, \
             patch('builtins.open', side_effect=_open_side_effect_no_state_file()):
            session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await worker._extend_session_movie('sess-1')
        mock_concat.assert_awaited_once_with(
            ['/app/media/sess-1/1.mp4', '/app/media/sess-1/2.mp4'], '/app/media/sess-1/movie.mp4'
        )


class TestConcatVideos:
    @pytest.mark.asyncio
    async def test_runs_ffmpeg_and_replaces_output_atomically(self):
        import worker
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b'', b''))
        proc.returncode = 0

        with patch('worker.asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)) as mock_exec, \
             patch('tempfile.mkstemp', return_value=(99, '/app/media/sess-1/tmp123.txt')), \
             patch('worker.os.close'), \
             patch('builtins.open', mock_open()), \
             patch('worker.os.path.exists', return_value=True), \
             patch('worker.os.remove') as mock_remove, \
             patch('worker.os.replace') as mock_replace:
            await worker._concat_videos(['/app/media/sess-1/1.mp4'], '/app/media/sess-1/movie.mp4')

        mock_exec.assert_awaited_once()
        mock_replace.assert_called_once_with('/app/media/sess-1/movie.mp4.tmp', '/app/media/sess-1/movie.mp4')
        mock_remove.assert_any_call('/app/media/sess-1/tmp123.txt')

    @pytest.mark.asyncio
    async def test_raises_and_cleans_up_on_ffmpeg_failure(self):
        import worker
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b'', b'boom'))
        proc.returncode = 1

        with patch('worker.asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)), \
             patch('tempfile.mkstemp', return_value=(99, '/app/media/sess-1/tmp123.txt')), \
             patch('worker.os.close'), \
             patch('builtins.open', mock_open()), \
             patch('worker.os.path.exists', return_value=True), \
             patch('worker.os.remove') as mock_remove, \
             patch('worker.os.replace') as mock_replace:
            with pytest.raises(RuntimeError):
                await worker._concat_videos(['/app/media/sess-1/1.mp4'], '/app/media/sess-1/movie.mp4')

        mock_replace.assert_not_called()
        mock_remove.assert_any_call('/app/media/sess-1/tmp123.txt')


class TestGetSessionMovie:
    @pytest.mark.asyncio
    async def test_404_when_session_not_owned_or_unknown(self):
        import main
        db = _mock_db(scalar_result=None)
        user = MagicMock(id='u1')
        with pytest.raises(HTTPException) as exc_info:
            await main.get_session_movie('sess-1', db=db, cu=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_not_available_when_file_missing(self):
        import main
        db = _mock_db(scalar_result='row-id')
        user = MagicMock(id='u1')
        with patch('main.os.path.exists', return_value=False):
            result = await main.get_session_movie('sess-1', db=db, cu=user)
        assert result.available is False
        assert result.url is None

    @pytest.mark.asyncio
    async def test_available_returns_media_url(self):
        import main
        db = _mock_db(scalar_result='row-id')
        user = MagicMock(id='u1')
        with patch('main.os.path.exists', return_value=True):
            result = await main.get_session_movie('sess-1', db=db, cu=user)
        assert result.available is True
        assert result.url == '/media/sess-1/movie.mp4'
