"""
cameras.py — Pydantic models for the camera admin API.

The admin API is the *only* path that mutates the camera roster. The
camera worker still reads the validated dict shape (keys: camera_id,
type, source, floor, role, active, name) — we only added `name`,
`created_at`, `updated_at` on top.

USB cameras are intentionally NOT creatable via the API:
  * USB enumeration is host-specific and not visible from the browser.
  * Legacy USB rows imported during the JSON-to-PostgreSQL cutover remain
    readable and manageable, but new host-local devices are not exposed in
    the browser workflow.
Only ``POST /cameras`` rejects ``type=usb`` for new entries.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Internal slug shape — must stay stable for the lifetime of any
# PatientSession that references it. We auto-generate this server-side
# so the user never has to reason about uniqueness.
CAMERA_ID_RE = re.compile(r"^cam_[a-z0-9_]+$")
FLOOR_RE = re.compile(r"^[a-z0-9_]+$")

CameraType = Literal["usb", "rtsp"]
CameraRole = Literal["entry", "exit", "inside"]


class CameraCreate(BaseModel):
    """Payload for POST /cameras. Server assigns camera_id + timestamps."""

    name: str = Field(min_length=1, max_length=80)
    type: Literal["rtsp"]  # USB intentionally excluded — see module docstring.
    source: str = Field(min_length=1, max_length=500)
    floor: str = Field(min_length=1, max_length=40)
    role: CameraRole
    active: bool = True
    # Optional location inside the deployment's singleton facility.
    location_id: Optional[int] = None

    @field_validator("source")
    @classmethod
    def _rtsp_url_shape(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("rtsp://") or v.startswith("rtsps://")):
            raise ValueError("source must start with rtsp:// or rtsps://")
        return v

    @field_validator("floor")
    @classmethod
    def _floor_shape(cls, v: str) -> str:
        v = v.strip().lower().replace(" ", "_")
        if not FLOOR_RE.match(v):
            raise ValueError("floor must be lowercase alphanumeric with underscores")
        return v


class CameraUpdate(BaseModel):
    """Payload for PATCH /cameras/{id}. All fields optional, partial merge."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    source: Optional[str] = Field(default=None, min_length=1, max_length=500)
    floor: Optional[str] = Field(default=None, min_length=1, max_length=40)
    role: Optional[CameraRole] = None
    active: Optional[bool] = None
    location_id: Optional[int] = None

    @field_validator("source")
    @classmethod
    def _rtsp_url_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not (v.startswith("rtsp://") or v.startswith("rtsps://")):
            raise ValueError("source must start with rtsp:// or rtsps://")
        return v

    @field_validator("floor")
    @classmethod
    def _floor_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower().replace(" ", "_")
        if not FLOOR_RE.match(v):
            raise ValueError("floor must be lowercase alphanumeric with underscores")
        return v


class CameraRead(BaseModel):
    """Response shape for GET /cameras and friends.

    `source` is returned as-is for now (RTSP creds inline). When you wire
    real auth, replace with a redactor that only returns the host:port.
    """

    model_config = ConfigDict(from_attributes=True)

    camera_id: str
    name: str
    type: CameraType
    # Read tolerates int sources because pre-existing USB rows store
    # device indices as raw ints (e.g. ``source: 0``). Create/Update
    # still enforce RTSP-string.
    source: Union[str, int]
    floor: str
    role: CameraRole
    active: bool
    facility_id: Optional[int] = None
    location_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    # Derived runtime status — populated by the registry, not the store.
    status: Literal["running", "stopped", "missing"] = "stopped"


class CameraTestRequest(BaseModel):
    """Payload for POST /cameras/test — probe an RTSP URL without saving."""

    source: str = Field(min_length=1, max_length=500)

    @field_validator("source")
    @classmethod
    def _rtsp_url_shape(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("rtsp://") or v.startswith("rtsps://")):
            raise ValueError("source must start with rtsp:// or rtsps://")
        return v


class CameraTestResult(BaseModel):
    """Result of a probe. `ok=False` means we couldn't grab a frame."""

    ok: bool
    width: Optional[int] = None
    height: Optional[int] = None
    elapsed_ms: int
    error: Optional[str] = None
