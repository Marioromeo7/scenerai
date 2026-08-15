"""
ffmpeg image+audio stitching — Section 4.3 of NEXT_PHASE_PLAN.md. The
"primitive video": one still image with a slow pan/zoom, voiced narration,
duration driven by the audio track. No ML dependency here at all — pure
ffmpeg subprocess, CPU only, doesn't touch the GPU or any model.

Only one image (not a multi-frame sequence — that's 4.4, explicitly parked
until 4.3 ships and is validated), so there's no frame-to-frame drift to
manage — a real consistency advantage over real video generation, per the
plan's own reasoning for choosing this path.
"""
import subprocess
import shutil


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def stitch_image_audio(image_path: str, audio_path: str, out_path: str,
                        width: int = 1920, height: int = 1080,
                        zoom_rate: float = 0.0005, max_zoom: float = 1.1) -> str:
    """zoompan for the slow pan/zoom, -shortest ties video length to the
    (typically shorter) audio track's real duration rather than a fixed
    guess. Raises subprocess.CalledProcessError on failure — caller decides
    whether that's fatal or retryable for this scenario's generation job."""
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH")

    filter_complex = (
        f"[0:v]scale={width}:{height},"
        f"zoompan=z='min(zoom+{zoom_rate},{max_zoom})':d=125:s={width}x{height}[v]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "1:a",
        "-shortest",
        "-c:v", "libx264", "-c:a", "aac",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_path
