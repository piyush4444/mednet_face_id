"""
recognize.py — Face recognition endpoint.

Thin HTTP shim over ``app.services.face_service.recognize_faces``. The
route decodes the uploaded image and delegates everything else to the
service layer, which owns detection, alignment, embedding, FAISS
search, and id→name resolution.
"""

import logging

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services.face_service import recognize_faces

logger = logging.getLogger("backend.recognize")

router = APIRouter(tags=["recognition"])


@router.post("/recognize")
async def recognize(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Recognize every face in the uploaded image.

    Returns a JSON payload of the form::

        {
            "faces": [
                {
                    "identity":   "Piyush",   # or "Unknown"
                    "confidence": 0.87,
                    "bbox":       [x1, y1, x2, y2]
                },
                ...
            ]
        }

    An empty ``faces`` array is returned when no faces are detected —
    this is NOT an error.
    """
    # ── Decode upload ────────────────────────────────────────────────────
    data = await image.read()
    np_img = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Invalid image file")

    # ── Delegate to service ──────────────────────────────────────────────
    results = recognize_faces(img, db)
    return {"faces": results}
