import asyncio
import json
import logging
import os

from arq.connections import RedisSettings
from sqlalchemy import select, update

from config import settings
from database import get_redis, AsyncSessionLocal
from models import Scenario, TurnMedia
from ai_service import engine_prefab, engine_init, engine_init_from_prefab

# main.py configures this for the API process; the worker is a separate
# process (own CMD in docker-compose) and needs its own setup, or every
# INFO-level log from prefab/init jobs — including the NPC-classification
# fallback logs — is silently dropped under Python's default WARNING root level.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

logger = logging.getLogger(__name__)

PREFAB_JOB_KEY = "prefab:running_count"
PREFAB_MAX     = 2
SESSION_TTL    = 60 * 60 * 12  # 12 hours, must match main.py


async def prefab_engine_job(
    ctx,
    scenario_id: str,
    char_name: str, char_pronouns: str, char_title: str,
    char_personality: str, greeting: str,
):
    """arq job: pre-compute engine at publish time and persist it, with a
    surfaced prefab_status instead of a silent permanent None."""
    redis = await get_redis()
    count = await redis.incr(PREFAB_JOB_KEY)
    # Rolling TTL so a worker crash between incr and the finally-block decr
    # (below) doesn't leak a permanently-stuck slot — arq's own max_jobs caps
    # total concurrency across both job types, but this counter additionally
    # protects Groq from being hit by up to max_jobs prefab jobs at once, each
    # of which is 15-20 calls; without a bound here a leaked slot degrades
    # prefab throughput forever instead of self-healing within a few minutes.
    await redis.expire(PREFAB_JOB_KEY, 300)
    if count > PREFAB_MAX:
        await redis.decr(PREFAB_JOB_KEY)
        logger.warning(f'Prefab skipped for {scenario_id[:8]}: too many concurrent jobs ({count - 1})')
        async with AsyncSessionLocal() as db:
            await db.execute(update(Scenario).where(Scenario.id == scenario_id).values(prefab_status="failed"))
            await db.commit()
        return
    try:
        prefab = await engine_prefab(char_name, char_pronouns, char_title, char_personality, greeting)
        async with AsyncSessionLocal() as db:
            if prefab:
                await db.execute(
                    update(Scenario).where(Scenario.id == scenario_id)
                    .values(prefab_engine_state=prefab, prefab_status="ready")
                )
                logger.info(f'Scenario {scenario_id[:8]} prefab ready')
            else:
                await db.execute(update(Scenario).where(Scenario.id == scenario_id).values(prefab_status="failed"))
                logger.error(f'Prefab failed for scenario {scenario_id[:8]}: engine_prefab returned nothing')
            await db.commit()
    except Exception as e:
        logger.error(f'Prefab failed for scenario {scenario_id[:8]}: {e}')
        async with AsyncSessionLocal() as db:
            await db.execute(update(Scenario).where(Scenario.id == scenario_id).values(prefab_status="failed"))
            await db.commit()
    finally:
        await redis.decr(PREFAB_JOB_KEY)


async def init_engine_job(
    ctx,
    session_id: str, scenario_id: str,
    persona_name: str, persona_pronouns: str,
    greeting: str, content_filter: str, preview: bool,
    restored_engine_state: dict | None,
):
    """arq job: initialize engine (from prefab, from a resumed session, or a
    full init for draft previews) and write the result into the session's
    Redis context so /sessions/{id}/status flips from 'initializing'."""
    redis = await get_redis()
    try:
        if restored_engine_state:
            logger.info(f'Session {session_id[:8]} restoring from previous session')
            engine_state = restored_engine_state
        else:
            async with AsyncSessionLocal() as db:
                sc = (await db.execute(select(Scenario).where(Scenario.id == scenario_id))).scalar_one_or_none()
            if not sc:
                raise RuntimeError(f'Scenario {scenario_id} no longer exists')
            prefab = sc.prefab_engine_state

            if not prefab:
                if not preview:
                    raise RuntimeError(f'Scenario {scenario_id} has no prefab — only published scenarios can be played')
                logger.info(f'Session {session_id[:8]} full init for draft preview (15-20 LLM calls)')
                engine_state = await engine_init(
                    char_name=sc.char_name,
                    char_pronouns=sc.char_pronouns,
                    char_title=sc.char_title,
                    char_personality=sc.char_personality,
                    greeting=greeting,
                    player_name=persona_name,
                    player_pronouns=persona_pronouns,
                    content_filter=content_filter,
                )
            else:
                logger.info(f'Session {session_id[:8]} using prefab (fast init — 1 LLM call)')
                engine_state = await engine_init_from_prefab(
                    prefab,
                    player_name=persona_name,
                    player_pronouns=persona_pronouns,
                    greeting=greeting,
                    content_filter=content_filter,
                )
        raw = await redis.get(f"session:{session_id}")
        if raw:
            data = json.loads(raw)
            data["engine_state"] = engine_state
            data["status"]       = "ready"
            data["history"]      = engine_state.get("display_history") or engine_state.get("history", [])
            await redis.setex(f"session:{session_id}", SESSION_TTL, json.dumps(data))
            logger.info(f'Session {session_id[:8]} initialized and ready')
    except Exception as e:
        logger.error(f'Engine init failed for {session_id[:8]}: {e}')
        raw = await redis.get(f"session:{session_id}")
        if raw:
            data = json.loads(raw)
            data["status"] = "error"
            data["error"]  = str(e)
            await redis.setex(f"session:{session_id}", SESSION_TTL, json.dumps(data))


MEDIA_ROOT = "/app/media"  # matches docker-compose.yml's worker volume mount


async def generate_turn_media_job(
    ctx,
    session_id: str, scenario_id: str, user_id: str, turn: int,
    image_prompt: str, narration_text: str,
    voice_id: str, voice_speed: float,
):
    """arq job: image + narration + single-segment video for one turn,
    via the native bridge (imagepipe/bridge_server.py). Runs off the
    request/response cycle -- a turn's text comes back immediately,
    media appears in TurnMedia once this finishes (status starts
    'pending', flips to 'ready' or 'failed').

    On success, also extends the session's cumulative movie.mp4 (see
    _extend_session_movie) -- the frontend still plays per-turn segments
    as a playlist in movie mode, but a single always-current file is also
    kept for direct download/sharing.
    """
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(TurnMedia).where(TurnMedia.session_id == session_id, TurnMedia.turn == turn)
        )).scalar_one_or_none()
        if existing:
            row = existing
            row.status = "pending"
            row.error = None
        else:
            row = TurnMedia(user_id=user_id, session_id=session_id, scenario_id=scenario_id,
                             turn=turn, status="pending")
            db.add(row)
        row.image_prompt, row.narration_text = image_prompt, narration_text
        row.voice_id, row.voice_speed = voice_id, voice_speed
        await db.commit()

    # Deterministic per (session, turn) rather than random -- reproducible
    # if this job ever needs to be replayed for debugging, matches the
    # seed-determinism principle the whole image pipeline was built on.
    seed = abs(hash((session_id, turn))) % (2**31)
    await _run_media_pipeline(session_id, turn, image_prompt, narration_text, voice_id, voice_speed, seed)


async def regenerate_turn_media_job(ctx, session_id: str, turn: int):
    """Re-rolls just the image+audio+video for a turn that already has a
    TurnMedia row, reusing its stored prompt/narration/voice (see
    TurnMedia's docstring) with a genuinely different seed -- not the one
    that already produced whatever the user is unhappy with."""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(TurnMedia).where(TurnMedia.session_id == session_id, TurnMedia.turn == turn)
        )).scalar_one_or_none()
        if not row or not row.image_prompt:
            logger.error(f"Cannot regenerate media: no prior TurnMedia row for session={session_id[:8]} turn={turn}")
            return
        image_prompt, narration_text = row.image_prompt, row.narration_text
        voice_id, voice_speed = row.voice_id, row.voice_speed
        old_seed = row.image_seed
        row.status = "pending"
        row.error = None
        await db.commit()

    # +1 rather than re-hashing -- guarantees a different seed from
    # old_seed even if the hash-based seed for (session_id, turn) would
    # otherwise repeat; simple and sufficient, doesn't need to be random.
    new_seed = (old_seed + 1) if old_seed is not None else abs(hash((session_id, turn, "regen"))) % (2**31)
    await _run_media_pipeline(session_id, turn, image_prompt, narration_text, voice_id, voice_speed, new_seed)


async def _run_media_pipeline(session_id: str, turn: int, image_prompt: str, narration_text: str,
                               voice_id: str, voice_speed: float, seed: int):
    """Shared by generate_turn_media_job and regenerate_turn_media_job --
    everything after the TurnMedia row is created/reset and a seed is
    decided is identical between a fresh generation and a reroll."""
    import base64
    import httpx

    session_dir = f"{MEDIA_ROOT}/{session_id}"
    os.makedirs(session_dir, exist_ok=True)

    try:
        async with httpx.AsyncClient(timeout=settings.image_bridge_timeout) as client:
            img_resp = await client.post(f"{settings.image_bridge_url}/generate",
                                          json={"prompt": image_prompt, "seed": seed})
            img_resp.raise_for_status()
            image_bytes = img_resp.content

            audio_resp = await client.post(f"{settings.image_bridge_url}/narrate",
                                            json={"text": narration_text, "voice": voice_id, "speed": voice_speed})
            audio_resp.raise_for_status()
            audio_bytes = audio_resp.content

            stitch_resp = await client.post(f"{settings.image_bridge_url}/stitch", json={
                "image_b64": base64.b64encode(image_bytes).decode(),
                "audio_b64": base64.b64encode(audio_bytes).decode(),
            })
            stitch_resp.raise_for_status()
            video_bytes = stitch_resp.content

        image_path = f"{session_dir}/{turn}.png"
        audio_path = f"{session_dir}/{turn}.wav"
        video_path = f"{session_dir}/{turn}.mp4"
        with open(image_path, "wb") as f: f.write(image_bytes)
        with open(audio_path, "wb") as f: f.write(audio_bytes)
        with open(video_path, "wb") as f: f.write(video_bytes)

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(TurnMedia).where(TurnMedia.session_id == session_id, TurnMedia.turn == turn)
                .values(status="ready", image_seed=seed,
                        image_url=f"/media/{session_id}/{turn}.png",
                        audio_url=f"/media/{session_id}/{turn}.wav",
                        video_segment_url=f"/media/{session_id}/{turn}.mp4")
            )
            await db.commit()
        logger.info(f"Turn media ready: session={session_id[:8]} turn={turn} seed={seed}")
        await _extend_session_movie(session_id)

    except Exception as e:
        logger.error(f"Turn media failed: session={session_id[:8]} turn={turn}: {e}")
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(TurnMedia).where(TurnMedia.session_id == session_id, TurnMedia.turn == turn)
                .values(status="failed", error=str(e)[:500])
            )
            await db.commit()


MOVIE_LOCK_TTL = 30  # seconds -- generous vs. actual concat time (a few seconds)


async def _extend_session_movie(session_id: str):
    """Keeps {session_dir}/movie.mp4 as an always-current concatenation of
    every ready turn's segment in order -- a single downloadable/shareable
    file for "the whole story so far" without picking a turn range (see
    main.py's /sessions/{id}/export, which this shares its ffmpeg approach
    with for an explicit range).

    Appends cheaply (concat [current movie.mp4, 1 new segment]) when new
    turns extend the movie contiguously -- O(1) work per turn, not O(n).
    Falls back to a full rebuild only when the desired turn list isn't a
    simple extension of what's already baked in (a regenerate changed a
    turn already included, or turns finished out of order under
    concurrent arq jobs).

    Guarded by a short Redis lock so two job completions for the same
    session don't race on the same output file. Skip-on-contention (not
    retry) is safe: whoever next acquires the lock re-scans DB state from
    scratch, so a skipped update is only deferred, never lost -- the next
    turn's completion (or a retry) converges the file to the same result.

    Best-effort: errors here are logged, not raised -- a movie-build
    problem must never flip an already-successful turn's media to
    'failed'.
    """
    try:
        redis = await get_redis()
        lock_key = f"movie_lock:{session_id}"
        got_lock = await redis.set(lock_key, "1", nx=True, ex=MOVIE_LOCK_TTL)
        if not got_lock:
            return
        try:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(TurnMedia.turn).where(
                        TurnMedia.session_id == session_id, TurnMedia.status == "ready",
                        TurnMedia.video_segment_url.isnot(None),
                    ).order_by(TurnMedia.turn)
                )).scalars().all()
            if not rows:
                return

            # Longest contiguous run starting at the first ready turn --
            # a gap (a still-pending or failed turn in the middle) means
            # the movie can't safely extend past it yet.
            contiguous = [rows[0]]
            for t in rows[1:]:
                if t == contiguous[-1] + 1:
                    contiguous.append(t)
                else:
                    break

            session_dir = f"{MEDIA_ROOT}/{session_id}"
            state_path  = f"{session_dir}/movie_state.json"
            movie_path  = f"{session_dir}/movie.mp4"

            try:
                with open(state_path) as f:
                    current = json.load(f).get("turns", [])
            except (FileNotFoundError, json.JSONDecodeError):
                current = []

            if current == contiguous:
                return  # already up to date

            is_extension = bool(current) and contiguous[:len(current)] == current
            if is_extension:
                for t in contiguous[len(current):]:
                    await _concat_videos([movie_path, f"{session_dir}/{t}.mp4"], movie_path)
            else:
                await _concat_videos([f"{session_dir}/{t}.mp4" for t in contiguous], movie_path)

            with open(state_path, "w", encoding="utf-8") as f:
                json.dump({"turns": contiguous}, f)
            logger.info(f"Session movie extended: session={session_id[:8]} turns=1..{contiguous[-1]}")
        finally:
            await redis.delete(lock_key)
    except Exception as e:
        logger.error(f"Session movie extend failed for {session_id[:8]}: {e}")


async def _concat_videos(input_paths: list[str], output_path: str):
    """Shared ffmpeg concat-demuxer helper. Re-encodes rather than stream-
    copies -- confirmed live (see /sessions/{id}/export in main.py) that
    stream-copy across independently-stitched segments produces
    "Non-monotonic DTS" warnings at the splice point; re-encoding (fast
    preset, short clips) avoids that for a small time cost.

    Always writes to a temp file and atomically replaces output_path --
    required (not just nice-to-have) when output_path is also one of the
    inputs, as it is for the append case: ffmpeg can't safely read and
    write the same file at once, and a crash mid-encode must never leave
    a truncated movie.mp4 visible to something already serving it."""
    import tempfile

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
            # Explicit output format -- tmp_output's extension is ".tmp",
            # not ".mp4" (it's renamed after success), so ffmpeg can't
            # infer the muxer from the filename the way it can for
            # /export's tmp-free output path. Found live: without this,
            # ffmpeg fails with "Unable to choose an output format".
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


class WorkerSettings:
    functions = [prefab_engine_job, init_engine_job, generate_turn_media_job, regenerate_turn_media_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 300
