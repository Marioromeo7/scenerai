"""Shared ffmpeg concat-demuxer helper -- used by worker.py's session-movie
extension (worker._extend_session_movie) and main.py's ad-hoc turn-range
export (/sessions/{id}/export). Was previously duplicated between the two
with the export copy missing the atomic-replace safety below; found via
code review and consolidated here so a future encoding-flag change only
needs to happen in one place.
"""
import asyncio
import os
import tempfile


async def concat_videos(input_paths: list[str], output_path: str):
    """Re-encodes rather than stream-copies -- confirmed live that
    stream-copy across independently-stitched segments produces
    "Non-monotonic DTS" warnings at the splice point; re-encoding (fast
    preset, short clips) avoids that for a small time cost.

    Always writes to a temp file and atomically replaces output_path --
    required (not just nice-to-have) when output_path is also one of the
    inputs, as it is for worker.py's session-movie append case: ffmpeg
    can't safely read and write the same file at once, and a crash
    mid-encode must never leave a truncated file visible to something
    already serving it."""
    session_dir = os.path.dirname(output_path)
    fd, filelist_path = tempfile.mkstemp(suffix=".txt", dir=session_dir)
    os.close(fd)
    tmp_output = f"{output_path}.tmp"
    try:
        with open(filelist_path, "w", encoding="utf-8") as f:
            for p in input_paths:
                f.write(f"file '{p}'\n")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", filelist_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart",
            # Explicit output format -- tmp_output's extension is ".tmp", not
            # ".mp4" (renamed after success), so ffmpeg can't infer the muxer
            # from the filename. Found live: without this, ffmpeg fails with
            # "Unable to choose an output format".
            "-f", "mp4", tmp_output,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {stderr.decode(errors='replace')[-500:]}")
        os.replace(tmp_output, output_path)
    finally:
        if os.path.exists(filelist_path):
            os.remove(filelist_path)
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
