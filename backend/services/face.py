import math
import logging
import urllib.request
from pathlib import Path
from threading import Lock
from typing import Any

import cv2
import av
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from backend.config import settings
from backend.services import gaze, intel_gaze

_weights_lock = Lock()
logger = logging.getLogger(__name__)


def _download_weight(url: str, target: Path) -> Path:
    with _weights_lock:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".download")
            try:
                urllib.request.urlretrieve(url, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    return target


def _get_landmarker() -> vision.FaceLandmarker:
    # VIDEO trackers retain timestamps and tracking state. Never reuse across clips.
    options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=_landmarker_model_path()),
        num_faces=1,
        running_mode=vision.RunningMode.VIDEO,
        output_facial_transformation_matrixes=True,
        output_face_blendshapes=True,
    )
    return vision.FaceLandmarker.create_from_options(options)


def _landmarker_model_path() -> str:
    bundled = Path(mp.__file__).parent / "modules" / "face_landmarker" / "face_landmarker.task"
    if bundled.exists():
        return str(bundled)
    target = settings.weights_dir / "face_landmarker.task"
    if not target.exists():
        url = (
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task"
        )
        _download_weight(url, target)
    return str(target)


def movement_summary(result):
    """Neutral geometry observations; coefficient cutoffs are display rules only."""
    groups = {"jaw opening": ("jawOpen",), "mouth-corner movement": ("mouthSmileLeft", "mouthSmileRight"),
              "brow raising": ("browInnerUp", "browOuterUpLeft", "browOuterUpRight"),
              "eyelid closure": ("eyeBlinkLeft", "eyeBlinkRight")}
    output = {"state": "uncertain", "observations": [], "coefficients": {}, "scoring_enabled": False}
    shapes = getattr(result, "face_blendshapes", []) if result else []
    if not shapes:
        return output
    values = {c.category_name: float(c.score) for c in shapes[0]}
    names = {name for group in groups.values() for name in group}
    if any(name not in values or not math.isfinite(values[name]) or not 0 <= values[name] <= 1 for name in names):
        return output
    output["coefficients"] = {name: round(values[name], 3) for name in sorted(names)}
    output["observations"] = [label for label, group in groups.items() if max(values[name] for name in group) >= .5]
    output["state"] = "active" if output["observations"] else "low"
    return output


class LiveMovementEstimator:
    """Independent still-image tracking; serialize shared mutable networks."""
    def __init__(self):
        self.lock = Lock()
        self.landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=_landmarker_model_path()),
            num_faces=1, running_mode=vision.RunningMode.IMAGE, output_face_blendshapes=True,
        ))

    def analyze(self, frame):
        with self.lock:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = self.landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            if not result.face_landmarks:
                return {"facial_movement": movement_summary(None)}
            return {"facial_movement": movement_summary(result)}


def _bbox_from_landmarks(landmarks: list, width: int, height: int) -> dict[str, float]:
    xs = [lm.x * width for lm in landmarks]
    ys = [lm.y * height for lm in landmarks]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    pad_x = (x_max - x_min) * 0.08
    pad_y = (y_max - y_min) * 0.08
    x0, x1 = np.clip([x_min - pad_x, x_max + pad_x], 0, width)
    y0, y1 = np.clip([y_min - pad_y, y_max + pad_y], 0, height)
    return {"x": float(x0), "y": float(y0), "w": float(x1 - x0), "h": float(y1 - y0)}


def analyze_video(video_path: Path, sample_fps: float = 5.0, calibration: list[dict] | None = None) -> dict[str, Any]:
    if not math.isfinite(sample_fps) or sample_fps <= 0:
        raise ValueError("sample_fps must be positive and finite")
    frames: list[dict[str, Any]] = []
    windows = gaze.validate_calibration(calibration or [])
    smoother = intel_gaze.GazeSmoother()
    model_status = intel_gaze.model_status()
    try:
        gaze_model = intel_gaze.get_estimator()
    except intel_gaze.GazeUnavailable:
        logger.exception("Local gaze models unavailable")
        gaze_model = None
        model_status = intel_gaze.model_status(False)
    with av.open(str(video_path)) as container:
        if not container.streams.video:
            raise ValueError("Recording has no video track")
        stream = container.streams.video[0]
        native_fps = float(stream.average_rate or 30)
        if not math.isfinite(native_fps) or native_fps <= 0:
            native_fps = 30.0
        origin = float(container.start_time or 0) / av.time_base
        last_time, last_ms, next_sample = -1.0, -1, 0.0
        with _get_landmarker() as landmarker:
            for index, decoded in enumerate(container.decode(video=0)):
                time = float(decoded.time) - origin if decoded.time is not None else index / native_fps
                if not math.isfinite(time) or time < 0 or time <= last_time:
                    time = max(0.0, last_time + 1.0 / native_fps)
                last_time = time
                if time < next_sample:
                    continue
                next_sample = time + 1.0 / sample_fps
                timestamp_ms = max(last_ms + 1, int(round(time * 1000)))
                last_ms = timestamp_ms
                frame = decoded.to_ndarray(format="bgr24")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp_ms)
                height, width = frame.shape[:2]
                entry = {"time": round(time, 3), "face_detected": False, "bbox": None,
                         "facial_movement": movement_summary(None),
                         "head_pose": gaze.head_orientation(None),
                         "eye_contact": intel_gaze.uncertain("model_unavailable"),
                         "calibration_frame": bool(windows and time < windows[-1]["end"])}
                if result.face_landmarks:
                    landmarks = result.face_landmarks[0]
                    entry.update(face_detected=True, bbox=_bbox_from_landmarks(landmarks, width, height),
                                 facial_movement=movement_summary(result),
                                 head_pose=gaze.head_orientation(next(iter(getattr(result, "facial_transformation_matrixes", [])), None)))
                if gaze_model is not None:
                    try:
                        gaze_frame = gaze_model.analyze(frame)
                        entry["eye_contact"] = smoother.update(gaze_frame["eye_contact"], time)
                        if gaze_frame["face_detected"]:
                            entry.update(face_detected=True, bbox=gaze_frame["bbox"], head_pose=gaze_frame["head_pose"])
                    except Exception:
                        logger.exception("Local gaze inference failed")
                        gaze_model = None
                        model_status = intel_gaze.model_status(False)
                        entry["eye_contact"] = intel_gaze.uncertain("model_unavailable")
                frames.append(entry)
    if not frames:
        raise ValueError("Recording contains no decodable video frames")
    return {"frames": frames, "summary": summarize_frames(frames), "gaze_model": model_status}


def summarize_frames(frames: list[dict[str, Any]]) -> dict[str, Any]:
    frames = [f for f in frames if not f.get("calibration_frame")]
    detected = [f for f in frames if f["face_detected"]]
    face_visible_ratio = len(detected) / len(frames) if frames else 0.0
    head_known = [f["head_pose"]["facing_camera"] for f in detected
                  if f.get("head_pose", {}).get("facing_camera") is not None]
    eye_known = [f["eye_contact"]["state"] for f in detected
                 if f.get("eye_contact", {}).get("state") in {"toward_lens", "away"}]
    coverage = len(eye_known) / len(frames) if frames else 0.0
    ratio = eye_known.count("toward_lens") / len(eye_known) if eye_known else None
    return {
        "score": None,
        "face_visible_ratio": round(face_visible_ratio, 3),
        "head_facing_ratio": round(sum(head_known) / len(head_known), 3) if head_known else None,
        "eye_contact_ratio": round(ratio, 3) if ratio is not None else None,
        "eye_contact_coverage": round(coverage, 3),
        "eye_contact_sample_count": len(eye_known),
        "sample_count": len(frames),
        "scoring_enabled": False,
        "notes": "Eye contact is a model estimate and does not affect your score.",
    }
