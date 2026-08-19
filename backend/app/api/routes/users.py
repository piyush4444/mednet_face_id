"""
users.py — HTTP surface for the unified user model.

Endpoint set:

    GET    /users?type=&q=&is_active=        list, type-filtered
    POST   /users                            create
    GET    /users/{id}                       detail + relations
    PUT    /users/{id}                       full update (legacy)
    PATCH  /users/{id}                       partial update
    DELETE /users/{id}                       hard delete (purges FAISS)
    POST   /users/{id}/update-face/multi     replace face samples

    GET    /users/{id}/relations             list every relation involving the user
    POST   /users/{id}/relations             link another user
    DELETE /users/{id}/relations/{rel_id}    unlink

Face operations remain delegated to ``face_service``; non-face CRUD is
handled by ``user_service`` and ``relation_service`` introduced in
Phase 2 of the user-model expansion.
"""

from typing import List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from backend.app.core.deps import require_permission
from backend.app.db.auth_models import Permission
from backend.app.db.postgres import get_db
from backend.app.schemas.user import (
    UserCreate,
    UserDetailResponse,
    UserRelationCreate,
    UserRelationResponse,
    UserResponse,
    UserUpdate,
)
from backend.app.services.face_service import (
    NoFaceDetectedError,
    delete_user as service_delete_user,
    update_user_face,
    update_user_full,
)
from backend.app.services.relation_service import (
    InvalidRelationPayloadError,
    RelationNotFoundError,
    add_relation,
    list_relations,
    remove_relation,
)
from backend.app.services.user_service import (
    InvalidUserPayloadError,
    UserNotFoundError,
    create_user as service_create_user,
    get_user,
    list_users,
    soft_delete_user,
    update_user,
    user_to_dict,
)

router = APIRouter(tags=["users"], prefix="/users")


# ── List / create / detail ───────────────────────────────────────────


@router.get("", response_model=List[UserResponse])
def list_users_endpoint(
    db: Session = Depends(get_db),
    type: Optional[str] = Query(default=None, description="Filter by user_type"),
    q: Optional[str] = Query(default=None, description="Free-text search on name / mrn / contact"),
    is_active: Optional[bool] = Query(
        default=True,
        description="True = active only (default); false = inactive only; null/None left to caller",
    ),
):
    """List users, optionally scoped by type and/or search string."""
    try:
        rows = list_users(db, user_type=type, q=q, is_active=is_active)
    except InvalidUserPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [user_to_dict(u) for u in rows]


@router.post(
    "",
    response_model=UserResponse,
    status_code=201,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def create_user_endpoint(payload: UserCreate, db: Session = Depends(get_db)):
    """Create a user row of the given type.

    Face enrolment is a separate step — call
    ``POST /users/{id}/update-face/multi`` after creation if the
    workflow requires face data.
    """
    try:
        user = service_create_user(db, payload.model_dump(exclude_none=True))
    except InvalidUserPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return user_to_dict(user)


@router.get("/{user_id}", response_model=UserDetailResponse)
def get_user_endpoint(user_id: int, db: Session = Depends(get_db)):
    """User detail plus every relation the user participates in
    (either side of the link)."""
    try:
        user = get_user(db, user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    body = user_to_dict(user)
    body["relations"] = list_relations(db, user_id)
    return body


# ── Update (PUT legacy + PATCH partial) ──────────────────────────────


@router.put(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def put_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)):
    """Legacy full-update endpoint. Equivalent to PATCH; preserved so
    existing frontend code keeps working."""
    try:
        # ``update_user_full`` predates the user-model expansion; for
        # the extended field set we route through ``user_service``.
        updates = payload.model_dump(exclude_none=True)
        if any(
            k in updates
            for k in (
                "user_type",
                "role",
                "specialty",
                "staff_department",
                "opd_department_id",
                "opd_room_id",
                "purpose",
                "note",
                "is_active",
            )
        ):
            user = update_user(db, user_id, updates)
            return user_to_dict(user)
        # Plain demographic update — keep the legacy helper so the
        # face-service hooks (name on FAISS labels) keep firing.
        updated = update_user_full(user_id, updates, db)
        return updated
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidUserPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def patch_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)):
    """Partial update with the extended user-model field set."""
    try:
        user = update_user(db, user_id, payload.model_dump(exclude_none=True))
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidUserPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return user_to_dict(user)


# ── Delete ───────────────────────────────────────────────────────────


@router.delete(
    "/{user_id}",
    status_code=204,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def delete_user_endpoint(
    user_id: int,
    db: Session = Depends(get_db),
    soft: bool = Query(
        default=False,
        description="If true, flip is_active=False instead of purging the FAISS row",
    ),
):
    """Delete a user.

    By default (``soft=false``) the user row, FAISS embedding, and
    associated face thumbnails are hard-deleted. Pass ``soft=true``
    to flip ``is_active`` to False and keep the data — used when an
    employee leaves or a department is reorganised but historic
    sessions / OPD visits must remain referenceable.
    """
    try:
        if soft:
            soft_delete_user(db, user_id)
        else:
            service_delete_user(user_id, db)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return None


# ── Face enrolment (existing) ────────────────────────────────────────


@router.post(
    "/{user_id}/update-face/multi",
    dependencies=[Depends(require_permission(Permission.FACES_ENROLL))],
)
async def update_face_multi(
    user_id: int,
    images: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Replace a user's face embeddings with a new multi-angle batch."""
    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required.")

    decoded: list = []
    for upload in images:
        data = await upload.read()
        np_img = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
        if img is not None:
            decoded.append(img)

    if not decoded:
        raise HTTPException(status_code=400, detail="No valid images could be decoded.")

    try:
        result = update_user_face(user_id, decoded, db)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NoFaceDetectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Relations ────────────────────────────────────────────────────────


@router.get(
    "/{user_id}/relations",
    response_model=List[UserRelationResponse],
)
def list_user_relations(user_id: int, db: Session = Depends(get_db)):
    """List every relation involving the user, in either direction."""
    # Existence check so a missing user returns 404 instead of an
    # empty list.
    try:
        get_user(db, user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return list_relations(db, user_id)


@router.post(
    "/{user_id}/relations",
    response_model=UserRelationResponse,
    status_code=201,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def create_user_relation(
    user_id: int,
    payload: UserRelationCreate,
    db: Session = Depends(get_db),
):
    """Link another user (``related_user_id``) to this user with the
    given ``relation_type``. Both directions are surfaced by
    ``GET /users/{id}/relations``; do not call this endpoint twice
    for the same pair."""
    try:
        get_user(db, user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        rel = add_relation(
            db,
            user_id=user_id,
            related_user_id=payload.related_user_id,
            relation_type=payload.relation_type,
            note=payload.note,
        )
    except InvalidRelationPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "id": rel.id,
        "user_id": rel.user_id,
        "related_user_id": rel.related_user_id,
        "relation_type": rel.relation_type,
        "note": rel.note,
        "created_at": rel.created_at,
    }


@router.delete(
    "/{user_id}/relations/{relation_id}",
    status_code=204,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def delete_user_relation(
    user_id: int,
    relation_id: int,
    db: Session = Depends(get_db),
):
    """Unlink a previously-created relation. The ``user_id`` path
    parameter is preserved for URL symmetry but the relation is
    identified by ``relation_id`` alone."""
    try:
        remove_relation(db, relation_id)
    except RelationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return None
