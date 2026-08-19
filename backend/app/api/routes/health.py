"""
health.py — Health-check endpoint.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Liveness probe — returns OK if the server is running."""
    return {"status": "ok"}
