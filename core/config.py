from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_KEY_FRAGMENTS = ("dev-", "change", "secret", "example", "placeholder", "your-")


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
    SECRET_KEY: str = "dev-secret-key-change-in-production-min-32-chars"

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

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        # Only enforce in non-trivial values; let dev defaults pass with a warning
        lower = v.lower()
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
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
