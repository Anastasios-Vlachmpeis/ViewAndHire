import math
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
from backend.services import gaze

FER_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "facial_expression_recognition/facial_expression_recognition_mobilefacenet_2022july.onnx"
)

EXPRESSION_LABELS = ["angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised"]

_weights_lock = Lock()


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


def _get_fer() -> cv2.dnn.Net:
    path = _download_weight(FER_MODEL_URL, settings.weights_dir / "fer_mobilefacenet.onnx")
    # setInput/forward mutate the network, so use one network per analysis.
    return cv2.dnn.readNet(str(path))


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


def _predict_expression(frame: np.ndarray, landmarks: list, net: cv2.dnn.Net) -> tuple[str | None, float]:
    height, width = frame.shape[:2]
    points = np.array([(lm.x * width, lm.y * height) for lm in landmarks], dtype=np.float32)
    # Align eyes, nose and mouth to the model's five-point face template.
    source = np.array([(points[33] + points[133]) / 2, (points[362] + points[263]) / 2,
                       points[1], points[61], points[291]], dtype=np.float32)
    target = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                       [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)
    transform, _ = cv2.estimateAffinePartial2D(source, target, method=cv2.LMEDS)
    if transform is None or not np.isfinite(transform).all():
        return None, 0.0
    aligned = cv2.warpAffine(frame, transform, (112, 112))
    blob = cv2.dnn.blobFromImage(aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
    net.setInput(blob)
    scores = net.forward().reshape(-1)
    if len(scores) != len(EXPRESSION_LABELS) or not np.isfinite(scores).all():
        raise ValueError("Expression model returned invalid scores")
    # The network emits logits, not calibrated probabilities.
    probabilities = np.exp(scores - scores.max())
    probabilities /= probabilities.sum()
    idx = int(np.argmax(scores))
    return EXPRESSION_LABELS[idx], float(probabilities[idx])


def analyze_video(video_path: Path, sample_fps: float = 5.0, calibration: list[dict] | None = None) -> dict[str, Any]:
    if not math.isfinite(sample_fps) or sample_fps <= 0:
        raise ValueError("sample_fps must be positive and finite")
    frames: list[dict[str, Any]] = []
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
            net = None
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
                         "expression": None, "expression_confidence": 0.0,
                         "head_pose": gaze.head_orientation(None),
                         "_eyes": {"values": None, "reason": "face_unavailable"}}
                if result.face_landmarks:
                    landmarks = result.face_landmarks[0]
                    if net is None:
                        net = _get_fer()
                    expression, confidence = _predict_expression(frame, landmarks, net)
                    entry.update(face_detected=True, bbox=_bbox_from_landmarks(landmarks, width, height),
                                 expression=expression, expression_confidence=round(confidence, 3),
                                 head_pose=gaze.head_orientation(next(iter(getattr(result, "facial_transformation_matrixes", [])), None)),
                                 _eyes=gaze.eye_features(landmarks, width, height))
                frames.append(entry)
    if not frames:
        raise ValueError("Recording contains no decodable video frames")
    calibration_status = gaze.apply_eye_contact(frames, gaze.validate_calibration(calibration or []))
    return {"frames": frames, "summary": summarize_frames(frames), "calibration": calibration_status}


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
    score = ratio * 100 if ratio is not None and coverage >= 0.5 and len(eye_known) >= 10 else None
    return {
        "score": round(score, 1) if score is not None else None,
        "face_visible_ratio": round(face_visible_ratio, 3),
        "head_facing_ratio": round(sum(head_known) / len(head_known), 3) if head_known else None,
        "eye_contact_ratio": round(ratio, 3) if ratio is not None else None,
        "eye_contact_coverage": round(coverage, 3),
        "eye_contact_sample_count": len(eye_known),
        "sample_count": len(frames),
        "notes": "Estimated lens gaze among clear samples; uncertain frames are excluded. Head orientation is separate."
                 if score is not None else "Eye-contact score unavailable: needs calibration and enough clear eye samples.",
    }
