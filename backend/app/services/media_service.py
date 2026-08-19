"""
media_service.py — profile-photo storage.

Photos captured at registration are the user's DP and feed the client
pre-registration payload (``patientProfilePhotoURL``). Storage is a
local directory today (``settings.MEDIA_DIR``, served at ``/media/*``
by this API); a dedicated media server later is a config change —
set ``MEDIA_BASE_URL`` and the resolved URLs move, because the DB only
ever stores paths relative to the media root.

Security notes
--------------
- Every photo is **re-encoded** to a fresh JPEG before it touches disk:
  whatever bytes arrived (EXIF, GPS tags, appended payloads, polyglot
  files) are discarded — only decoded pixels survive.
- Filenames are derived from the person's random ``person_guid``, never
  the sequential integer id, so photo URLs cannot be enumerated.
- ``MEDIA_DIR`` is gitignored (biometric PII). Serving is currently
  unauthenticated like the rest of the API — the feat/auth-rbac merge
  must gate ``/media`` alongside the other PII surfaces.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from backend.app.core.config import settings

logger = logging.getLogger("backend.media")

PROFILE_PHOTO_SUBDIR = "profile_photos"
_JPEG_QUALITY = 90


def media_root() -> Path:
    """Absolute media root; created on first use."""
    root = Path(settings.MEDIA_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_profile_photo(person_guid: Optional[str], image_bgr: np.ndarray) -> Optional[str]:
    """Re-encode ``image_bgr`` as JPEG and store it as the profile photo.

    Returns the path *relative to the media root* (what goes in
    ``users.photo_url``), or ``None`` when encoding/writing fails —
    photo storage is best-effort and must never fail a registration.
    """
    name = person_guid or str(uuid.uuid4())
    rel_path = f"{PROFILE_PHOTO_SUBDIR}/{name}.jpg"
    try:
        ok, buf = cv2.imencode(
            ".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY]
        )
        if not ok:
            logger.warning("Profile photo encode failed (guid=%s)", person_guid)
            return None
        dest = media_root() / PROFILE_PHOTO_SUBDIR
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"{name}.jpg"
        # Write-then-rename so a crash mid-write never leaves a torn file
        # at the served path.
        tmp = target.with_suffix(".jpg.tmp")
        tmp.write_bytes(buf.tobytes())
        os.replace(tmp, target)
        return rel_path
    except Exception as exc:
        logger.warning("Profile photo save failed (guid=%s): %s", person_guid, exc)
        return None


def resolve_media_url(rel_path: Optional[str]) -> Optional[str]:
    """Turn a stored relative path into the URL clients should use.

    With ``MEDIA_BASE_URL`` set (future media server) the URL points
    there; otherwise it is the API's own ``/media/*`` mount (relative,
    so the SPA resolves it against its API origin).
    """
    if not rel_path:
        return None
    base = settings.MEDIA_BASE_URL.rstrip("/")
    if base:
        return f"{base}/{rel_path}"
    return f"/media/{rel_path}"
