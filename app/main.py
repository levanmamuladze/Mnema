import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from sqlalchemy import text

from api.routers import admin, auth, chat
from app import __version__
from core import metrics
from core.config import settings
from core.exceptions import AppException
from core.limiter import limiter
from core.logging import RequestIDMiddleware, setup_logging
from db.session import AsyncSessionLocal, init_db


def _init_sentry() -> None:
    """Initialise Sentry error tracking if a DSN is configured.

    No-op when SENTRY_DSN is empty or the SDK isn't installed, so it never
    affects the default lightweight deployment.
    """
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; "
            "run `pip install sentry-sdk` to enable error tracking."
        )
        return
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.0,  # errors only by default; raise for tracing
        send_default_pii=False,
    )

# Uvicorn only attaches handlers to its own loggers; configure the root logger
# at import time so our module-level logger.info(...) calls are observable.
setup_logging()

logger = logging.getLogger(__name__)


# How often the background reaper runs. The token denylist would otherwise
# grow without bound as logged-out / rotated refresh tokens accumulate.
_CLEANUP_INTERVAL_SECONDS = 3600


async def _periodic_cleanup() -> None:
    """Purge expired refresh-token denylist rows.

    Runs once at startup and then every hour. Errors are logged and
    swallowed so a transient DB hiccup never kills the loop.
    """
    from db.repositories.token_repository import RevokedTokenRepository

    while True:
        try:
            async with AsyncSessionLocal() as session:
                tokens = await RevokedTokenRepository(session).purge_expired()
                await session.commit()
            if tokens:
                logger.info("cleanup: purged %d expired tokens", tokens)
        except Exception:
            logger.exception("Periodic cleanup failed")
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: validate config, init DB, launch background cleanup."""
    settings.assert_production_ready()
    await init_db()
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


def create_app() -> FastAPI:
    _init_sentry()
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.DEBUG,
        lifespan=lifespan,
        # Hide API docs in production
        docs_url="/api/docs" if settings.DEBUG else None,
        redoc_url="/api/redoc" if settings.DEBUG else None,
        openapi_url="/api/openapi.json" if settings.DEBUG else None,
    )

    # Added first so it's the outermost middleware (Starlette runs them in
    # reverse registration order), so every log line carries the request id.
    app.add_middleware(RequestIDMiddleware)

    if settings.METRICS_ENABLED:
        @app.middleware("http")
        async def metrics_middleware(request: Request, call_next):
            start = time.perf_counter()
            response = await call_next(request)
            # Skip the scrape endpoint itself to avoid self-referential noise.
            if request.url.path != "/metrics":
                metrics.record_request(
                    request.method,
                    response.status_code,
                    time.perf_counter() - start,
                )
            return response

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # CSP allows 'unsafe-inline' for scripts because the templates use inline
    # handlers; jsdelivr is whitelisted for marked + DOMPurify.
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'",
        )
        # HSTS only over HTTPS — gate on the secure-cookie flag.
        if settings.COOKIE_SECURE:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    # Global handler — all AppExceptions return clean JSON (no stack traces)
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    if os.path.exists("frontend/static"):
        app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(admin.router)

    templates = Jinja2Templates(directory="frontend/templates")

    @app.get("/")
    async def serve_root(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/chat-ui")
    async def serve_chat(request: Request):
        return templates.TemplateResponse("chat.html", {"request": request})

    @app.get("/admin-ui")
    async def serve_admin(request: Request):
        return templates.TemplateResponse("admin.html", {"request": request})

    if settings.METRICS_ENABLED:
        @app.get("/metrics")
        async def get_metrics(request: Request) -> Response:
            # Optional bearer-token guard. When METRICS_TOKEN is unset the
            # endpoint is open — only do that when /metrics is not exposed to
            # untrusted networks (it is never proxied by the shipped Caddyfile).
            if settings.METRICS_TOKEN:
                auth = request.headers.get("Authorization", "")
                expected = f"Bearer {settings.METRICS_TOKEN}"
                if auth != expected:
                    return PlainTextResponse("Unauthorized", status_code=401)
            return PlainTextResponse(
                metrics.render(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

    @app.get("/health")
    async def health() -> JSONResponse:
        # Probe the DB so the healthcheck fails when Postgres is down. Ollama is
        # intentionally not probed — a slow model load shouldn't flap the check.
        db_ok = True
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
            logger.exception("Health check: database unreachable")
        return JSONResponse(
            status_code=200 if db_ok else 503,
            content={
                "status": "ok" if db_ok else "degraded",
                "database": "ok" if db_ok else "unreachable",
                "version": __version__,
            },
        )

    return app


app = create_app()
