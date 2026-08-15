"""
Local image-generation bridge — runs natively on this machine (NOT in
Docker), so it keeps direct GPU access without needing GPU passthrough
into Docker Desktop, which this project isn't set up for. The Dockerized
backend/worker call this over HTTP (host.docker.internal:PORT from inside
a container) instead of embedding torch/diffusers/CUDA into their own
images, which would add several GB and a GPU-container dependency the
project doesn't have for a lightweight API service.

Deliberately sequential, not concurrent: only one GPU exists on this
machine, so a global lock serializes every /generate call. That means
concurrent users see latency under load instead of a crash or a corrupted
generation — the tradeoff explicitly chosen over the alternative (parallel
GPU access, which doesn't exist here) or refusing requests outright.

SD1.5 is loaded ONCE at startup and kept resident for the process
lifetime, not per-request -- avoids paying the ~7s pipeline-load cost on
every single turn. Inpainting/face-detection (sd_inpaint.py,
face_detect.py) aren't wired in here yet -- this is the simplest viable
version (plain generate, new seed on regenerate), matching the same
"ship the working version first" sequencing as everything else this
session. Upgrading /regenerate to use inpainting instead of a full reroll
is a real follow-up, not forgotten -- it's what got hair/cloak to 85-95%
in the research track -- just not required for the first working version.

Also handles narration (Kokoro, CPU) and stitching (ffmpeg, CPU) for the
same reason -- keeping the whole local media pipeline in one native
process avoids adding ffmpeg/kokoro-onnx as dependencies to the Docker
worker image (a real but avoidable build-time/image-size cost), and
avoids a second native process to manage.

Usage:
    .venv/Scripts/python.exe bridge_server.py [--port 8188]
"""
import argparse
import io
import logging
import os
import tempfile
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import sd_pipeline
import narration as narration_module
import stitch as stitch_module
from visual_profile import NEGATIVE_PROMPT

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-8s %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Scenarai Local Media Bridge")

_pipe = None
_kokoro = None
# Separate locks: image generation is GPU-bound, narration is CPU-bound --
# no reason a narration request should have to wait behind a queued image
# generation, they don't contend for the same resource.
_generation_lock = threading.Lock()
_narration_lock = threading.Lock()


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = NEGATIVE_PROMPT
    seed: int
    steps: int = 20


class NarrateRequest(BaseModel):
    text: str
    voice: str = narration_module.DEFAULT_VOICE
    speed: float = 1.0


class StitchRequest(BaseModel):
    """image_b64/audio_b64: base64-encoded bytes -- simplest way to pass
    two binary payloads in one JSON request without multipart handling."""
    image_b64: str
    audio_b64: str


@app.on_event("startup")
def load_models():
    global _pipe, _kokoro
    logger.info("Loading SD1.5 pipeline (one-time, kept resident)...")
    _pipe = sd_pipeline.load_pipeline()
    logger.info("Pipeline loaded.")
    if narration_module.models_available():
        logger.info("Loading Kokoro narrator...")
        _kokoro = narration_module.load_narrator()
        logger.info("Kokoro loaded.")
    else:
        logger.warning("Kokoro model files not found -- /narrate will 503 until they're downloaded.")


@app.get("/health")
def health():
    return {
        "status": "ok" if _pipe is not None else "loading",
        "image_ready": _pipe is not None,
        "narration_ready": _kokoro is not None,
        "ffmpeg_available": stitch_module.ffmpeg_available(),
    }


@app.post("/generate")
def generate(req: GenerateRequest):
    if _pipe is None:
        raise HTTPException(503, "Pipeline still loading, try again shortly.")
    # Only one GPU -- serialize requests rather than let diffusers calls
    # race on the same CUDA context. Concurrent callers wait their turn
    # instead of failing; this is the "accept latency, not breakage" design.
    with _generation_lock:
        try:
            image = sd_pipeline.generate(_pipe, req.prompt, req.negative_prompt, req.seed, req.steps)
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            raise HTTPException(500, f"Generation failed: {e}")

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/narrate")
def narrate(req: NarrateRequest):
    if _kokoro is None:
        raise HTTPException(503, "Narration model not loaded (missing model files or still starting).")
    with _narration_lock:
        try:
            samples, sample_rate = narration_module.narrate(_kokoro, req.text, voice=req.voice, speed=req.speed)
        except Exception as e:
            logger.error(f"Narration failed: {e}")
            raise HTTPException(500, f"Narration failed: {e}")

    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    return Response(content=buf.getvalue(), media_type="audio/wav")


@app.post("/stitch")
def stitch(req: StitchRequest):
    if not stitch_module.ffmpeg_available():
        raise HTTPException(503, "ffmpeg not found on this machine's PATH.")
    import base64
    image_bytes = base64.b64decode(req.image_b64)
    audio_bytes = base64.b64decode(req.audio_b64)

    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, "frame.png")
        audio_path = os.path.join(tmp, "audio.wav")
        out_path = os.path.join(tmp, "segment.mp4")
        with open(img_path, "wb") as f: f.write(image_bytes)
        with open(audio_path, "wb") as f: f.write(audio_bytes)
        try:
            stitch_module.stitch_image_audio(img_path, audio_path, out_path)
        except Exception as e:
            logger.error(f"Stitch failed: {e}")
            raise HTTPException(500, f"Stitch failed: {e}")
        with open(out_path, "rb") as f:
            video_bytes = f.read()

    return Response(content=video_bytes, media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8188)
    args = p.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)
