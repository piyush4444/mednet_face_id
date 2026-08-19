"""
face_db.py — Thin re-export of the canonical FAISS database.

The real implementation lives in ``engine.database.face_db`` so that the
engine (pipeline processes) can run without depending on the ``app``
package. API routes should import from here so that ``app`` remains the
single entry point for the backend.
"""

from backend.engine.database.face_db import FaceDatabase

__all__ = ["FaceDatabase"]
