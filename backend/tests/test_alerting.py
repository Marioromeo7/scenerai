"""Tests for optional webhook alert formatting and delivery behavior."""
import logging
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

import alerting
import error_monitoring


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("generic", {"event": "example", "summary": "A safe notice", "service": "scenarai", "count": 2}),
        ("discord", {"content": 'Scenarai [example]: A safe notice\n{"count":2}'}),
        ("slack", {"text": 'Scenarai [example]: A safe notice\n{"count":2}'}),
    ],
)
def test_payload_matches_selected_webhook_format(monkeypatch, fmt, expected):
    monkeypatch.setattr(alerting.settings, "alert_webhook_format", fmt)
    assert alerting._payload("example", "A safe notice", {"count": 2}) == expected


def test_sync_sender_posts_safe_startup_alert(monkeypatch):
    monkeypatch.setattr(alerting.settings, "alert_webhook_url", "https://hooks.example.test/private")
    monkeypatch.setattr(alerting.settings, "alert_webhook_format", "generic")
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
    alerting.send_alert_sync("startup_failed", "Startup failed.", details={"exception_type": "RuntimeError"})

    request, timeout = requests[0]
    assert request.full_url == "https://hooks.example.test/private"
    assert timeout == 3
    assert json.loads(request.data) == {
        "event": "startup_failed",
        "summary": "Startup failed.",
        "service": "scenarai",
        "exception_type": "RuntimeError",
    }


@pytest.mark.asyncio
async def test_send_alert_posts_payload_and_returns_success(monkeypatch):
    monkeypatch.setattr(alerting.settings, "alert_webhook_url", "https://hooks.example.test/secret")
    monkeypatch.setattr(alerting.settings, "alert_webhook_format", "generic")
    calls = []

    class Response:
        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 3.0
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            calls.append((url, json))
            return Response()

    monkeypatch.setattr(alerting.httpx, "AsyncClient", Client)
    delivered = await alerting.send_alert("test_event", "Test notice", details={"request_id": "abc"})

    assert delivered is True
    assert calls == [(
        "https://hooks.example.test/secret",
        {"event": "test_event", "summary": "Test notice", "service": "scenarai", "request_id": "abc"},
    )]


@pytest.mark.asyncio
async def test_send_alert_without_url_does_not_make_request(monkeypatch):
    monkeypatch.setattr(alerting.settings, "alert_webhook_url", "")

    class NeverClient:
        def __init__(self, **kwargs):
            raise AssertionError("client should not be created without a webhook URL")

    monkeypatch.setattr(alerting.httpx, "AsyncClient", NeverClient)
    assert await alerting.send_alert("test_event", "Test notice") is False


@pytest.mark.asyncio
async def test_cooldown_suppresses_repeated_alerts(monkeypatch):
    monkeypatch.setattr(alerting.settings, "alert_webhook_url", "https://hooks.example.test/secret")
    alerting._last_sent.clear()
    posts = []

    class Response:
        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            posts.append(True)
            return Response()

    monkeypatch.setattr(alerting.httpx, "AsyncClient", Client)
    assert await alerting.send_alert("boom", "Failure", cooldown_key="same", cooldown_seconds=300)
    assert not await alerting.send_alert("boom", "Failure", cooldown_key="same", cooldown_seconds=300)
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_delivery_error_does_not_expose_webhook_url_in_logs(monkeypatch, caplog):
    secret_url = "https://hooks.example.test/keep-private"
    monkeypatch.setattr(alerting.settings, "alert_webhook_url", secret_url)

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            raise httpx.ConnectError(f"connection failed for {url}")

    monkeypatch.setattr(alerting.httpx, "AsyncClient", Client)
    with caplog.at_level(logging.WARNING, logger="scenarai.alerts"):
        delivered = await alerting.send_alert("delivery_error", "No details")

    assert delivered is False
    assert "ConnectError" in caplog.text
    assert secret_url not in caplog.text


@pytest.mark.asyncio
async def test_unhandled_error_response_schedules_safe_alert(monkeypatch):
    monkeypatch.setattr(alerting.settings, "alert_webhook_url", "https://hooks.example.test/secret")
    request = MagicMock()
    request.state.request_id = "req-123"
    request.scope = {"route": SimpleNamespace(path="/sessions/{session_id}/turn")}
    request.method = "POST"
    request.url.path = "/sessions/private-id/turn"

    response = await error_monitoring.unhandled_exception_handler(
        request, RuntimeError("do not include this exception message")
    )

    assert response.status_code == 500
    assert response.background.func is alerting.send_alert
    assert response.background.args[:2] == ("unhandled_error", "An unhandled server error occurred.")
    details = response.background.kwargs["details"]
    assert details == {
        "request_id": "req-123",
        "method": "POST",
        "route": "/sessions/{session_id}/turn",
        "exception_type": "RuntimeError",
    }
    assert "private-id" not in str(details)
