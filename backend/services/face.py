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


def _looking_at_camera(landmarks: list, width: int, height: int) -> bool:
    """Camera-facing proxy, measured relative to face size, not image size."""
    if len(landmarks) < 478:
        return False
    points = np.array([(lm.x * width, lm.y * height) for lm in landmarks])
    eyes = [(points[33] + points[133]) / 2, (points[362] + points[263]) / 2]
    axis = eyes[1] - eyes[0]
    eye_spacing = np.linalg.norm(axis)
    if eye_spacing < 1e-6:
        return False
    axis /= eye_spacing
    vertical = np.array([-axis[1], axis[0]])
    for center, corners, iris_ids in zip(eyes, [(33, 133), (362, 263)], [range(468, 473), range(473, 478)]):
        eye_width = np.linalg.norm(points[corners[1]] - points[corners[0]])
        if eye_width < 1e-6:
            return False
        iris = points[list(iris_ids)].mean(axis=0)
        if abs(np.dot(iris - center, axis)) / eye_width >= 0.22:
            return False
    nose_offset = points[1] - (eyes[0] + eyes[1]) / 2
    yaw_proxy = abs(np.dot(nose_offset, axis)) / eye_spacing
    pitch_proxy = np.dot(nose_offset, vertical) / eye_spacing
    return bool(yaw_proxy < 0.35 and 0.15 < pitch_proxy < 0.95)


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


def analyze_video(video_path: Path, sample_fps: float = 5.0) -> dict[str, Any]:
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
                         "expression": None, "expression_confidence": 0.0, "looking_at_camera": False}
                if result.face_landmarks:
                    landmarks = result.face_landmarks[0]
                    if net is None:
                        net = _get_fer()
                    expression, confidence = _predict_expression(frame, landmarks, net)
                    entry.update(face_detected=True, bbox=_bbox_from_landmarks(landmarks, width, height),
                                 expression=expression, expression_confidence=round(confidence, 3),
                                 looking_at_camera=_looking_at_camera(landmarks, width, height))
                frames.append(entry)
    if not frames:
        raise ValueError("Recording contains no decodable video frames")
    return {"frames": frames, "summary": summarize_frames(frames)}


def summarize_frames(frames: list[dict[str, Any]]) -> dict[str, Any]:
    detected = [f for f in frames if f["face_detected"]]
    looking = [f for f in detected if f["looking_at_camera"]]
    face_visible_ratio = len(detected) / len(frames) if frames else 0.0
    looking_ratio = len(looking) / len(detected) if detected else 0.0

    negative = sum(1 for f in detected if f["expression"] in {"fearful", "sad", "angry", "disgust"})
    positive = sum(1 for f in detected if f["expression"] in {"happy", "neutral", "surprised"})
    expression_balance = positive / len(detected) if detected else 0.0

    score = (
        face_visible_ratio * 30
        + looking_ratio * 45
        + expression_balance * 25
    )
    negative_ratio = negative / len(detected) if detected else 0.0
    score = max(0.0, min(100.0, score - negative_ratio * 15))

    return {
        "score": round(score, 1) if frames else None,
        "face_visible_ratio": round(face_visible_ratio, 3),
        "looking_ratio": round(looking_ratio, 3),
        "expression_balance": round(expression_balance, 3),
        "sample_count": len(frames),
        "notes": "Camera-facing and expression estimates are uncalibrated visual proxies.",
    }
