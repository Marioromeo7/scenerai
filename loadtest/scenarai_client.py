"""
Tier B load-test client — Section 3 of NEXT_PHASE_PLAN.md.

Drives the two real user flows (player, creator) directly over HTTP with
httpx, without a Flutter/emulator layer. Each call returns a structured
result record matching the plan's per-client reporting schema so a run can
be aggregated into failure-rate-vs-concurrency curves by category.

Failure categories (tracked separately, per the plan):
  hard_error       — any non-2xx response, or a request that raised.
  latency_breach   — a step's duration crosses its configured threshold.
                      Recorded, NOT fail-fast: Groq's own exponential backoff
                      on rate limits is correct, intended behavior (slow is
                      not broken), so a slow step must not abort the rest of
                      the client's scripted flow — only a genuine hard_error
                      or silent_bad_state does that. Earlier versions of this
                      harness treated any breach as terminal, which meant a
                      single slow cheap step (e.g. create_persona under
                      concurrency) silently prevented the client from ever
                      reaching the turn calls that are the actual point of
                      the test.
  silent_bad_state — HTTP 200 but the session ends up wrong (stuck
                      initializing, turn count doesn't advance, engine_state
                      lost) — the category that fix-map items 1 and 3 target.
"""
import time
import uuid
import httpx

PLAYER_TURNS = [
    "I look around the room, taking in the details.",
    "I ask what's going on here.",
    "I take a step closer and wait for a response.",
]


class ClientResult:
    def __init__(self, client_id, flow, concurrency_at_failure):
        self.client_id = client_id
        self.flow = flow
        self.concurrency_at_failure = concurrency_at_failure
        self.failure_category = None      # hard_error | silent_bad_state | None — NOT latency_breach, see record_breach
        self.failed_step = None
        self.latencies_ms = {}            # step -> duration_ms
        self.latency_breaches = []        # [{"step", "elapsed_ms", "threshold_ms"}] — observed, non-fatal
        self.error_detail = None
        self.timestamp = None
        self.succeeded = False

    def fail(self, category, step, detail="", timestamp=None):
        # First failure wins — a client's report should point at the root cause,
        # not whatever broke next as a consequence.
        if self.failure_category is not None:
            return
        self.failure_category = category
        self.failed_step = step
        self.error_detail = str(detail)[:500]
        self.timestamp = timestamp or time.time()

    def record_breach(self, step, elapsed_ms, threshold_ms):
        """A slow step is data, not a failure — Groq's own backoff on rate
        limits is correct, intended behavior. Recording it must never stop
        the client's scripted flow the way fail() does."""
        self.latency_breaches.append({
            "step": step, "elapsed_ms": round(elapsed_ms, 1), "threshold_ms": threshold_ms,
        })

    def to_dict(self):
        p50_before = None
        if self.latencies_ms:
            vals = sorted(self.latencies_ms.values())
            p50_before = vals[len(vals) // 2]
        return {
            "client_id": self.client_id,
            "flow": self.flow,
            "concurrency_at_failure": self.concurrency_at_failure,
            "failure_category": self.failure_category,
            "failed_step": self.failed_step,
            "latency_ms_p50_before_failure": p50_before,
            "timestamp": self.timestamp,
            "error_detail": self.error_detail,
            "succeeded": self.succeeded,
            "step_latencies_ms": self.latencies_ms,
            "latency_breaches": self.latency_breaches,
        }


async def _timed(result, step, thresholds, coro, threshold_key=None):
    """Run one HTTP call, record its latency, and record (not fail on) a
    latency_breach if it crosses this step's configured threshold. Returns
    the response or None if the call raised (network error, timeout) —
    caller decides how to classify. threshold_key lets multiple distinctly-
    named steps (e.g. turn_1, turn_2) share one threshold entry (e.g. "turn")
    without colliding in latencies_ms."""
    threshold_key = threshold_key or step
    start = time.monotonic()
    try:
        resp = await coro
    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        result.latencies_ms[step] = elapsed_ms
        result.fail("hard_error", step, f"request exception after {elapsed_ms:.0f}ms: {e!r}")
        return None
    elapsed_ms = (time.monotonic() - start) * 1000
    result.latencies_ms[step] = elapsed_ms
    threshold = thresholds.get(threshold_key)
    if threshold and elapsed_ms > threshold:
        result.record_breach(step, elapsed_ms, threshold)
    return resp


async def _poll_status(client, result, session_id, thresholds, max_wait_s=60, interval_s=1.0):
    """Poll /sessions/{id}/status until 'ready'. A stuck 'initializing' or
    'error' status with HTTP 200 the whole time is exactly the silent_bad_state
    fix-map items 1/3 are meant to prevent."""
    start = time.monotonic()
    while time.monotonic() - start < max_wait_s:
        resp = await _timed(result, "session_status_poll", thresholds,
                             client.get(f"/sessions/{session_id}/status"))
        if resp is None:
            return False
        if resp.status_code != 200:
            result.fail("hard_error", "session_status_poll", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        status = resp.json().get("status")
        if status == "ready":
            # Every poll iteration reused the same "session_status_poll" key,
            # so latencies_ms only ever kept the LAST poll's near-instant
            # round-trip -- the actually-interesting number (how long the
            # user waited for the engine to become usable) was never
            # recorded anywhere. Cheap to add, no threshold configured for
            # it in thresholds.json so it's recorded without breach-checking.
            result.latencies_ms["session_init_wait"] = (time.monotonic() - start) * 1000
            return True
        if status == "error":
            result.fail("silent_bad_state", "session_init_error",
                         f"status flipped to error: {resp.json()}")
            return False
        await _sleep(interval_s)
    result.fail("silent_bad_state", "session_init_timeout",
                f"never reached 'ready' within {max_wait_s}s (stuck initializing)")
    return False


async def _sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)


async def player_flow(base_url, client_id, concurrency_at_start, thresholds, turns=None, timeout=240.0):
    """login (register) -> browse published scenario -> create session ->
    poll status -> N scripted turns -> verify history -> end session.

    timeout=240s, not the previous 120s -- the committed thresholds.json
    baseline itself records a real turn_2 latency of 160020.2ms (Groq
    rate-limit backoff, expected/correct behavior per this module's own
    docstring on latency_breach). A 120s httpx client timeout aborted that
    call before it could complete, recording it as hard_error and ending
    the client's flow before it ever reached later turns -- reintroducing
    exactly the "a slow step must not abort the rest of the flow" bug this
    module's docstring says was already fixed once."""
    result = ClientResult(client_id, "player", concurrency_at_start)
    turns = turns or PLAYER_TURNS
    # pydantic's EmailStr checks domain deliverability (MX records), which rejects
    # RFC 2606 reserved TLDs like .test/.example — gmail.com always resolves,
    # and only the random local-part needs to be unique, not a real mailbox.
    email = f"loadtest-player-{uuid.uuid4().hex[:12]}@gmail.com"
    password = "LoadTest12345!"

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        resp = await _timed(result, "register", thresholds, client.post(
            "/auth/register", json={"email": email, "password": password}))
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 201:
            result.fail("hard_error", "register", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        resp = await _timed(result, "create_persona", thresholds, client.post(
            "/personas", json={"name": "LoadTest Player", "pronouns": "they/them", "brief": ""}))
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 201:
            result.fail("hard_error", "create_persona", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result
        persona_id = resp.json()["id"]

        resp = await _timed(result, "browse_public", thresholds, client.get("/scenarios/public?limit=5"))
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 200:
            result.fail("hard_error", "browse_public", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result
        items = resp.json().get("items", [])
        if not items:
            result.fail("silent_bad_state", "browse_public", "no published scenarios available to play")
            return result
        scenario_id = items[0]["id"]

        resp = await _timed(result, "create_session", thresholds, client.post(
            "/sessions", json={"scenario_id": scenario_id, "persona_id": persona_id,
                                "content_filter": "off", "preview": False}))
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 201:
            result.fail("hard_error", "create_session", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result
        session_id = resp.json()["session_id"]

        if not await _poll_status(client, result, session_id, thresholds):
            return result

        for i, turn_text in enumerate(turns):
            step_name = f"turn_{i + 1}"
            resp = await _timed(result, step_name, thresholds, client.post(
                f"/sessions/{session_id}/turn", json={"input": turn_text}), threshold_key="turn")
            if resp is None or result.failure_category:
                return result
            if resp.status_code != 200:
                result.fail("hard_error", step_name, f"HTTP {resp.status_code} on turn {i + 1}: {resp.text[:200]}")
                return result
            body = resp.json()
            if not body.get("response"):
                result.fail("silent_bad_state", step_name, f"turn {i + 1} returned empty response with HTTP 200")
                return result
            if body.get("turn") != i + 1:
                result.fail("silent_bad_state", step_name,
                             f"turn counter mismatch: expected {i + 1}, got {body.get('turn')} — history may be corrupted")
                return result

        resp = await _timed(result, "get_history", thresholds, client.get(f"/sessions/{session_id}/history"))
        # The only step in this file that didn't explicitly check a non-200
        # status (found via code review): a 404/500 with no exception fell
        # through this `== 200` guard silently, straight to end_session and
        # a possible succeeded=True -- masking a real failure in exactly
        # the check this harness exists to catch (silent_bad_state).
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 200:
            result.fail("hard_error", "get_history", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result
        if resp.json().get("turns") != len(turns):
            result.fail("silent_bad_state", "get_history",
                         f"expected {len(turns)} persisted turns, session_log shows {resp.json().get('turns')}")
            return result

        await _timed(result, "end_session", thresholds, client.delete(f"/sessions/{session_id}"))
        if not result.failure_category:
            result.succeeded = True
    return result


async def creator_flow(base_url, client_id, concurrency_at_start, thresholds, timeout=240.0):
    """login (register) -> create scenario -> publish -> poll prefab_status ->
    preview session (1 turn) -> end."""
    result = ClientResult(client_id, "creator", concurrency_at_start)
    email = f"loadtest-creator-{uuid.uuid4().hex[:12]}@gmail.com"
    password = "LoadTest12345!"

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        resp = await _timed(result, "register", thresholds, client.post(
            "/auth/register", json={"email": email, "password": password}))
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 201:
            result.fail("hard_error", "register", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        resp = await _timed(result, "create_scenario", thresholds, client.post(
            "/scenarios", json={
                "char_name": "Loadtest Character",
                "char_pronouns": "she/her",
                "char_title": "A wary innkeeper",
                "char_personality": "Guarded but fair. Watches strangers closely before trusting them.",
                "greeting": "You push open the heavy door. The innkeeper looks up from the counter, eyes narrowing slightly as she takes you in.",
            }))
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 201:
            result.fail("hard_error", "create_scenario", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result
        scenario_id = resp.json()["id"]

        resp = await _timed(result, "publish", thresholds, client.post(f"/scenarios/{scenario_id}/publish"))
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 200:
            result.fail("hard_error", "publish", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result

        if not await _poll_prefab(client, result, scenario_id, thresholds):
            return result

        resp = await _timed(result, "create_persona", thresholds, client.post(
            "/personas", json={"name": "Loadtest Previewer", "pronouns": "they/them", "brief": ""}))
        if resp is None or result.failure_category:
            return result
        persona_id = resp.json()["id"]

        resp = await _timed(result, "create_preview_session", thresholds, client.post(
            "/sessions", json={"scenario_id": scenario_id, "persona_id": persona_id,
                                "content_filter": "off", "preview": True}))
        if resp is None or result.failure_category:
            return result
        if resp.status_code != 201:
            result.fail("hard_error", "create_preview_session", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result
        session_id = resp.json()["session_id"]

        if not await _poll_status(client, result, session_id, thresholds):
            return result

        resp = await _timed(result, "turn", thresholds, client.post(
            f"/sessions/{session_id}/turn", json={"input": "I nod to the innkeeper."}))
        if resp is not None and resp.status_code != 200:
            result.fail("hard_error", "turn", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result

        await _timed(result, "end_session", thresholds, client.delete(f"/sessions/{session_id}"))
        if not result.failure_category:
            result.succeeded = True
    return result


async def _poll_prefab(client, result, scenario_id, thresholds, max_wait_s=90, interval_s=2.0):
    """Poll GET /scenarios/{id} for prefab_status — this is fix-map item 1's
    signal replacing the old silent-forever-None failure mode."""
    start = time.monotonic()
    while time.monotonic() - start < max_wait_s:
        resp = await _timed(result, "prefab_status_poll", thresholds, client.get(f"/scenarios/{scenario_id}"))
        if resp is None:
            return False
        if resp.status_code != 200:
            result.fail("hard_error", "prefab_status_poll", f"HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        status = resp.json().get("prefab_status")
        if status == "ready":
            # Same fix as _poll_status's session_init_wait -- see there.
            result.latencies_ms["prefab_wait"] = (time.monotonic() - start) * 1000
            return True
        if status == "failed":
            result.fail("hard_error", "prefab_missing", "prefab_status flipped to 'failed'")
            return False
        await _sleep(interval_s)
    result.fail("silent_bad_state", "prefab_missing",
                f"prefab_status never reached 'ready'/'failed' within {max_wait_s}s")
    return False
