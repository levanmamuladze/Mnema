import os
import warnings
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_KEY_FRAGMENTS = ("dev-", "change", "secret", "example", "placeholder", "your-")

# Set =1 to bypass the fragment check for local prototyping; never in prod.
_ALLOW_INSECURE_ENV = "ALLOW_INSECURE_SECRET_KEY"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    APP_NAME: str = "Mnema"
    DEBUG: bool = False

    # Security — no default: construction fails if unset (a default would
    # silently pass the length check and give false security).
    SECRET_KEY: str

    # CORS — comma-separated allowed origins; set explicitly in production.
    ALLOWED_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"

    # Cookies — set COOKIE_SECURE=true behind HTTPS in production
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./rag_app.db"

    # JWT
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # Must match the model the Compose `ollama-init` puller fetches
    # (docker-compose.yml OLLAMA_MODEL default). If the app default and the
    # puller default disagree, /api/chat 404s on a fresh stack because the
    # app asks for a model that was never pulled.
    OLLAMA_MODEL: str = "qwen2.5:14b"

    # Embeddings / Reranking
    EMBED_MODEL: str = "BAAI/bge-base-en-v1.5"
    EMBED_DIMENSION: int = 768
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANKER_ENABLED: bool = True

    # RAG
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    TOP_K_RETRIEVAL: int = 5
    RETRIEVAL_CANDIDATES: int = 20

    # File upload
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # Rate limiting — slowapi storage backend. "memory://" is per-process and
    # does NOT share counts across workers; point it at Redis
    # ("redis://redis:6379/0") once you run more than one worker.
    RATE_LIMIT_STORAGE_URI: str = "memory://"

    # LLM backpressure — max number of generations streaming from Ollama at
    # once. Requests beyond this queue (await a slot) instead of piling onto a
    # single GPU and degrading every response. Size to your hardware: ~2–4 for
    # one consumer GPU.
    MAX_CONCURRENT_GENERATIONS: int = 4

    # Observability
    METRICS_ENABLED: bool = True
    # If set, GET /metrics requires `Authorization: Bearer <token>`. Leave empty
    # only when /metrics is unreachable from outside (e.g. scraped over the
    # internal network and never proxied by Caddy).
    METRICS_TOKEN: str = ""
    # Optional Sentry error tracking. Empty = disabled (no-op). Set a DSN and
    # `pip install sentry-sdk` to enable.
    SENTRY_DSN: str = ""

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        """
        Always-on strength check.

        Rules:
          1. Must be at least 32 characters.
          2. Must not contain any insecure fragment ("dev-", "change", "secret",
             "example", "placeholder", "your-") — these are the substrings used
             in every example/template SECRET_KEY we've ever shipped.

        Escape hatch: set ALLOW_INSECURE_SECRET_KEY=1 in your shell. The
        validator will then emit a warning instead of raising. Intended for
        local prototyping only — production must never set this.
        """
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")

        lower = v.lower()
        insecure = next((frag for frag in _INSECURE_KEY_FRAGMENTS if frag in lower), None)
        if insecure is not None:
            msg = (
                f"SECRET_KEY contains the insecure fragment {insecure!r}. "
                "Generate a strong key with: "
                "python -c 'import secrets; print(secrets.token_urlsafe(48))'"
            )
            if os.environ.get(_ALLOW_INSECURE_ENV) == "1":
                warnings.warn(msg, RuntimeWarning, stacklevel=2)
            else:
                raise ValueError(msg)
        return v

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        from urllib.parse import urlparse
        return urlparse(self.DATABASE_URL).scheme.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        from urllib.parse import urlparse
        return urlparse(self.DATABASE_URL).scheme.startswith("postgresql")

    def assert_production_ready(self) -> None:
        """Call at startup to hard-fail if insecure defaults are still in use."""
        if not self.DEBUG:
            lower = self.SECRET_KEY.lower()
            if any(frag in lower for frag in _INSECURE_KEY_FRAGMENTS):
                raise RuntimeError(
                    "SECRET_KEY contains an insecure default value. "
                    "Set a strong random SECRET_KEY in your environment before deploying."
                )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
