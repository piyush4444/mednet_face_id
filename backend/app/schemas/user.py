"""
user.py — Pydantic schemas for the user-model expansion.

Schema set:
    UserBase           — fields shared across types
    UserCreate         — POST /users payload
    UserUpdate         — PATCH/PUT /users/{id} payload (all optional)
    UserResponse       — outbound serialisation
    UserRelationCreate — POST /users/{id}/relations payload
    UserRelationResponse — outbound relation row

Backward-compatibility aliases ``PatientBase``, ``PatientUpdate`` and
``PatientResponse`` are preserved at the bottom of this module so the
existing ``patients.py`` route, face_service, and any third-party
consumers keep working unchanged.
"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Allowed value sets (mirrors backend.app.db.models enums) ──────────
USER_TYPES = {"PATIENT", "DOCTOR", "EMPLOYEE", "VISITOR", "RELATIVE"}
RELATION_TYPES = {"SPOUSE", "PARENT", "CHILD", "SIBLING", "GUARDIAN", "OTHER"}


# ── Core user schemas ─────────────────────────────────────────────────


class UserBase(BaseModel):
    """Fields shared across every user type.

    Type-specific fields are appended in :class:`UserCreate` and the
    service layer raises a clear error when a required-by-type field
    is missing.
    """

    name: str = Field(..., min_length=1, max_length=255)
    user_type: str = Field(default="PATIENT")

    # Demographics — applicable to all types but optional everywhere
    age: Optional[int] = None
    gender: Optional[str] = None
    dob: Optional[date] = None
    contact_number: Optional[str] = None
    address: Optional[str] = None

    # Patient-only legacy fields (kept so the existing patient intake
    # form continues to round-trip without modification)
    mrn: Optional[str] = None
    department: Optional[str] = None
    doctor: Optional[str] = None
    category: Optional[str] = None

    # Doctor / employee
    role: Optional[str] = None
    specialty: Optional[str] = None
    staff_department: Optional[str] = None
    opd_department_id: Optional[int] = None
    opd_room_id: Optional[int] = None

    # Visitor
    purpose: Optional[str] = None

    # All-type
    note: Optional[str] = None
    is_active: Optional[bool] = True


class UserCreate(UserBase):
    """POST /users payload. The service validates required fields per
    type (e.g. PATIENT must have mrn, RELATIVE must reference at least
    one patient through the relations endpoint)."""

    # On create, ``user_type`` is required and must be one of USER_TYPES.
    user_type: str = Field(...)


class UserUpdate(BaseModel):
    """Partial update payload. Every field is optional; the service
    only writes the keys that are present in the request body."""

    user_type: Optional[str] = None
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    dob: Optional[date] = None
    contact_number: Optional[str] = None
    address: Optional[str] = None

    mrn: Optional[str] = None
    department: Optional[str] = None
    doctor: Optional[str] = None
    category: Optional[str] = None

    role: Optional[str] = None
    specialty: Optional[str] = None
    staff_department: Optional[str] = None
    opd_department_id: Optional[int] = None
    opd_room_id: Optional[int] = None
    purpose: Optional[str] = None
    note: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    """Outbound user representation.

    Includes the type discriminator + every optional field at top
    level. Relations are surfaced only via the detail endpoint
    (``GET /users/{id}``).
    """

    id: int
    user_type: str
    name: str

    mrn: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    dob: Optional[date] = None
    contact_number: Optional[str] = None
    address: Optional[str] = None

    # Legacy patient fields
    department: Optional[str] = None
    doctor: Optional[str] = None
    category: Optional[str] = None

    # Type-specific
    role: Optional[str] = None
    specialty: Optional[str] = None
    staff_department: Optional[str] = None
    opd_department_id: Optional[int] = None
    opd_room_id: Optional[int] = None
    purpose: Optional[str] = None
    note: Optional[str] = None

    is_active: bool = True

    # Tracking state
    current_status: Optional[str] = None
    current_floor: Optional[str] = None
    current_camera_id: Optional[str] = None
    last_seen_at: Optional[datetime] = None

    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ── Relation schemas ──────────────────────────────────────────────────


class UserRelationCreate(BaseModel):
    related_user_id: int = Field(..., gt=0)
    relation_type: str = Field(..., min_length=1, max_length=20)
    note: Optional[str] = Field(default=None, max_length=128)


class UserRelationResponse(BaseModel):
    id: int
    user_id: int
    related_user_id: int
    relation_type: str
    note: Optional[str] = None

    # Convenience fields surfaced from the JOIN so the frontend does
    # not need a second round-trip to display the other party's name.
    related_user_name: Optional[str] = None
    related_user_type: Optional[str] = None

    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserDetailResponse(UserResponse):
    """``GET /users/{id}`` body — base user plus the user's relations."""

    relations: List[UserRelationResponse] = Field(default_factory=list)


# ── Backward-compatibility aliases ────────────────────────────────────
# Kept so the existing ``routes/users.py`` (used by the Manage Users
# UI today) and ``routes/patients.py`` continue to import the same
# names. New code should import :class:`UserCreate` / :class:`UserUpdate`
# / :class:`UserResponse` directly.

PatientBase = UserBase
PatientUpdate = UserUpdate
PatientResponse = UserResponse
