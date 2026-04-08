import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.routers import admin, auth, chat
from core.config import settings
from core.exceptions import AppException
from core.limiter import limiter
from db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: validate config, init DB."""
    settings.assert_production_ready()
    await init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.DEBUG,
        lifespan=lifespan,
        # Hide API docs in production
        docs_url="/api/docs" if settings.DEBUG else None,
        redoc_url="/api/redoc" if settings.DEBUG else None,
        openapi_url="/api/openapi.json" if settings.DEBUG else None,
    )

    # Attach rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS — explicit origin whitelist from environment variable
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # Global handler — all AppExceptions return clean JSON (no stack traces)
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    # Static files
    if os.path.exists("frontend/static"):
        app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

    # API routers
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(admin.router)

    # Jinja2 frontend routes
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

    @app.get("/health")
    async def health() -> dict:
        # Minimal response — never expose debug flag or internals
        return {"status": "ok"}

    return app


app = create_app()
