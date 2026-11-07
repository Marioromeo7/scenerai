"""Optional, privacy-conscious webhook alerts for important app events."""
import asyncio
import json
import logging
import time
import urllib.request

import httpx

from config import settings

logger = logging.getLogger("scenarai.alerts")
_last_sent: dict[str, float] = {}
_cooldown_lock = asyncio.Lock()


def _payload(event: str, summary: str, details: dict | None = None) -> dict:
    message = f"Scenarai [{event}]: {summary}"
    fields = {"event": event, "summary": summary, "service": "scenarai", **(details or {})}
    if details:
        message += "\n" + json.dumps(details, ensure_ascii=False, separators=(",", ":"))
    fmt = settings.alert_webhook_format.strip().lower()
    if fmt == "discord":
        return {"content": message[:1900]}
    if fmt == "slack":
        return {"text": message}
    return fields


async def send_alert(
    event: str,
    summary: str,
    *,
    details: dict | None = None,
    cooldown_key: str | None = None,
    cooldown_seconds: int = 300,
) -> None:
    """Send a best-effort alert; failures never break the app request/job."""
    url = settings.alert_webhook_url.strip()
    if not url:
        return

    if cooldown_key:
        now = time.monotonic()
        async with _cooldown_lock:
            previous = _last_sent.get(cooldown_key, 0.0)
            if now - previous < cooldown_seconds:
                return
            _last_sent[cooldown_key] = now
            if len(_last_sent) > 128:
                _last_sent.clear()
                _last_sent[cooldown_key] = now

    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
            response = await client.post(url, json=_payload(event, summary, details))
            response.raise_for_status()
    except Exception as exc:
        # HTTP client exceptions can include the request URL, which is a secret.
        logger.warning("Webhook alert delivery failed (%s)", type(exc).__name__)


def send_alert_sync(event: str, summary: str, *, details: dict | None = None) -> None:
    """Small synchronous variant used by the Render process supervisor."""
    url = settings.alert_webhook_url.strip()
    if not url:
        return
    request = urllib.request.Request(
        url,
        data=json.dumps(_payload(event, summary, details)).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3):
            pass
    except Exception as exc:
        logger.warning("Webhook alert delivery failed (%s)", type(exc).__name__)
