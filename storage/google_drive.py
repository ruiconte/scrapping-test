"""Uploads completed prospect CSV batches to Google Drive (Fableya/Prospects).

Uses the official Drive v3 API with OAuth 2.0, scoped to
drive.file (access only to files/folders this app itself creates —
never the user's whole Drive).

Every public function catches its own errors, logs them, and returns a
plain success/failure result — it never raises. A Drive outage, quota
error, or OAuth hiccup must never affect local data or interrupt the
scraping session that's calling this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from config import BASE_DIR, PROSPECTS_EXPORT_DIR
from utils.logging import get_logger

log = get_logger()

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"
UPLOAD_STATE_PATH = PROSPECTS_EXPORT_DIR / "drive_upload_state.json"
DRIVE_FOLDER_PATH = ["Fableya", "Prospects"]

_service = None
_folder_id_cache: dict[str, str] = {}


def _get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except (ValueError, OSError) as exc:
            log.info(f"[DRIVE] Could not read token.json ({exc}); re-authenticating.")
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as exc:
            log.info(f"[DRIVE] Token refresh failed ({exc}); re-authenticating.")

    if not CREDENTIALS_PATH.exists():
        raise RuntimeError(
            f"{CREDENTIALS_PATH.name} not found at the project root. Download an OAuth "
            "client (Desktop app) from Google Cloud Console first."
        )

    log.info("[DRIVE] Opening Google authorization page in your browser...")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    log.info("[DRIVE] Authorization complete — token saved to token.json.")
    return creds


def _get_service():
    global _service
    if _service is not None:
        return _service
    from googleapiclient.discovery import build

    creds = _get_credentials()
    _service = build("drive", "v3", credentials=creds)
    return _service


def _load_upload_state() -> dict:
    if not UPLOAD_STATE_PATH.exists():
        return {}
    try:
        return json.loads(UPLOAD_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_upload_state(state: dict) -> None:
    UPLOAD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = UPLOAD_STATE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, UPLOAD_STATE_PATH)  # atomic, same pattern as prospect_exporter


def _find_or_create_folder(name: str, parent_id: Optional[str]) -> str:
    service = _get_service()
    query = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    query += f" and '{parent_id}' in parents" if parent_id else " and 'root' in parents"

    results = service.files().list(q=query, fields="files(id, name)", spaces="drive").execute()
    existing = results.get("files", [])
    if existing:
        return existing[0]["id"]

    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = service.files().create(body=metadata, fields="id").execute()
    log.info(f"[DRIVE] Created folder '{name}' on Drive.")
    return folder["id"]


def _get_target_folder_id() -> str:
    """Finds (or creates, once) Fableya/Prospects, caching the id both in
    memory and in the upload-state file so it's never recreated."""
    cache_key = "/".join(DRIVE_FOLDER_PATH)
    if cache_key in _folder_id_cache:
        return _folder_id_cache[cache_key]

    state = _load_upload_state()
    cached = state.get("_folder_ids", {}).get(cache_key)
    if cached:
        _folder_id_cache[cache_key] = cached
        return cached

    parent_id = None
    for name in DRIVE_FOLDER_PATH:
        parent_id = _find_or_create_folder(name, parent_id)

    state.setdefault("_folder_ids", {})[cache_key] = parent_id
    _save_upload_state(state)
    _folder_id_cache[cache_key] = parent_id
    return parent_id


def is_uploaded(filename: str) -> bool:
    return bool(_load_upload_state().get(filename, {}).get("uploaded"))


def authenticate() -> bool:
    """Triggers (or silently reuses) Google OAuth. Useful to run once,
    deliberately, before an unattended session — so the browser consent
    screen doesn't pop up unexpectedly mid-scrape."""
    try:
        _get_credentials()
        return True
    except Exception as exc:
        log.info(f"[ERROR] [DRIVE] Authentication failed: {exc}")
        return False


def upload_file(file_path: Path) -> bool:
    """Uploads one CSV batch file to Fableya/Prospects on Google Drive.

    Returns True once the upload is confirmed successful (and also True,
    without re-uploading, if this file was already uploaded before).
    Returns False on any failure — network, OAuth, quota, Drive outage,
    etc. — leaving the local file and upload state untouched so it's
    retried on the next run.
    """
    file_path = Path(file_path)
    filename = file_path.name

    if is_uploaded(filename):
        return True

    if not file_path.exists():
        log.info(f"[ERROR] [DRIVE] Skipping upload — {filename} no longer exists locally.")
        return False

    try:
        from googleapiclient.http import MediaFileUpload

        log.info(f"[DRIVE] Uploading {filename}...")
        folder_id = _get_target_folder_id()
        service = _get_service()

        metadata = {"name": filename, "parents": [folder_id]}
        media = MediaFileUpload(str(file_path), mimetype="text/csv", resumable=True)
        uploaded = service.files().create(body=metadata, media_body=media, fields="id").execute()
        file_id = uploaded.get("id")
        if not file_id:
            raise RuntimeError("Drive API did not return a file id.")

        state = _load_upload_state()
        state[filename] = {"uploaded": True, "drive_file_id": file_id}
        _save_upload_state(state)
        log.info(f"[DRIVE] Upload complete — file_id={file_id}")
        return True

    except Exception as exc:
        log.info(f"[ERROR] [DRIVE] Upload failed for {filename}: {exc}. Local file kept, will retry later.")
        return False


def retry_pending_uploads() -> None:
    """Call at startup: re-attempts any completed batch CSV not yet marked
    uploaded (e.g. after a crash, an offline period, or a Drive outage)."""
    if not PROSPECTS_EXPORT_DIR.exists():
        return
    pending = [f for f in sorted(PROSPECTS_EXPORT_DIR.glob("prospects_*.csv")) if not is_uploaded(f.name)]
    if not pending:
        return
    log.info(f"[DRIVE] Found {len(pending)} previously-unuploaded batch(es), retrying...")
    for f in pending:
        upload_file(f)
