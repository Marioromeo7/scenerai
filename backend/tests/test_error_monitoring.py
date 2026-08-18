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

    @pytest.mark.asyncio
    async def test_regression_response_carries_x_request_id_header(self):
        """Before the fix, X-Request-ID was only ever set by
        RequestIDMiddleware's post-call_next() code -- which never runs for
        an unhandled exception, since call_next() re-raises rather than
        returning a response in that case (confirmed live with an isolated
        repro: the header was silently absent from every real 500). This
        handler must set it directly on its own response instead of relying
        on code that never executes for the case it's handling."""
        request = MagicMock()
        request.state.request_id = "abc-123"
        request.method = "GET"
        request.url.path = "/some/route"
        exc = RuntimeError("boom")

        response = await error_monitoring.unhandled_exception_handler(request, exc)

        assert response.headers["X-Request-ID"] == "abc-123"


class TestRequestIDMiddleware:
    @pytest.mark.asyncio
    async def test_sets_request_id_state_and_header_on_success(self):
        middleware = error_monitoring.RequestIDMiddleware(app=MagicMock())
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/ok"
        response = MagicMock()
        response.headers = {}
        response.status_code = 200

        async def call_next(req):
            return response

        result = await middleware.dispatch(request, call_next)

        assert result is response
        assert "X-Request-ID" in response.headers
        assert request.state.request_id == response.headers["X-Request-ID"]

    @pytest.mark.asyncio
    async def test_regression_reraises_unchanged_instead_of_swallowing_on_exception(self):
        """Before the fix, this middleware had no try/except around
        call_next() at all -- an unhandled exception just propagated
        straight through with no completion log entry for the failed
        request. Must still tag request.state.request_id (so the exception
        handler's response body can carry it) and must re-raise the exact
        same exception, not swallow it or wrap it in something else --
        ServerErrorMiddleware's own handling depends on seeing it."""
        middleware = error_monitoring.RequestIDMiddleware(app=MagicMock())
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/boom"

        async def call_next(req):
            raise RuntimeError("deliberate test exception")

        with pytest.raises(RuntimeError, match="deliberate test exception"):
            await middleware.dispatch(request, call_next)

        assert request.state.request_id  # tagged even though the request failed
