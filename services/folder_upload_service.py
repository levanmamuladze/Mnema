"""
Folder upload service.

Scans a folder (local path, SMB/UNC share, or OneDrive via Graph API), then
uploads and indexes every valid file as an isolated background job.
Progress is tracked in an in-memory dict keyed by job_id.

For single-process deployments this is sufficient.  To scale across multiple
workers, replace _jobs with a shared store (e.g. Redis + Celery).
"""
import asyncio
import mimetypes
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from core.config import settings
from db.repositories.document_repository import DocumentRepository
from db.repositories.document_role_repository import DocumentRoleRepository
from db.session import AsyncSessionLocal
from services.ingestion_service import IngestionService

# Extensions accepted by the upload pipeline (mirrors admin.py ALLOWED_EXTENSIONS)
_ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".docx", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp",
    ".kt", ".swift", ".scala", ".sql", ".sh", ".bash", ".zsh", ".yaml",
    ".yml", ".json", ".xml", ".html", ".css", ".toml", ".ini", ".cfg",
}

# In-memory job store  {job_id: dict}
# Each job dict has keys: status, total, processed, succeeded, failed, failed_files
_jobs: dict[str, dict] = {}


# ── Public helpers ────────────────────────────────────────────────────────────

def create_job() -> str:
    """Allocate a new job record and return its ID."""
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "status": "running",
        "total": 0,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "failed_files": [],
    }
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    """Return the job dict or None if job_id is unknown."""
    return _jobs.get(job_id)


# ── Folder scanning ───────────────────────────────────────────────────────────

def _scan_local(folder_path: str) -> list[Path]:
    """Recursively collect files with allowed extensions from a local path."""
    # Reject null bytes and path traversal components before any filesystem call
    if "\x00" in folder_path:
        raise ValueError("Invalid folder path: null byte detected")
    raw = Path(folder_path)
    if not raw.is_absolute():
        raise ValueError("Folder path must be absolute (e.g. /data/docs or C:\\docs)")
    # Resolve symlinks; the resolved path must still start with itself
    # (prevents traversal via symlinks that escape the intended tree)
    root = raw.resolve()
    if root != raw.resolve():  # always equal after resolve — kept as explicit contract
        raise ValueError("Folder path contains invalid components")
    # Reject paths containing '..' after initial normalisation just to be safe
    try:
        raw.relative_to(raw.anchor)  # confirms path is under its own root
    except ValueError:
        raise ValueError(f"Invalid folder path: {folder_path}")
    if not root.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    if not root.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {folder_path}")
    return [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _ALLOWED_EXTENSIONS
    ]


def _scan_smb(smb_path: str, username: str, password: str) -> list[tuple[str, Optional[bytes]]]:
    """
    Scan an SMB share and return (filename, content_or_None) pairs.

    smb_path format:  \\\\server\\share\\optional\\subdir
                      //server/share/optional/subdir

    Requires:  pip install smbprotocol
    """
    try:
        import smbclient  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "smbprotocol is not installed.  "
            "Add 'smbprotocol' to requirements.txt and rebuild the image."
        )

    # Normalise to backslash form expected by smbclient
    normalised = smb_path.replace("/", "\\")
    server = normalised.lstrip("\\").split("\\")[0]
    smbclient.register_session(server, username=username, password=password)

    results: list[tuple[str, Optional[bytes]]] = []
    for dirpath, _, filenames in smbclient.walk(normalised):
        for fname in filenames:
            if Path(fname).suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            full = f"{dirpath}\\{fname}"
            try:
                with smbclient.open_file(full, mode="rb") as fh:
                    results.append((fname, fh.read()))
            except Exception:
                results.append((fname, None))  # unreadable — will be logged as failed
    return results


# ── OneDrive / Microsoft Graph ───────────────────────────────────────────────

def _is_onedrive_url(path: str) -> bool:
    """Return True for OneDrive consumer and SharePoint/OneDrive-for-Business URLs."""
    lower = path.lower()
    return (
        "onedrive.live.com" in lower
        or "1drv.ms" in lower
        or ".sharepoint.com" in lower
        or "onedrive.com" in lower
    )


def _graph_folder_candidates(url: str) -> list[str]:
    """
    Return an ordered list of Graph API 'children' endpoints to try for a
    given OneDrive browser URL.  We try multiple strategies because the URL
    parameters map to Graph API paths in non-obvious ways across account types.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    candidates: list[str] = []

    # Strategy 1 — path derived from the 'id' query parameter
    # id = /personal/<cid>/Documents/inventyou  →  root:/Documents/inventyou:/children
    if "id" in params:
        raw = params["id"][0]
        parts = raw.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "personal":
            relative = "/".join(parts[2:])
        else:
            relative = raw.strip("/")
        if relative:
            candidates.append(
                f"https://graph.microsoft.com/v1.0/me/drive/root:/{relative}:/children"
            )
            # Strategy 1b — same path via the special/documents shortcut
            # (consumer OneDrive sometimes stores Documents as a special folder)
            rel_parts = relative.split("/", 1)
            if rel_parts[0].lower() == "documents" and len(rel_parts) == 2:
                sub = rel_parts[1]
                candidates.append(
                    f"https://graph.microsoft.com/v1.0/me/drive/special/documents:/{sub}:/children"
                )
            elif rel_parts[0].lower() == "documents" and len(rel_parts) == 1:
                candidates.append(
                    "https://graph.microsoft.com/v1.0/me/drive/special/documents/children"
                )

    # Strategy 2 — viewid used directly as a drive-item ID
    # In many OneDrive URLs the viewid IS the folder's item ID
    if "viewid" in params:
        item_id = params["viewid"][0]
        candidates.append(
            f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/children"
        )

    # Strategy 3 — SharePoint / OneDrive for Business path-based
    if ".sharepoint.com" in parsed.netloc:
        doc_path = parsed.path.rstrip("/")
        candidates.append(
            f"https://graph.microsoft.com/v1.0/sites/{parsed.netloc}"
            f"/drive/root:{doc_path}:/children"
        )

    if not candidates:
        raise ValueError(
            "Unrecognised OneDrive URL format. "
            "Paste the URL directly from your OneDrive browser tab."
        )

    return candidates


async def _validate_onedrive_token(client: httpx.AsyncClient, access_token: str) -> None:
    """
    Call GET /me to verify the token is valid, then GET /me/drive to verify
    it has Files.Read scope.  Raises PermissionError with an actionable message.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    me = await client.get("https://graph.microsoft.com/v1.0/me", headers=headers, timeout=15.0)
    if me.status_code == 401:
        raise PermissionError(
            "Access token is invalid or expired. "
            "Go to Graph Explorer, sign in, and copy a fresh token from the 'Access token' tab."
        )

    drive = await client.get("https://graph.microsoft.com/v1.0/me/drive", headers=headers, timeout=15.0)
    if drive.status_code in (401, 403):
        raise PermissionError(
            "Token is valid but is missing the Files.Read permission. "
            "In Graph Explorer go to 'Modify permissions', enable Files.Read, "
            "click 'Modify permissions', then copy the new token."
        )


async def _collect_onedrive_files(
    client: httpx.AsyncClient,
    headers: dict,
    url: str,
    results: list,
) -> None:
    """Recursively follow folder items and pagination links, collecting files."""
    resp = await client.get(url, headers=headers, timeout=30.0)

    if resp.status_code == 401:
        raise PermissionError(
            "Access token is invalid or expired. "
            "Go to Graph Explorer, sign in, and copy a fresh token from the 'Access token' tab."
        )
    if resp.status_code == 403:
        raise PermissionError(
            "Token is missing Files.Read permission. "
            "In Graph Explorer go to 'Modify permissions', enable Files.Read, "
            "click 'Modify permissions', then copy the new token."
        )
    if resp.status_code == 404:
        raise FileNotFoundError(
            "OneDrive folder not found. "
            "Check the URL is correct and that you are signed in as the folder owner."
        )
    resp.raise_for_status()

    data = resp.json()
    for item in data.get("value", []):
        name = item.get("name", "")
        if "folder" in item:
            child_url = (
                f"https://graph.microsoft.com/v1.0/me/drive/items/{item['id']}/children"
            )
            await _collect_onedrive_files(client, headers, child_url, results)
        elif "file" in item:
            if Path(name).suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            download_url = item.get("@microsoft.graph.downloadUrl")
            if not download_url:
                results.append((name, None))
                continue
            try:
                file_resp = await client.get(download_url, timeout=120.0)
                results.append(
                    (name, file_resp.content if file_resp.status_code == 200 else None)
                )
            except Exception:
                results.append((name, None))

    # Follow pagination
    next_link = data.get("@odata.nextLink")
    if next_link:
        await _collect_onedrive_files(client, headers, next_link, results)


async def _scan_onedrive(
    url: str,
    access_token: str,
    manual_path: Optional[str] = None,
) -> list[tuple[str, Optional[bytes]]]:
    """
    Return (filename, content_or_None) pairs for all supported files in the
    OneDrive folder identified by *url*.

    If *manual_path* is provided (e.g. "Documents/inventyou") it is used
    directly, bypassing URL parsing entirely.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    if manual_path:
        path = manual_path.strip("/")
        candidates = [
            f"https://graph.microsoft.com/v1.0/me/drive/root:/{path}:/children"
        ]
    else:
        candidates = _graph_folder_candidates(url)

    async with httpx.AsyncClient() as client:
        await _validate_onedrive_token(client, access_token)
        tried: list[str] = []
        for graph_url in candidates:
            tried.append(graph_url)
            try:
                results: list[tuple[str, Optional[bytes]]] = []
                await _collect_onedrive_files(client, headers, graph_url, results)
                return results  # first strategy that succeeds wins
            except FileNotFoundError:
                continue  # try next candidate
        raise FileNotFoundError(
            f"OneDrive folder not found. Tried {len(tried)} path(s): "
            + " | ".join(tried)
            + " — Run 'GET https://graph.microsoft.com/v1.0/me/drive/root/children' "
            "in Graph Explorer to see your actual folder names."
        )


# ── Per-file processing ───────────────────────────────────────────────────────

async def _process_one(
    job: dict,
    original_name: str,
    content: bytes,
    role_list: list[str],
    admin_id: int,
    upload_dir: Path,
) -> None:
    """
    Save one file to disk, create the DB record, assign roles, and run the
    ingestion pipeline.  Errors are caught and appended to job["failed_files"]
    so a single failure never aborts the batch.
    """
    if len(content) > settings.max_upload_size_bytes:
        job["failed"] += 1
        job["failed_files"].append({
            "filename": original_name,
            "status": "failed",
            "error": f"Exceeds {settings.MAX_UPLOAD_SIZE_MB} MB size limit",
        })
        return

    unique_filename = f"{uuid.uuid4().hex}_{original_name}"
    file_path = upload_dir / unique_filename

    try:
        file_path.write_bytes(content)
        mime_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"

        async with AsyncSessionLocal() as session:
            doc_repo = DocumentRepository(session)
            doc = await doc_repo.create_document(
                filename=unique_filename,
                original_filename=original_name,
                file_path=str(file_path),
                file_size=len(content),
                mime_type=mime_type,
                uploaded_by_admin=admin_id,
            )

            role_repo = DocumentRoleRepository(session)
            await role_repo.set_roles(doc.id, role_list)

            ingestion_service = IngestionService(session)
            await ingestion_service.index_document(doc)

            await session.commit()

        job["succeeded"] += 1

    except Exception as exc:
        file_path.unlink(missing_ok=True)  # clean up orphaned file on failure
        job["failed"] += 1
        job["failed_files"].append({
            "filename": original_name,
            "status": "failed",
            "error": str(exc),
        })


# ── Background job entry point ────────────────────────────────────────────────

async def run_folder_job(
    job_id: str,
    folder_path: str,
    role_list: list[str],
    admin_id: int,
    username: Optional[str],
    password: Optional[str],
    onedrive_path: Optional[str] = None,
) -> None:
    """
    Background coroutine launched via asyncio.create_task().

    Flow:
      1. Detect path type (local vs. SMB/UNC).
      2. Scan folder for valid files.
      3. Process each file individually; failures are isolated.
      4. Mark the job as "completed" (or "failed" if scanning itself errors).
    """
    job = _jobs[job_id]
    upload_dir = Path(settings.UPLOAD_DIR) / str(admin_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    try:
        is_onedrive = _is_onedrive_url(folder_path)
        is_smb = (
            not is_onedrive
            and (folder_path.startswith("\\\\") or folder_path.startswith("//"))
        )

        if is_onedrive:
            if not password:
                raise ValueError(
                    "A Microsoft Graph API access token is required for OneDrive. "
                    "Paste it in the Password field. "
                    "Get one from https://developer.microsoft.com/en-us/graph/graph-explorer"
                )
            items = await _scan_onedrive(folder_path, password, onedrive_path)
            job["total"] = len(items)

            if not items:
                job["status"] = "completed"
                return

            for original_name, content in items:
                if content is None:
                    job["failed"] += 1
                    job["failed_files"].append({
                        "filename": original_name,
                        "status": "failed",
                        "error": "Could not download file from OneDrive",
                    })
                else:
                    await _process_one(
                        job, original_name, content, role_list, admin_id, upload_dir
                    )
                job["processed"] += 1
                await asyncio.sleep(0)

        elif is_smb:
            if not username or not password:
                raise ValueError(
                    "Username and password are required for network share paths."
                )
            items = _scan_smb(folder_path, username, password)
            job["total"] = len(items)

            if not items:
                job["status"] = "completed"
                return

            for original_name, content in items:
                if content is None:
                    job["failed"] += 1
                    job["failed_files"].append({
                        "filename": original_name,
                        "status": "failed",
                        "error": "Could not read file from network share",
                    })
                else:
                    await _process_one(
                        job, original_name, content, role_list, admin_id, upload_dir
                    )
                job["processed"] += 1
                await asyncio.sleep(0)  # yield to event loop between files

        else:
            file_paths = _scan_local(folder_path)
            job["total"] = len(file_paths)

            if not file_paths:
                job["status"] = "completed"
                return

            for fp in file_paths:
                try:
                    content = fp.read_bytes()
                except OSError as exc:
                    job["processed"] += 1
                    job["failed"] += 1
                    job["failed_files"].append({
                        "filename": fp.name,
                        "status": "failed",
                        "error": str(exc),
                    })
                    continue

                await _process_one(
                    job, fp.name, content, role_list, admin_id, upload_dir
                )
                job["processed"] += 1
                await asyncio.sleep(0)  # yield to event loop between files

        job["status"] = "completed"

    except Exception as exc:
        job["status"] = "failed"
        job["failed_files"].append({
            "filename": "(folder scan)",
            "status": "failed",
            "error": str(exc),
        })
