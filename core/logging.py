"""
Structured logging with per-request correlation IDs.

Every log line carries a `request_id` so all the lines emitted while
handling one HTTP request can be grepped together — previously the
admin_audit lines and error tracebacks had no way to be correlated across
concurrent requests. The id is taken from an inbound `X-Request-ID` header
(so it can be threaded through a reverse proxy) or generated per request,
and echoed back on the response.

Usage:
    from core.logging import setup_logging, RequestIDMiddleware
    setup_logging()                 # once, at import/startup
    app.add_middleware(RequestIDMiddleware)
"""
import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Default "-" so lines emitted outside a request (startup, background tasks)
# still format cleanly.
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> str:
    return _request_id_ctx.get()


def set_request_id(value: str) -> None:
    _request_id_ctx.set(value)


class _RequestIDFilter(logging.Filter):
    """Inject the current request_id onto every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging with a request-id-aware formatter.

    Idempotent: re-running won't stack duplicate handlers.
    """
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [req=%(request_id)s] %(name)s - %(message)s"
    )
    req_filter = _RequestIDFilter()

    handler = next(
        (h for h in root.handlers if isinstance(h, logging.StreamHandler)), None
    )
    if handler is None:
        handler = logging.StreamHandler()
        root.addHandler(handler)
    handler.setFormatter(formatter)

    # Filter on every handler so records always have request_id before
    # formatting (a record missing the attr would raise in the formatter).
    for h in root.handlers:
        if not any(isinstance(f, _RequestIDFilter) for f in h.filters):
            h.addFilter(req_filter)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign/propagate a request id and echo it on the response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        token = _request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
