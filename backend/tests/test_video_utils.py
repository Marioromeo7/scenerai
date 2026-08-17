"""Tests for video_utils.concat_videos -- the shared ffmpeg concat-demuxer
helper used by worker.py's session-movie extension (via the worker._concat_videos
alias, see test_session_movie.py's TestExtendSessionMovie) and main.py's
/sessions/{id}/export (see test_regenerate_media.py's TestExportSessionVideo).
This file is the canonical place testing the actual subprocess/tempfile/
atomic-replace mechanics, since both callers now share one implementation."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import video_utils


class TestConcatVideos:
    @pytest.mark.asyncio
    async def test_runs_ffmpeg_and_replaces_output_atomically(self):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b'', b''))
        proc.returncode = 0

        with patch('video_utils.asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)) as mock_exec, \
             patch('video_utils.tempfile.mkstemp', return_value=(99, '/app/media/sess-1/tmp123.txt')), \
             patch('video_utils.os.close'), \
             patch('builtins.open', mock_open()), \
             patch('video_utils.os.path.exists', return_value=True), \
             patch('video_utils.os.remove') as mock_remove, \
             patch('video_utils.os.replace') as mock_replace:
            await video_utils.concat_videos(['/app/media/sess-1/1.mp4'], '/app/media/sess-1/movie.mp4')

        mock_exec.assert_awaited_once()
        mock_replace.assert_called_once_with('/app/media/sess-1/movie.mp4.tmp', '/app/media/sess-1/movie.mp4')
        mock_remove.assert_any_call('/app/media/sess-1/tmp123.txt')

    @pytest.mark.asyncio
    async def test_raises_and_cleans_up_on_ffmpeg_failure(self):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b'', b'boom'))
        proc.returncode = 1

        with patch('video_utils.asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)), \
             patch('video_utils.tempfile.mkstemp', return_value=(99, '/app/media/sess-1/tmp123.txt')), \
             patch('video_utils.os.close'), \
             patch('builtins.open', mock_open()), \
             patch('video_utils.os.path.exists', return_value=True), \
             patch('video_utils.os.remove') as mock_remove, \
             patch('video_utils.os.replace') as mock_replace:
            with pytest.raises(RuntimeError):
                await video_utils.concat_videos(['/app/media/sess-1/1.mp4'], '/app/media/sess-1/movie.mp4')

        mock_replace.assert_not_called()
        mock_remove.assert_any_call('/app/media/sess-1/tmp123.txt')
