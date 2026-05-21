"""
Security tests for services/folder_upload_service._scan_local.

Covers:
  - Null-byte rejection
  - Non-absolute path rejection
  - Non-existent path rejection
  - Non-directory rejection
  - Empty allowlist = endpoint disabled
  - Path outside allowlist rejected
  - Path inside allowlist accepted
  - Extension filtering
  - Symlink escape via per-file resolve
"""
from __future__ import annotations

import os
import pytest

from services import folder_upload_service as svc


@pytest.fixture
def allowlisted_root(tmp_path, monkeypatch):
    """
    Build a temp directory and make the live `settings` object treat it as
    the only allowed upload root. We patch the property (since settings is
    a singleton @lru_cache) rather than reconstructing Settings.
    """
    monkeypatch.setattr(
        svc.settings,
        "ALLOWED_UPLOAD_ROOTS",
        str(tmp_path),
        raising=False,
    )
    return tmp_path


# ── Input validation ─────────────────────────────────────────────────────────


def test_rejects_null_byte():
    with pytest.raises(ValueError, match="null byte"):
        svc._scan_local("/tmp/foo\x00bar")


def test_rejects_relative_path():
    with pytest.raises(ValueError, match="absolute"):
        svc._scan_local("relative/path")


def test_rejects_nonexistent_path(tmp_path):
    missing = tmp_path / "definitely-not-there"
    with pytest.raises(FileNotFoundError):
        svc._scan_local(str(missing))


def test_rejects_non_directory(tmp_path, allowlisted_root):
    f = tmp_path / "a-file.txt"
    f.write_text("hi")
    with pytest.raises(NotADirectoryError):
        svc._scan_local(str(f))


# ── Allowlist enforcement ────────────────────────────────────────────────────


def test_empty_allowlist_blocks_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(svc.settings, "ALLOWED_UPLOAD_ROOTS", "", raising=False)
    with pytest.raises(PermissionError, match="disabled"):
        svc._scan_local(str(tmp_path))


def test_path_outside_allowlist_rejected(tmp_path, monkeypatch):
    """allowlist points at tmp_path/a, request points at tmp_path/b."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.setattr(svc.settings, "ALLOWED_UPLOAD_ROOTS", str(allowed), raising=False)

    with pytest.raises(PermissionError, match="not under any entry"):
        svc._scan_local(str(outside))


def test_path_inside_allowlist_accepted(allowlisted_root):
    """Smoke test: an allowed empty directory returns []."""
    assert svc._scan_local(str(allowlisted_root)) == []


def test_nested_path_inside_allowlist_accepted(allowlisted_root):
    nested = allowlisted_root / "sub" / "dir"
    nested.mkdir(parents=True)
    assert svc._scan_local(str(nested)) == []


# ── File collection ──────────────────────────────────────────────────────────


def test_returns_only_allowed_extensions(allowlisted_root):
    (allowlisted_root / "ok.txt").write_text("hi")
    (allowlisted_root / "skip.exe").write_bytes(b"\x00")
    (allowlisted_root / "another.md").write_text("# hi")
    (allowlisted_root / "image.png").write_bytes(b"PNG")

    out = svc._scan_local(str(allowlisted_root))
    names = sorted(p.name for p in out)
    assert names == ["another.md", "ok.txt"]


def test_walks_subdirectories(allowlisted_root):
    nested = allowlisted_root / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "deep.md").write_text("# deep")
    (allowlisted_root / "top.txt").write_text("top")

    names = sorted(p.name for p in svc._scan_local(str(allowlisted_root)))
    assert names == ["deep.md", "top.txt"]


# ── Symlink escape (POSIX only — Windows symlinks need elevated rights) ──────


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires admin on Windows")
def test_symlink_escaping_root_is_filtered(tmp_path, monkeypatch):
    """A symlink inside the scanned tree pointing OUT of it must be skipped."""
    inside = tmp_path / "scan"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")

    # Allowlist permits `inside`, not `outside`.
    monkeypatch.setattr(svc.settings, "ALLOWED_UPLOAD_ROOTS", str(inside), raising=False)

    # Create a symlink inside `inside` that points to a file outside.
    link = inside / "looks_innocent.txt"
    link.symlink_to(outside / "secret.txt")

    # The scan must NOT yield the symlink, because it resolves outside `inside`.
    out = svc._scan_local(str(inside))
    assert out == []
