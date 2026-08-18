"""
Centralized error visibility — before this, an unhandled exception in any
route just became FastAPI's default opaque 500 with no structured log,
no way to correlate it back to a specific request, and no hook for an
external monitoring service. Right now the only way to know something
broke was a user reporting it or someone reading raw uvicorn output.

Two pieces:
  - request ID middleware: tags every request with a UUID, in both logs
    and the response header, so a user-reported error can actually be
    traced back to its exact log lines.
  - a global exception handler: catches anything that reaches it
    unhandled, logs full context + traceback, and returns a safe generic
    message (never leaks internals to the client).

Not wired to Sentry or any external service yet -- that needs an account
(same external-signup blocker as the Cloudflare/Oracle work). The
integration point is marked below; forwarding to Sentry once a DSN
exists is a single call, not a redesign.
"""
import logging
import time
import traceback
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("scenarai.errors")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        t0 = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # BaseHTTPMiddleware's call_next() re-raises here rather than
            # returning a response -- a generic-Exception handler (like
            # unhandled_exception_handler below) runs in Starlette's
            # ServerErrorMiddleware, which sits ABOVE this middleware in
            # the stack, so the code after call_next() never ran for any
            # 500. Confirmed live with an isolated repro: the X-Request-ID
            # response header AND this completion/latency log line were
            # both silently skipped for every unhandled exception -- the
            # one case this middleware most exists for ("so a user-reported
            # error can actually be traced back to its exact log lines").
            # Log the failed request here, then re-raise unchanged so
            # ServerErrorMiddleware's own handling is untouched.
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.info(
                "[req %s] %s %s -> ERROR (%sms)",
                request_id, request.method, request.url.path, latency_ms,
            )
            raise
        response.headers["X-Request-ID"] = request_id
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "[req %s] %s %s -> %d (%sms)",
            request_id, request.method, request.url.path, response.status_code, latency_ms,
        )
        return response


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "[req %s] UNHANDLED %s %s -- %s: %s\n%s",
        request_id, request.method, request.url.path,
        type(exc).__name__, exc, traceback.format_exc(),
    )

    # Sentry integration point -- once SENTRY_DSN is configured:
    #   import sentry_sdk
    #   sentry_sdk.capture_exception(exc)

    # Set directly on this response, not left to RequestIDMiddleware's
    # post-call_next() code -- that code never runs for an exception this
    # handler is catching (see RequestIDMiddleware's docstring above).
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )
