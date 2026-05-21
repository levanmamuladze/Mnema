"""
Unit tests for core/config.Settings — specifically the SECRET_KEY validator.

We construct Settings() directly (bypassing the @lru_cache `get_settings`)
to test the validator in isolation. The .env file is disabled per-call via
`_env_file=None` so tests are not affected by what's on disk.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import Settings


# A long key with no insecure fragments. Used by every passing test below.
STRONG_KEY = "Z9pQrT2kJ7mNvHfA4bXsW8yLcUoEi3RgKqMaBdFhPjNwVxZtCu"


def _make(secret_key: str, **overrides) -> Settings:
    """Construct Settings with _env_file disabled so on-disk .env can't bleed in."""
    return Settings(_env_file=None, SECRET_KEY=secret_key, **overrides)


# ── Length check ─────────────────────────────────────────────────────────────


def test_rejects_short_key():
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _make("short")


def test_accepts_strong_key():
    s = _make(STRONG_KEY)
    assert s.SECRET_KEY == STRONG_KEY


# ── Insecure-fragment check ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_key",
    [
        # 32+ chars but contains a forbidden fragment.
        "dev-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "PleaseChangeMeAAAAAAAAAAAAAAAAAA",
        "this-is-a-secret-aaaaaaaaaaaaaaaa",
        "example-key-aaaaaaaaaaaaaaaaaaaaa",
        "placeholder-aaaaaaaaaaaaaaaaaaaaa",
        "your-key-here-aaaaaaaaaaaaaaaaaaa",
    ],
)
def test_rejects_insecure_fragments(bad_key):
    with pytest.raises(ValidationError, match="insecure fragment"):
        _make(bad_key)


def test_escape_hatch_warns_instead_of_raising(monkeypatch):
    """ALLOW_INSECURE_SECRET_KEY=1 downgrades the error to a RuntimeWarning."""
    monkeypatch.setenv("ALLOW_INSECURE_SECRET_KEY", "1")
    with pytest.warns(RuntimeWarning, match="insecure fragment"):
        s = _make("dev-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert s.SECRET_KEY.startswith("dev-")


def test_escape_hatch_does_not_bypass_length_check(monkeypatch):
    """Even with the escape hatch set, a short key must still be rejected."""
    monkeypatch.setenv("ALLOW_INSECURE_SECRET_KEY", "1")
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _make("short")


# ── assert_production_ready still works ──────────────────────────────────────


def test_assert_production_ready_passes_with_strong_key():
    s = _make(STRONG_KEY, DEBUG=False)
    # Should not raise.
    s.assert_production_ready()


def test_assert_production_ready_skipped_when_debug(monkeypatch):
    """DEBUG mode skips the production gate (unchanged from previous behavior)."""
    monkeypatch.setenv("ALLOW_INSECURE_SECRET_KEY", "1")
    with pytest.warns(RuntimeWarning):
        s = _make("dev-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", DEBUG=True)
    # DEBUG=True: assert_production_ready is a no-op.
    s.assert_production_ready()
