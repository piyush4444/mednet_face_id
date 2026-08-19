"""
user_service.py — CRUD for the unified users table.

Single source of truth for non-face operations on a ``User``:
listing, filtering, creating, updating, soft-deleting. Face data
(FAISS embeddings + JPEG samples) is owned by ``face_service``; this
module never touches the FAISS index.

Phase 2 of the user-model expansion (see
``docs/CHANGELOG.md`` — Phases 1–8).
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.db.models import User, UserType
from backend.app.services.media_service import resolve_media_url


# ── Errors ───────────────────────────────────────────────────────────


class UserServiceError(Exception):
    """Base class for user-service errors."""


class UserNotFoundError(UserServiceError):
    pass


class InvalidUserPayloadError(UserServiceError):
    pass


# ── Validation helpers ───────────────────────────────────────────────

_VALID_USER_TYPES = {t.value for t in UserType}

# Fields a caller may write through. The ``current_*`` and timestamp
# columns are owned by the tracking pipeline; FAISS data is owned by
# face_service. Anything outside this set is silently dropped.
_EDITABLE_FIELDS = {
    "user_type",
    "name",
    "age",
    "gender",
    "dob",
    "contact_number",
    "address",
    "mrn",
    "department",
    "doctor",
    "category",
    "role",
    "specialty",
    "staff_department",
    "opd_department_id",
    "opd_room_id",
    "purpose",
    "note",
    "is_active",
}


def _validate_type(user_type: str) -> str:
    if user_type not in _VALID_USER_TYPES:
        raise InvalidUserPayloadError(
            f"invalid user_type: {user_type!r}. "
            f"Allowed: {sorted(_VALID_USER_TYPES)}"
        )
    return user_type


def _validate_required_by_type(payload: Dict[str, Any]) -> None:
    """Per-type required-field check.

    The schema layer (Pydantic) keeps every field optional so that
    PATCH calls work; per-type required fields are enforced here on
    create.
    """
    user_type = payload.get("user_type")
    name = (payload.get("name") or "").strip()
    if not name:
        raise InvalidUserPayloadError("name is required")

    if user_type == UserType.PATIENT.value:
        if not (payload.get("mrn") or "").strip():
            raise InvalidUserPayloadError("PATIENT requires mrn")
    elif user_type == UserType.DOCTOR.value:
        if payload.get("opd_department_id") is None:
            raise InvalidUserPayloadError(
                "DOCTOR requires opd_department_id"
            )
    elif user_type == UserType.EMPLOYEE.value:
        if not (payload.get("role") or "").strip():
            raise InvalidUserPayloadError("EMPLOYEE requires role")
    elif user_type == UserType.VISITOR.value:
        # purpose is recommended but not strictly required
        pass
    elif user_type == UserType.RELATIVE.value:
        # RELATIVE must be linked to at least one patient; this is
        # enforced when the relations endpoint is called. The user
        # record itself can be created without a link first.
        pass


# ── Queries ──────────────────────────────────────────────────────────


def list_users(
    db: Session,
    *,
    user_type: Optional[str] = None,
    q: Optional[str] = None,
    is_active: Optional[bool] = True,
    limit: int = 200,
) -> List[User]:
    """List users, optionally filtered by type and/or a free-text query
    against name and MRN.

    ``is_active=True`` (the default) hides soft-deleted rows; pass
    ``None`` to include everything.
    """
    query = db.query(User)

    if user_type is not None:
        query = query.filter(User.user_type == _validate_type(user_type))

    if is_active is True:
        query = query.filter(User.is_active.is_(True))
    elif is_active is False:
        query = query.filter(User.is_active.is_(False))

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.name.ilike(like),
                User.mrn.ilike(like),
                User.contact_number.ilike(like),
            )
        )

    return query.order_by(User.name.asc()).limit(limit).all()


def get_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise UserNotFoundError(f"user id={user_id} not found")
    return user


# ── Create / update / soft-delete ────────────────────────────────────


def create_user(db: Session, payload: Dict[str, Any]) -> User:
    """Create a user row of the given type. Face enrolment is a
    separate call against ``/users/{id}/update-face/multi``."""

    user_type = _validate_type(payload.get("user_type") or UserType.PATIENT.value)
    payload = dict(payload)
    payload["user_type"] = user_type
    _validate_required_by_type(payload)

    # MRN uniqueness — enforced at the DB level but a clearer error
    # message helps the client.
    mrn = (payload.get("mrn") or "").strip()
    if mrn:
        existing = db.query(User).filter(User.mrn == mrn).first()
        if existing is not None:
            raise InvalidUserPayloadError(
                f"a user with mrn={mrn!r} already exists (id={existing.id})"
            )

    columns = {
        k: v for k, v in payload.items()
        if k in _EDITABLE_FIELDS and v is not None
    }
    columns.setdefault("user_type", user_type)
    columns.setdefault("is_active", True)
    columns.setdefault("current_status", "OUT")

    user = User(**columns)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    user_id: int,
    updates: Dict[str, Any],
) -> User:
    """Partial update. Unknown keys are dropped silently; the
    ``user_type`` value is validated when supplied."""
    user = get_user(db, user_id)

    if "user_type" in updates and updates["user_type"] is not None:
        _validate_type(updates["user_type"])

    for k, v in updates.items():
        if k in _EDITABLE_FIELDS and v is not None:
            setattr(user, k, v)

    db.commit()
    db.refresh(user)
    return user


def soft_delete_user(db: Session, user_id: int) -> User:
    """Flip ``is_active`` to False. Preserves history (sessions, OPD
    visits) and the FAISS embedding — use ``face_service.delete_user``
    for a hard delete that also purges the index."""
    return update_user(db, user_id, {"is_active": False})


# ── Serialisation ────────────────────────────────────────────────────


def user_to_dict(u: User) -> Dict[str, Any]:
    """Serialise a ``User`` row to the response shape consumed by the
    frontend. Mirrors :class:`backend.app.schemas.user.UserResponse`."""
    return {
        "id": u.id,
        "user_type": u.user_type,
        "name": u.name,
        "mrn": u.mrn,
        "age": u.age,
        "gender": u.gender,
        "dob": u.dob.isoformat() if u.dob else None,
        "contact_number": u.contact_number,
        "address": u.address,
        "department": u.department,
        "doctor": u.doctor,
        "category": u.category,
        "role": u.role,
        "specialty": u.specialty,
        "staff_department": u.staff_department,
        "opd_department_id": u.opd_department_id,
        "opd_room_id": u.opd_room_id,
        "purpose": u.purpose,
        "note": u.note,
        "is_active": u.is_active,
        "photo_url": resolve_media_url(u.photo_url),
        "current_status": u.current_status,
        "current_floor": u.current_floor,
        "current_camera_id": u.current_camera_id,
        "last_seen_at": u.last_seen_at,
        "created_at": u.created_at,
    }
