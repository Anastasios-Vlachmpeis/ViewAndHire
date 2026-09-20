import urllib.request
from pathlib import Path
from typing import Any

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from backend.config import settings

FER_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "facial_expression_recognition/facial_expression_recognition_mobilefacenet_2022july.onnx"
)
YUNET_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)

EXPRESSION_LABELS = ["angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised"]

_landmarker: vision.FaceLandmarker | None = None
_fer_net: cv2.dnn.Net | None = None
_yunet: cv2.FaceDetectorYN | None = None


def _ensure_weights() -> tuple[Path, Path]:
    settings.weights_dir.mkdir(parents=True, exist_ok=True)
    fer_path = settings.weights_dir / "fer_mobilefacenet.onnx"
    yunet_path = settings.weights_dir / "yunet.onnx"
    if not fer_path.exists():
        urllib.request.urlretrieve(FER_MODEL_URL, fer_path)
    if not yunet_path.exists():
        urllib.request.urlretrieve(YUNET_MODEL_URL, yunet_path)
    return fer_path, yunet_path


def _get_landmarker() -> vision.FaceLandmarker:
    global _landmarker
    if _landmarker is None:
        base_options = python.BaseOptions(model_asset_path=_landmarker_model_path())
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
            num_faces=1,
            running_mode=vision.RunningMode.VIDEO,
        )
        _landmarker = vision.FaceLandmarker.create_from_options(options)
    return _landmarker


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
        settings.weights_dir.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, target)
    return str(target)


def _get_fer() -> cv2.dnn.Net:
    global _fer_net
    if _fer_net is None:
        fer_path, _ = _ensure_weights()
        _fer_net = cv2.dnn.readNet(str(fer_path))
    return _fer_net


def _get_yunet() -> cv2.FaceDetectorYN:
    global _yunet
    if _yunet is None:
        _, yunet_path = _ensure_weights()
        _yunet = cv2.FaceDetectorYN.create(str(yunet_path), "", (320, 320), 0.6, 0.3, 5000)
    return _yunet


def _bbox_from_landmarks(landmarks: list, width: int, height: int) -> dict[str, float]:
    xs = [lm.x * width for lm in landmarks]
    ys = [lm.y * height for lm in landmarks]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    pad_x = (x_max - x_min) * 0.08
    pad_y = (y_max - y_min) * 0.08
    return {
        "x": float(max(0.0, x_min - pad_x)),
        "y": float(max(0.0, y_min - pad_y)),
        "w": float(min(width, x_max + pad_x) - max(0.0, x_min - pad_x)),
        "h": float(min(height, y_max + pad_y) - max(0.0, y_min - pad_y)),
    }


def _looking_at_camera(landmarks: list, width: int, height: int) -> bool:
    if len(landmarks) < 478:
        return False
    left_iris = np.mean([(landmarks[i].x, landmarks[i].y) for i in (468, 469, 470, 471, 472)], axis=0)
    right_iris = np.mean([(landmarks[i].x, landmarks[i].y) for i in (473, 474, 475, 476, 477)], axis=0)
    left_eye = np.mean([(landmarks[i].x, landmarks[i].y) for i in (33, 133, 160, 159, 158, 157, 173)], axis=0)
    right_eye = np.mean([(landmarks[i].x, landmarks[i].y) for i in (362, 263, 387, 386, 385, 384, 398)], axis=0)
    nose = (landmarks[1].x, landmarks[1].y)

    left_offset = abs(left_iris[0] - left_eye[0])
    right_offset = abs(right_iris[0] - right_eye[0])
    yaw_proxy = abs((left_eye[0] + right_eye[0]) / 2 - nose[0])
    pitch_proxy = abs(nose[1] - (left_eye[1] + right_eye[1]) / 2)

    return bool(left_offset < 0.018 and right_offset < 0.018 and yaw_proxy < 0.03 and pitch_proxy < 0.04)


def _predict_expression(frame: np.ndarray, bbox: dict[str, float]) -> tuple[str, float]:
    x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
    x = max(0, x)
    y = max(0, y)
    crop = frame[y : y + int(h), x : x + int(w)]
    if crop.size == 0:
        return "neutral", 0.0
    blob = cv2.dnn.blobFromImage(crop, 1.0 / 255.0, (112, 112), (0, 0, 0), swapRB=True)
    net = _get_fer()
    net.setInput(blob)
    scores = net.forward()[0]
    idx = int(np.argmax(scores))
    return EXPRESSION_LABELS[idx], float(scores[idx])


def analyze_video(video_path: Path, sample_fps: float = 5.0) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {"frames": [], "summary": {"score": 0.0, "face_visible_ratio": 0.0, "looking_ratio": 0.0}}

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(int(round(native_fps / sample_fps)), 1)
    landmarker = _get_landmarker()
    frames: list[dict[str, Any]] = []
    frame_idx = 0
    sampled = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % step != 0:
            frame_idx += 1
            continue
        timestamp_ms = int((frame_idx / native_fps) * 1000)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = landmarker.detect_for_video(mp_image, timestamp_ms)
        height, width = frame.shape[:2]
        entry: dict[str, Any] = {
            "time": round(frame_idx / native_fps, 3),
            "face_detected": False,
            "bbox": None,
            "expression": None,
            "expression_confidence": 0.0,
            "looking_at_camera": False,
        }
        if result.face_landmarks:
            landmarks = result.face_landmarks[0]
            bbox = _bbox_from_landmarks(landmarks, width, height)
            expression, confidence = _predict_expression(frame, bbox)
            entry.update(
                {
                    "face_detected": True,
                    "bbox": bbox,
                    "expression": expression,
                    "expression_confidence": round(confidence, 3),
                    "looking_at_camera": bool(_looking_at_camera(landmarks, width, height)),
                }
            )
        frames.append(entry)
        sampled += 1
        frame_idx += 1

    cap.release()

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
        "frames": frames,
        "summary": {
            "score": round(score, 1),
            "face_visible_ratio": round(face_visible_ratio, 3),
            "looking_ratio": round(looking_ratio, 3),
            "expression_balance": round(expression_balance, 3),
            "sample_count": sampled,
        },
    }
