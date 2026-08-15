"""Tests for error_monitoring.py — request ID tagging and the global
unhandled-exception handler."""
import pytest
from unittest.mock import MagicMock
import error_monitoring


class TestUnhandledExceptionHandler:
    @pytest.mark.asyncio
    async def test_returns_generic_500_not_internal_details(self):
        request = MagicMock()
        request.state.request_id = "test-req-id"
        request.method = "GET"
        request.url.path = "/some/route"
        exc = ValueError("a very specific internal detail that shouldn't leak")

        response = await error_monitoring.unhandled_exception_handler(request, exc)

        assert response.status_code == 500
        body = response.body.decode()
        assert "internal detail" not in body  # never leak the raw exception message
        assert "Internal server error" in body

    @pytest.mark.asyncio
    async def test_response_includes_request_id_for_correlation(self):
        request = MagicMock()
        request.state.request_id = "abc-123"
        request.method = "GET"
        request.url.path = "/some/route"
        exc = RuntimeError("boom")

        response = await error_monitoring.unhandled_exception_handler(request, exc)

        assert "abc-123" in response.body.decode()

    @pytest.mark.asyncio
    async def test_missing_request_id_does_not_crash(self):
        """Belt-and-suspenders: if RequestIDMiddleware somehow didn't run
        (e.g. an exception during middleware setup itself), the handler
        still has to return a response, not raise a second exception."""
        request = MagicMock()
        del request.state.request_id  # simulate it never being set
        request.state = MagicMock(spec=[])  # no request_id attribute at all
        request.method = "GET"
        request.url.path = "/some/route"
        exc = RuntimeError("boom")

        response = await error_monitoring.unhandled_exception_handler(request, exc)

        assert response.status_code == 500
