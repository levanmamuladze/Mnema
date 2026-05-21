"""
Shared test fixtures and import-time setup.

Why the env-var dance:
    `core.config` reads `.env` at module import. To make tests deterministic
    (independent of whatever `.env` happens to be on disk), we set safe
    overrides BEFORE any application module is imported. pytest loads
    conftest.py before any test module, so this is the right hook.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the repository root is importable as the top-level package source.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Deterministic, validator-passing values for tests.
# These must be set BEFORE `core.config` (or anything that imports it) loads.
os.environ.setdefault(
    "SECRET_KEY",
    # 48 random-ish chars, no insecure-fragment substrings.
    "Z9pQrT2kJ7mNvHfA4bXsW8yLcUoEi3RgKqMaBdFhPjNwVxZtCu",
)
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
