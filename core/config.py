import os
import warnings
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_KEY_FRAGMENTS = ("dev-", "change", "secret", "example", "placeholder", "your-")

# Set ALLOW_INSECURE_SECRET_KEY=1 in your shell to bypass the fragment check
# during local prototyping. Production deployments must never set this.
_ALLOW_INSECURE_ENV = "ALLOW_INSECURE_SECRET_KEY"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    APP_NAME: str = "Onboarding AI"
    DEBUG: bool = False

    # Security
    # No default: pydantic-settings will raise at construction time if SECRET_KEY
    # is missing from both the environment and the .env file. The previous default
    # ("dev-secret-key-change-in-production-min-32-chars") passed the length check
    # silently and gave a false sense of security.
    SECRET_KEY: str

    # CORS — comma-separated list of allowed origins, e.g. "https://app.example.com"
    # Defaults to localhost in development; must be set explicitly in production.
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
    OLLAMA_MODEL: str = "qwen2.5:7b"

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

    # Folder-upload allowlist. Comma-separated absolute paths under which the
    # /admin/documents/upload-folder endpoint is permitted to scan. Defaults to
    # empty, which disables that endpoint entirely (fail-closed). Without this
    # an admin could scan /etc, /root, /proc or other container-readable paths.
    # Example (Docker): ALLOWED_UPLOAD_ROOTS=/app/uploads,/mnt/shared
    ALLOWED_UPLOAD_ROOTS: str = ""

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
    def allowed_upload_roots(self) -> list["Path"]:
        """
        Parsed view of ALLOWED_UPLOAD_ROOTS. Returns resolved absolute Paths.
        Empty list means folder uploads are disabled (fail-closed).
        Lazy import: Path isn't needed at module-import time.
        """
        from pathlib import Path

        roots: list[Path] = []
        for raw in self.ALLOWED_UPLOAD_ROOTS.split(","):
            stripped = raw.strip()
            if not stripped:
                continue
            roots.append(Path(stripped).resolve())
        return roots

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
