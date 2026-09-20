"""Bounded, stateless live-frame inference. Images are never saved or sent remotely."""
import io
import logging
import time
from threading import Lock

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from backend.services import face, intel_gaze

router = APIRouter(prefix="/api/gaze", tags=["gaze"])
logger = logging.getLogger(__name__)
_frame_lock = Lock()
MAX_BYTES = 512 * 1024
_movement_estimator = None


def live_movement(frame):
    global _movement_estimator
    if _movement_estimator is None:
        _movement_estimator = face.LiveMovementEstimator()
    return _movement_estimator.analyze(frame)


def analyze_jpeg(content):
    # Reject oversized decoded images before allocating the full pixel array.
    try:
        with Image.open(io.BytesIO(content)) as header:
            if header.format != "JPEG" or not (16 <= header.width <= 1280 and 16 <= header.height <= 720):
                raise ValueError("Expected a JPEG frame up to 1280 by 720 pixels")
        frame = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode frame")
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, "Invalid live video frame") from exc
    try:
        start = time.perf_counter()
        result = intel_gaze.get_estimator().analyze(frame, blocking=False)
        result.update(facial_movement=face.movement_summary(None))
        if result.get("face_detected"):
            try:
                result.update(live_movement(frame))
            except Exception:
                # A movement failure must not suppress otherwise usable gaze data.
                logger.exception("Live movement inference failed")
        result["processing_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return result
    except intel_gaze.GazeUnavailable as exc:
        raise HTTPException(503, "Eye-contact analysis is warming up or temporarily unavailable") from exc
    except Exception as exc:
        logger.exception("Live gaze inference failed")
        raise HTTPException(503, "Eye-contact analysis is temporarily unavailable") from exc


@router.post("/frame")
async def live_frame(request: Request):
    if request.headers.get("content-type", "").split(";")[0] != "image/jpeg":
        raise HTTPException(415, "Send a JPEG frame")
    if not _frame_lock.acquire(blocking=False):
        raise HTTPException(503, "Eye-contact analysis is busy")
    try:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise HTTPException(413, "Live video frame is too large")
        return await run_in_threadpool(analyze_jpeg, bytes(content))
    finally:
        _frame_lock.release()
