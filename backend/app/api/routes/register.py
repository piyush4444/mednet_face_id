"""
register.py — Face registration endpoint.

Thin HTTP shim over ``app.services.face_service.register_face_multi``.
This route is intentionally dumb: parse the multipart upload, decode the
images, hand everything to the service layer, and return whatever the
service returns. All business logic (detection, alignment, embedding,
Postgres insert, FAISS insert, save) lives in the service.
"""

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services.face_service import (
    NoFaceDetectedError,
    register_face_multi,
)

logger = logging.getLogger("backend.register")

router = APIRouter(tags=["registration"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB per image

# Magic-byte signatures for formats we accept. We check bytes, not just the
# file extension — a ".jpg" filename with PNG contents is still PNG, and a
# non-image masquerading as an image is rejected here before OpenCV sees it.
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpeg"),       # JPEG (all variants)
    (b"\x89PNG\r\n\x1a\n", "png"),   # PNG
)


def _sniff_image_type(data: bytes) -> Optional[str]:
    for prefix, label in _IMAGE_SIGNATURES:
        if data.startswith(prefix):
            return label
    return None


def _parse_dob(raw: Optional[str]) -> Optional[date]:
    """Accept YYYY-MM-DD (HTML <input type=date>) or return None."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid dob format. Expected YYYY-MM-DD.",
        )


_VALID_USER_TYPES = {"PATIENT", "DOCTOR", "EMPLOYEE", "VISITOR", "RELATIVE"}


def _build_extras(
    *,
    user_type: Optional[str],
    age: Optional[int],
    gender: Optional[str],
    dob: Optional[str],
    contact_number: Optional[str],
    address: Optional[str],
    department: Optional[str],
    doctor: Optional[str],
    category: Optional[str],
    role: Optional[str],
    specialty: Optional[str],
    staff_department: Optional[str],
    opd_department_id: Optional[int],
    opd_room_id: Optional[int],
    purpose: Optional[str],
    note: Optional[str],
) -> Dict[str, Any]:
    """Collect every optional field, skipping blanks. The user_type
    field discriminates which subset of the extras is meaningful for
    the row being created — but the service writes whatever is
    supplied; redundant fields are simply ignored downstream."""
    extras: Dict[str, Any] = {}

    if user_type:
        if user_type not in _VALID_USER_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid user_type: {user_type!r}. "
                    f"Allowed: {sorted(_VALID_USER_TYPES)}"
                ),
            )
        extras["user_type"] = user_type

    if age is not None:
        extras["age"] = age
    if gender and gender.strip():
        extras["gender"] = gender.strip()
    parsed_dob = _parse_dob(dob)
    if parsed_dob is not None:
        extras["dob"] = parsed_dob
    if contact_number and contact_number.strip():
        extras["contact_number"] = contact_number.strip()
    if address and address.strip():
        extras["address"] = address.strip()
    if department and department.strip():
        extras["department"] = department.strip()
    if doctor and doctor.strip():
        extras["doctor"] = doctor.strip()
    if category and category.strip():
        extras["category"] = category.strip()
    if role and role.strip():
        extras["role"] = role.strip()
    if specialty and specialty.strip():
        extras["specialty"] = specialty.strip()
    if staff_department and staff_department.strip():
        extras["staff_department"] = staff_department.strip()
    if opd_department_id is not None:
        extras["opd_department_id"] = opd_department_id
    if opd_room_id is not None:
        extras["opd_room_id"] = opd_room_id
    if purpose and purpose.strip():
        extras["purpose"] = purpose.strip()
    if note and note.strip():
        extras["note"] = note.strip()
    return extras


@router.post("/register/multi")
async def register_multi(
    name: str = Form(...),
    images: List[UploadFile] = File(...),
    user_type: Optional[str] = Form(None),
    age: Optional[int] = Form(None),
    gender: Optional[str] = Form(None),
    dob: Optional[str] = Form(None),
    contact_number: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    department: Optional[str] = Form(None),
    doctor: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    specialty: Optional[str] = Form(None),
    staff_department: Optional[str] = Form(None),
    opd_department_id: Optional[int] = Form(None),
    opd_room_id: Optional[int] = Form(None),
    purpose: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Register a new identity from one or more images (different angles).

    Each image is detected, aligned, embedded independently, and stored
    as its own embedding under the same user_id. Recommend 5–10 images
    covering: frontal, slight left, slight right, looking up, looking
    down. Images with no detectable face are silently skipped.

    - **name**: Identity label.
    - **images**: 1..N face images. Capped server-side at
      ``MAX_EMBEDDINGS_PER_IDENTITY`` (default 10).
    - **user_type**: One of PATIENT (default) / DOCTOR / EMPLOYEE /
      VISITOR / RELATIVE.
    - Patient-specific extras: age, gender, dob (YYYY-MM-DD),
      contact_number, address, department, doctor, category.
    - Doctor / employee extras: role, specialty, staff_department,
      opd_department_id, opd_room_id.
    - Visitor / general extras: purpose, note.
    """
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required.")

    extras = _build_extras(
        user_type=user_type,
        age=age,
        gender=gender,
        dob=dob,
        contact_number=contact_number,
        address=address,
        department=department,
        doctor=doctor,
        category=category,
        role=role,
        specialty=specialty,
        staff_department=staff_department,
        opd_department_id=opd_department_id,
        opd_room_id=opd_room_id,
        purpose=purpose,
        note=note,
    )

    decoded: list = []
    skipped: list[dict] = []  # per-file reasons, surfaced back to the client

    for upload in images:
        filename = upload.filename or "(unnamed)"
        data = await upload.read()

        if not data:
            skipped.append({"filename": filename, "reason": "empty file"})
            continue

        if len(data) > MAX_UPLOAD_BYTES:
            skipped.append({
                "filename": filename,
                "reason": f"too large ({len(data)} bytes, max {MAX_UPLOAD_BYTES})",
            })
            continue

        sniffed = _sniff_image_type(data)
        if sniffed is None:
            skipped.append({
                "filename": filename,
                "reason": "unsupported format (only JPEG/PNG allowed)",
            })
            continue

        np_img = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
        if img is None:
            skipped.append({"filename": filename, "reason": "could not decode image"})
            continue

        decoded.append(img)

    if not decoded:
        detail = "No valid images could be decoded."
        if skipped:
            first = skipped[0]
            detail = f"{detail} First error: {first['filename']}: {first['reason']}"
        raise HTTPException(status_code=400, detail=detail)

    try:
        result = register_face_multi(name, decoded, db, extras)
    except NoFaceDetectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Unexpected error during multi-image registration: {exc}")
        raise HTTPException(status_code=500, detail="Internal server error during face registration.")

    # Attach per-file validation warnings so the UI can show them.
    if skipped:
        result["skipped"] = skipped
    return result
