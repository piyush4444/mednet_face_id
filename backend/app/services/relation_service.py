"""
relation_service.py — CRUD for the user_relations table.

Phase 2 of the user-model expansion. Relations are directed but the
list endpoint returns rows where the given user is on either side,
so relatives and patients each see the link from their own profile.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from backend.app.db.models import RelationType, User, UserRelation


# ── Errors ───────────────────────────────────────────────────────────


class RelationServiceError(Exception):
    """Base class for relation-service errors."""


class RelationNotFoundError(RelationServiceError):
    pass


class InvalidRelationPayloadError(RelationServiceError):
    pass


# ── Validation ───────────────────────────────────────────────────────

_VALID_RELATION_TYPES = {t.value for t in RelationType}


def _validate_relation_type(relation_type: str) -> str:
    if relation_type not in _VALID_RELATION_TYPES:
        raise InvalidRelationPayloadError(
            f"invalid relation_type: {relation_type!r}. "
            f"Allowed: {sorted(_VALID_RELATION_TYPES)}"
        )
    return relation_type


# ── Queries ──────────────────────────────────────────────────────────


def list_relations(db: Session, user_id: int) -> List[Dict[str, Any]]:
    """Return every relation involving the user, in either direction.

    Each row is decorated with the *other party*'s name and type so a
    single response is enough to render the panel.
    """
    # Other-party alias so the join gives us the partner's columns.
    Other = aliased(User)
    rows = (
        db.query(UserRelation, Other.name, Other.user_type)
        .join(
            Other,
            or_(
                (UserRelation.related_user_id == Other.id)
                & (UserRelation.user_id == user_id),
                (UserRelation.user_id == Other.id)
                & (UserRelation.related_user_id == user_id),
            ),
        )
        .filter(
            or_(
                UserRelation.user_id == user_id,
                UserRelation.related_user_id == user_id,
            )
        )
        .order_by(UserRelation.created_at.desc())
        .all()
    )

    out: List[Dict[str, Any]] = []
    for rel, other_name, other_type in rows:
        out.append(
            {
                "id": rel.id,
                "user_id": rel.user_id,
                "related_user_id": rel.related_user_id,
                "relation_type": rel.relation_type,
                "note": rel.note,
                "related_user_name": other_name,
                "related_user_type": other_type,
                "created_at": rel.created_at,
            }
        )
    return out


# ── Mutations ────────────────────────────────────────────────────────


def add_relation(
    db: Session,
    *,
    user_id: int,
    related_user_id: int,
    relation_type: str,
    note: Optional[str] = None,
) -> UserRelation:
    """Link two users. Raises if either side does not exist, the two
    are the same user, or the same triple already exists."""

    relation_type = _validate_relation_type(relation_type)

    if user_id == related_user_id:
        raise InvalidRelationPayloadError(
            "a user cannot be related to themselves"
        )

    # Both users must exist
    pair = (
        db.query(User.id)
        .filter(User.id.in_([user_id, related_user_id]))
        .all()
    )
    if len({row.id for row in pair}) != 2:
        raise InvalidRelationPayloadError(
            f"both user_id={user_id} and "
            f"related_user_id={related_user_id} must exist"
        )

    # Duplicate guard — DB constraint exists, but a clearer error is
    # nicer to the client.
    existing = (
        db.query(UserRelation)
        .filter(
            UserRelation.user_id == user_id,
            UserRelation.related_user_id == related_user_id,
            UserRelation.relation_type == relation_type,
        )
        .first()
    )
    if existing is not None:
        raise InvalidRelationPayloadError(
            f"relation already exists (id={existing.id})"
        )

    rel = UserRelation(
        user_id=user_id,
        related_user_id=related_user_id,
        relation_type=relation_type,
        note=note,
    )
    db.add(rel)
    db.commit()
    db.refresh(rel)
    return rel


def remove_relation(db: Session, relation_id: int) -> None:
    rel = db.query(UserRelation).filter(UserRelation.id == relation_id).first()
    if rel is None:
        raise RelationNotFoundError(f"relation id={relation_id} not found")
    db.delete(rel)
    db.commit()
