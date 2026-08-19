"""
face_service.py — Business logic for registration and recognition.

Architectural role
------------------
This module is the **only** place in the backend that knows how to
stitch together the engine pipeline (detect → align → embed → match)
with the persistence layer (PostgreSQL + FAISS). API routes call these
two functions and do nothing else.

    ┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐
    │  API routes  │───▶│ face_service │───▶│ engine.pipeline.*    │
    │ (thin shim)  │     │ (this file)  │     │ pipeline_service     │
    └──────────────┘     └──────────────┘     │ PostgreSQL / FAISS   │
                                              └──────────────────────┘

Why key FAISS by ``user.id``
----------------------------
The relational database owns the canonical identity (``users.id``); the
FAISS index stores the 512-d embedding keyed by ``str(user.id)``. Two
benefits:

    * Renaming a user is a single UPDATE in Postgres — the FAISS index
      doesn't move.
    * Duplicate or colliding names in Postgres don't break the vector
      lookup, because the key is the primary-key integer, not free text.

On recognize we search FAISS, get back the id string, cast it to int,
and resolve the human-readable name via a Postgres query (cached
per-request to avoid hammering the DB when a frame has many faces).
"""

from __future__ import annotations

import logging
import random
import string
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy.orm import Session

from backend.app.db.models import Patient
from backend.app.services.media_service import resolve_media_url, save_profile_photo
from backend.app.services.pipeline_service import (
    face_db_write_lock,
    get_detector,
    get_face_db,
    get_recognizer,
    schedule_save,
)

# Import engine functions directly — no wrappers, no re-implementation.
# These are pure helpers (stateless) so it's safe to call them from any
# thread.
from backend.engine.pipeline.alignment import (
    align_center_crop,
    align_with_landmarks,
    normalize_pixels,
)
from backend.engine.pipeline.detection import _detect_faces, _filter_faces
from backend.engine.pipeline.embedding import extract_embedding

import backend.config as config

logger = logging.getLogger("backend.face_service")


# ── MRN generator ────────────────────────────────────────────────────────
def generate_mrn(length: int = 8) -> str:
    """Generate a random alphanumeric MRN (uppercase letters + digits)."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


def _create_user_with_mrn(
    name: str,
    db_session: Session,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Patient:
    """
    Insert a User row for a registration. For patients an MRN is
    auto-generated and the call retries on collision; for non-patient
    types (DOCTOR / EMPLOYEE / VISITOR / RELATIVE) MRN generation is
    skipped — only patients carry one.

    Args:
        name:         Display name (required).
        db_session:   Active SQLAlchemy session.
        extra_fields: Optional fields. ``user_type`` (if present)
                      decides whether MRN is generated. The remaining
                      keys are written verbatim onto the User row.

    Raises:
        Exception: if unable to allocate a unique MRN after 5 attempts
                   (patient path only).
    """
    extras = dict(extra_fields or {})
    user_type = extras.get("user_type", "PATIENT")

    if user_type == "PATIENT":
        # Existing patient flow — auto-generate MRN with retry on
        # collision.
        for _ in range(5):
            mrn = generate_mrn()
            user = Patient(name=name, mrn=mrn, **extras)
            try:
                db_session.add(user)
                db_session.commit()
                db_session.refresh(user)
                return user
            except Exception:
                db_session.rollback()
        raise Exception("Failed to generate unique MRN")

    # Non-patient: no MRN. Single insert, no retry loop.
    user = Patient(name=name, **extras)
    try:
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user
    except Exception:
        db_session.rollback()
        raise


# ── Custom exceptions ────────────────────────────────────────────────────
class FaceServiceError(Exception):
    """Base class for all face-service errors."""


class NoFaceDetectedError(FaceServiceError):
    """Raised when the detector finds no usable face in the input image."""


# ── Internal helpers ─────────────────────────────────────────────────────
def _detect_all(image: np.ndarray) -> List[Dict[str, Any]]:
    """
    Run the engine detector + filter and return a list of face dicts.

    The dict shape matches what the rest of the pipeline expects:
        {"bbox": [x1, y1, x2, y2], "confidence": float, "landmarks": ndarray|None}
    """
    detector = get_detector()
    raw = _detect_faces(detector, image)
    filtered = _filter_faces(raw, image.shape[0], image.shape[1])
    return filtered


def _align_face(
    image: np.ndarray,
    face: Dict[str, Any],
) -> np.ndarray:
    """
    Crop, align, and pixel-normalize a single detected face.

    Prefers landmark-based affine alignment when landmarks are present
    (accurate), and falls back to a centered square crop otherwise.
    """
    x1, y1, x2, y2 = face["bbox"]
    face_img = image[y1:y2, x1:x2].copy()

    target_size = config.ALIGNED_FACE_SIZE  # (112, 112)
    landmarks = face.get("landmarks")

    if landmarks is not None:
        aligned = align_with_landmarks(face_img, landmarks, face["bbox"], target_size)
    else:
        aligned = align_center_crop(face_img, target_size)

    return normalize_pixels(aligned)


def _largest_face(faces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pick the face with the largest bounding-box area.

    For registration we only accept ONE face per image — the subject is
    supposed to be centered and dominant. The largest box is the most
    likely candidate and ignores noisy background detections.
    """
    return max(
        faces,
        key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]),
    )


def _resolve_name(
    db_session: Session,
    user_id: int,
    cache: Dict[int, Optional[str]],
) -> Optional[str]:
    """
    Look up ``users.name`` by primary key, memoized for this request.

    Returns ``None`` when the id is not present in Postgres (stale FAISS
    entry — e.g. user was deleted relationally but the embedding wasn't
    removed). The caller should treat that as Unknown.
    """
    if user_id in cache:
        return cache[user_id]

    user = db_session.query(Patient).filter(Patient.id == user_id).first()
    name = user.name if user is not None else None
    cache[user_id] = name
    return name


# ── Legacy migration ────────────────────────────────────────────────────
def _migrate_legacy_identity(
    name_key: str,
    db_session: Session,
    face_db,
) -> Optional[int]:
    """
    Migrate a CLI-registered FAISS identity (keyed by name string) to the
    id-keyed system.

    1. Find or create a Patient row in Postgres for the given name.
    2. Reconstruct the embeddings stored under the name key in FAISS.
    3. Remove the old name-keyed entries and re-insert under str(user.id).
    4. Persist FAISS to disk.

    Returns the user.id on success, None on failure.
    """
    try:
        # Find existing user by name, or create one
        user = db_session.query(Patient).filter(Patient.name == name_key).first()
        if user is None:
            user = _create_user_with_mrn(name_key, db_session)
            logger.info(
                "Auto-created Patient id=%d mrn=%s for legacy identity '%s'",
                user.id, user.mrn, name_key,
            )

        new_key = str(user.id)

        # If FAISS already has entries under the new key, skip migration
        if face_db._count_for_identity(new_key) > 0:
            # Just remove the legacy entries — already migrated on a
            # previous call that was interrupted before cleanup.
            with face_db_write_lock:
                face_db.remove_identity(name_key)
            schedule_save()
            return user.id

        # Reconstruct embeddings from the old name-keyed entries
        import numpy as np
        old_indices = [
            i for i, n in enumerate(face_db._names) if n == name_key
        ]
        if not old_indices:
            return user.id

        embeddings = np.vstack([
            face_db._index.reconstruct(i) for i in old_indices
        ]).astype(np.float32)

        # Atomic swap: remove old, insert new, async save
        with face_db_write_lock:
            face_db.remove_identity(name_key)
            for emb in embeddings:
                face_db.register(new_key, emb)
        schedule_save()

        logger.info(
            "Migrated legacy FAISS identity '%s' → user.id=%d (%d embeddings)",
            name_key, user.id, len(embeddings),
        )
        return user.id

    except Exception as exc:
        logger.error(
            "Failed to migrate legacy identity '%s': %s", name_key, exc
        )
        db_session.rollback()
        return None


# ── Public API: registration ─────────────────────────────────────────────
def register_face_multi(
    name: str,
    images: List[np.ndarray],
    db_session: Session,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Register a new identity from multiple images (different angles).

    Each image is detected, aligned, embedded, and stored as a SEPARATE
    embedding under the same ``user.id``. We deliberately do NOT average
    the embeddings into one mean vector — averaging across pose collapses
    the very angle-specific information that makes off-frontal recognition
    work. The face_db is capped at MAX_EMBEDDINGS_PER_IDENTITY (default 10).

    Args:
        name:        Human-readable identity label.
        images:      List of decoded BGR images. Images with no detectable
                     face are silently skipped; if none have a face the
                     call raises NoFaceDetectedError and rolls back the
                     user insert.
        db_session:  Active SQLAlchemy session.

    Returns:
        ``{"success": True, "user_id": int, "name": str, "embeddings_stored": int}``

    Raises:
        NoFaceDetectedError: if zero usable faces found across all images.
    """
    if not images:
        raise NoFaceDetectedError("No images provided")

    # ── 1. Detect + embed every image first (no DB writes yet) ───────
    recognizer = get_recognizer()
    embeddings: List[np.ndarray] = []
    profile_source: Optional[np.ndarray] = None
    for idx, image in enumerate(images):
        faces = _detect_all(image)
        if not faces:
            logger.info("Image %d: no face detected, skipping", idx)
            continue
        face = _largest_face(faces)
        aligned = _align_face(image, face)
        embeddings.append(extract_embedding(recognizer, aligned))
        # First image with a usable face becomes the profile photo (DP).
        if profile_source is None:
            profile_source = image

    if not embeddings:
        raise NoFaceDetectedError("No face detected in any of the provided images")

    # ── 2. Insert user (Postgres is the source of truth) ─────────────
    user = _create_user_with_mrn(name, db_session, extra_fields)

    # ── 3. Push every embedding into FAISS under the same user_id ────
    # If FAISS ops fail partway through, roll back the Patient row and
    # purge any embeddings we managed to write so we don't leave orphans.
    face_db = get_face_db()
    stored = 0
    try:
        with face_db_write_lock:
            for emb in embeddings:
                before = face_db.size
                face_db.register(str(user.id), emb)
                if face_db.size > before:
                    stored += 1
            face_db.save()
    except Exception as exc:
        logger.error(
            "FAISS write failed for user id=%d — rolling back Patient row: %s",
            user.id, exc,
        )
        try:
            with face_db_write_lock:
                face_db.remove_identity(str(user.id))
        except Exception:
            pass
        db_session.delete(user)
        db_session.commit()
        raise

    # ── 4. Profile photo (best-effort — never fails a registration) ──
    # Saved after the FAISS write so the rollback path above cannot
    # leave an orphaned photo on disk. The image is re-encoded and the
    # filename is the random person_guid (see media_service security
    # notes).
    if profile_source is not None:
        photo_path = save_profile_photo(user.person_guid, profile_source)
        if photo_path:
            user.photo_url = photo_path
            db_session.commit()

    logger.info(
        "Registered user id=%d mrn=%s name=%r with %d/%d embeddings (db size=%d)",
        user.id, user.mrn, name, stored, len(embeddings), face_db.size,
    )
    return {
        "success": True,
        "user_id": user.id,
        "name": user.name,
        "mrn": user.mrn,
        "embeddings_stored": stored,
        "photo_url": resolve_media_url(user.photo_url),
    }


# ── Public API: management ───────────────────────────────────────────────
def get_all_users(db_session: Session) -> List[Patient]:
    """Retrieve all registered users."""
    return db_session.query(Patient).all()


def update_user_full(
    user_id: int,
    updates: Dict[str, Any],
    db_session: Session,
) -> Patient:
    """
    Update any editable demographic/clinical fields for a patient.

    ``updates`` is a dict of field→value pairs (only set keys are touched).
    FAISS embeddings are untouched — only Postgres is updated.

    Editable fields: name, age, gender, dob, contact_number, address,
                     department, doctor, category.
    """
    EDITABLE = {
        "name", "age", "gender", "dob",
        "contact_number", "address",
        "department", "doctor", "category",
    }
    user = db_session.query(Patient).filter(Patient.id == user_id).first()
    if not user:
        raise ValueError(f"Patient {user_id} not found")

    for field, value in updates.items():
        if field in EDITABLE and value is not None:
            setattr(user, field, value)

    db_session.commit()
    db_session.refresh(user)
    return user


def update_user_name(user_id: int, new_name: str, db_session: Session) -> Patient:
    """Backward-compat: update only the patient's name."""
    return update_user_full(user_id, {"name": new_name}, db_session)


def delete_user(user_id: int, db_session: Session):
    """
    Completely remove a user.
    1. Removes from FAISS Vector DB
    2. Deletes PostgreSQL relational row
    """
    user = db_session.query(Patient).filter(Patient.id == user_id).first()
    if not user:
        raise ValueError(f"Patient {user_id} not found")
        
    # Purge Vector Embeddings (async save — reconcile fixes any crash-window drift)
    face_db = get_face_db()
    with face_db_write_lock:
        removed_count = face_db.remove_identity(str(user_id))
    if removed_count > 0:
        schedule_save()
            
    # Purge ORM
    db_session.delete(user)
    db_session.commit()
    logger.info("Deleted user_id=%d and %d vector embeddings", user_id, removed_count)

def update_user_face(
    user_id: int, 
    images: List[np.ndarray], 
    db_session: Session
) -> Dict[str, Any]:
    """
    Overwrites a user's face embeddings with a new batch.
    Extracts faces from all provided images, scrubs old FAISS embeddings,
    and inserts the new embeddings.
    """
    if not images:
        raise NoFaceDetectedError("No images provided for update")

    user = db_session.query(Patient).filter(Patient.id == user_id).first()
    if not user:
        raise ValueError(f"Patient {user_id} not found")

    # 1. Detect + Embed across all images first to ensure validity
    recognizer = get_recognizer()
    embeddings: List[np.ndarray] = []
    profile_source: Optional[np.ndarray] = None
    for idx, image in enumerate(images):
        faces = _detect_all(image)
        if not faces:
            logger.info("Update user_id=%d: image %d has no face, skipping", user.id, idx)
            continue
        face = _largest_face(faces)
        aligned = _align_face(image, face)
        embeddings.append(extract_embedding(recognizer, aligned))
        if profile_source is None:
            profile_source = image

    if not embeddings:
        raise NoFaceDetectedError("No face detected in any of the uploaded images")

    # 2. Swap out embeddings via Lock
    face_db = get_face_db()
    stored = 0
    with face_db_write_lock:
        # Purge all old embeddings for this ID
        face_db.remove_identity(str(user_id))
        # Insert all new embeddings
        for emb in embeddings:
            before = face_db.size
            face_db.register(str(user_id), emb)
            if face_db.size > before:
                stored += 1
        face_db.save()
        
    # Refresh the profile photo alongside the embeddings (same guid-based
    # filename — the old file is overwritten in place). Best-effort.
    if profile_source is not None:
        photo_path = save_profile_photo(user.person_guid, profile_source)
        if photo_path:
            user.photo_url = photo_path
            db_session.commit()

    logger.info(
        "Updated face embedding for user_id=%d name=%r (stored %d/%d new angles)",
        user.id, user.name, stored, len(embeddings)
    )

    return {
        "success": True,
        "user_id": user.id,
        "name": user.name,
        "embeddings_stored": stored,
        "photo_url": resolve_media_url(user.photo_url),
    }


# ── Public API: recognition ──────────────────────────────────────────────
def recognize_faces(
    image: np.ndarray,
    db_session: Session,
) -> List[Dict[str, Any]]:
    """
    Detect every face in an image and identify each one.

    Steps (per face):
        1. Align the crop.
        2. Extract its embedding.
        3. Search FAISS for the nearest neighbour above threshold.
        4. Resolve the numeric id back to a human-readable name via
           PostgreSQL.

    Args:
        image:       Decoded BGR image (H, W, 3).
        db_session:  Active SQLAlchemy session.

    Returns:
        A list of dicts, one per detected face::

            [
                {
                    "identity":   "Piyush",  # or "Unknown"
                    "confidence": 0.87,
                    "bbox":       [x1, y1, x2, y2],
                },
                ...
            ]

        An empty list when no faces are detected (NOT an error — the
        caller may legitimately be polling an empty frame).
    """
    faces = _detect_all(image)
    if not faces:
        return []

    recognizer = get_recognizer()
    face_db = get_face_db()

    # Per-request cache: many faces in one frame may belong to the
    # same user, and we don't want to re-hit Postgres each time.
    name_cache: Dict[int, Optional[str]] = {}
    results: List[Dict[str, Any]] = []

    for face in faces:
        bbox = face["bbox"]

        # 1-2. Align + embed
        aligned = _align_face(image, face)
        embedding = extract_embedding(recognizer, aligned)

        # 3. FAISS lookup — returns (id_string_or_"Unknown", score)
        identity_key, score = face_db.search(embedding)

        if identity_key == "Unknown":
            results.append({
                "identity": "Unknown",
                "confidence": float(score),
                "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
            })
            continue

        # 4. Resolve id → name via Postgres. A non-integer key means a
        # legacy row that was registered under a plain name rather than
        # a user id (e.g. CLI registration). Auto-migrate: find or create
        # a Postgres Patient, re-key the FAISS entries from name → user.id,
        # and include user_id in the response so tracking works.
        try:
            user_id = int(identity_key)
        except ValueError:
            user_id = _migrate_legacy_identity(
                identity_key, db_session, face_db
            )
            if user_id is not None:
                name_cache[user_id] = identity_key
                results.append({
                    "identity": identity_key,
                    "user_id": user_id,
                    "confidence": float(score),
                    "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
                })
            else:
                results.append({
                    "identity": identity_key,
                    "confidence": float(score),
                    "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
                })
            continue

        name = _resolve_name(db_session, user_id, name_cache)
        if name is None:
            # FAISS has the id, Postgres doesn't — orphaned embedding.
            # Self-heal: remove the stale entry under the write lock so
            # we never log this warning twice for the same id.
            logger.warning(
                "Invalid FAISS entry for user_id=%d — removing from FAISS",
                user_id,
            )
            try:
                with face_db_write_lock:
                    removed = face_db.remove_identity(str(user_id))
                if removed:
                    # Orphan purge on a recognition hot path — async save so
                    # recognition requests don't block on FAISS serialisation.
                    schedule_save()
            except Exception as exc:  # pragma: no cover — defensive
                logger.error("Failed to purge orphan FAISS id=%d: %s", user_id, exc)

            results.append({
                "identity": "Unknown",
                "confidence": float(score),
                "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
            })
            continue

        results.append({
            "identity": name,
            "user_id": user_id,
            "confidence": float(score),
            "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
        })

    return results
