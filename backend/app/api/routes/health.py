"""
health.py — Health-check endpoint.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from backend.app.db.postgres import engine

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Liveness probe — returns OK if the server is running."""
    return {"status": "ok"}


@router.get("/ready")
def readiness_check():
    """Readiness probe — verifies the API can reach PostgreSQL."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc
    return {"status": "ready", "database": "ok"}
