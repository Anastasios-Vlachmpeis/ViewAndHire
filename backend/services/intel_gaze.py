# Portions adapted from Intel Open Model Zoo, Copyright (C) 2018-2023 Intel Corporation.
# Licensed under Apache-2.0; see THIRD_PARTY_NOTICES.md.
"""Local appearance-based gaze inference using Intel Open Model Zoo models.

Crop geometry, BGR preprocessing and roll compensation follow Intel's gaze demo.
See THIRD_PARTY_NOTICES.md. Angle bands are provisional display rules, not a
validated eye-contact classifier; they must not contribute to interview scores.
"""
import hashlib
import json
import math
import tempfile
import urllib.request
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

from backend.config import settings

MODEL_NAME = "gaze-estimation-adas-0002"
MODEL_VERSION = "intel-omz-2023.0-fp16-v1"
TOWARD_DEGREES = 5.0
AWAY_DEGREES = 15.0
_load_lock = Lock()
_estimator = None


class GazeUnavailable(RuntimeError):
    pass


def model_status(available=True):
    return {"status": "ready" if available else "unavailable", "model": MODEL_NAME,
            "version": MODEL_VERSION, "requires_calibration": False,
            "scoring_enabled": False,
            "reason": "Eye contact is a model estimate and does not affect your score."
            if available else "Eye-contact analysis is unavailable. Head direction is shown separately."}


def uncertain(reason):
    return {"state": "uncertain", "reason": reason, "angle_degrees": None,
            "vector": None, "scoring_eligible": False, "model": MODEL_NAME}


def image_tensor(image, shape, eye_state=False):
    # IR files include their own preprocessing; the public eye ONNX does not.
    resized = cv2.resize(image, (int(shape[3]), int(shape[2])), interpolation=cv2.INTER_CUBIC)
    values = resized.astype(np.float32)
    if eye_state:
        values = (values - 127.0) / 255.0
    return np.ascontiguousarray(values.transpose(2, 0, 1)[None])


def adjusted_face_box(row, width, height):
    x, y = int(row[3] * width), int(row[4] * height)
    w, h = int((row[5] - row[3]) * width), int((row[6] - row[4]) * height)
    x -= int(.067 * w); y -= int(.028 * h)
    w += int(.15 * w); h += int(.13 * h)
    if w < h:
        x -= (h - w) // 2; w = h
    else:
        y -= (w - h) // 2; h = w
    if min(w, h) < 60 or x < 0 or y < 0 or x + w > width or y + h > height:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def eye_crop(image, first, second, roll):
    side = int(1.8 * np.linalg.norm(first - second))
    if side < 24:
        return None
    midpoint = (first + second) // 2
    x, y = int(midpoint[0]) - side // 2, int(midpoint[1]) - side // 2
    if x < 0 or y < 0 or x + side > image.shape[1] or y + side > image.shape[0]:
        return None
    crop = image[y:y + side, x:x + side]
    transform = cv2.getRotationMatrix2D((side // 2, side // 2), roll, 1)
    return cv2.warpAffine(crop, transform, (side, side), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def gaze_result(vector, roll):
    vector = np.asarray(vector, dtype=float).reshape(-1)
    if len(vector) != 3 or not np.isfinite(vector).all() or np.linalg.norm(vector) < 1e-6:
        return uncertain("invalid_gaze_vector")
    vector /= np.linalg.norm(vector)
    cs, sn = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    x, y, z = vector
    vector = np.array([x * cs + y * sn, -x * sn + y * cs, z])
    # Intel demo utils.cpp defines straight ahead as (0, 0, -1).
    # Follow the executable reference, not the contradictory README axis prose.
    angle = math.degrees(math.acos(float(np.clip(-z, -1, 1))))
    state = "toward_lens" if angle <= TOWARD_DEGREES else "away" if angle >= AWAY_DEGREES else "uncertain"
    return {"state": state, "reason": "borderline_gaze" if state == "uncertain" else None,
            "angle_degrees": round(angle, 2), "vector": [round(float(v), 5) for v in vector],
            "scoring_eligible": False, "model": MODEL_NAME}


class GazeSmoother:
    """Per-stream confirmation, never share temporal state between recordings."""
    def __init__(self):
        self.previous = None
        self.time = -math.inf

    def update(self, result, timestamp):
        raw = result["state"]
        output = dict(result)
        if raw != "uncertain" and (raw != self.previous or not 0 < timestamp - self.time <= .6):
            output.update(state="uncertain", reason="gaze_transition")
        self.previous, self.time = raw, timestamp
        return output


def _valid_file(path, spec):
    return (path.exists() and path.stat().st_size == spec["size"]
            and hashlib.sha384(path.read_bytes()).hexdigest() == spec["sha384"])


def ensure_models():
    manifest = json.loads(Path(__file__).with_name("gaze_models.json").read_text(encoding="utf-8"))
    directory = settings.weights_dir / "intel_gaze"
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, specs in manifest.items():
        for spec in specs:
            path = directory / spec["name"]
            if not _valid_file(path, spec):
                # Unique temporary files also tolerate a simultaneous verification process.
                with tempfile.NamedTemporaryFile(dir=directory, suffix=".download", delete=False) as output:
                    temporary = Path(output.name)
                    try:
                        with urllib.request.urlopen(spec["url"], timeout=30) as response:
                            size = 0
                            while chunk := response.read(1024 * 1024):
                                size += len(chunk)
                                if size > spec["size"]:
                                    raise GazeUnavailable("Unexpected gaze model download size")
                                output.write(chunk)
                    except Exception:
                        output.close()
                        temporary.unlink(missing_ok=True)
                        raise
                try:
                    if not _valid_file(temporary, spec):
                        raise GazeUnavailable("Gaze model checksum verification failed")
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            if path.suffix in {".xml", ".onnx"}:
                paths[name] = path
    return paths


class IntelGazeEstimator:
    def __init__(self):
        from openvino import Core
        core = Core()
        paths = ensure_models()
        self.models = {name: core.compile_model(str(path), "CPU", {
            "PERFORMANCE_HINT": "LATENCY", "INFERENCE_NUM_THREADS": 2, "NUM_STREAMS": 1,
        }) for name, path in paths.items()}
        self.lock = Lock()

    def _image_infer(self, name, image):
        model = self.models[name]
        tensor = image_tensor(image, model.input().shape, eye_state=name == "open-closed-eye-0001")
        return model({model.input().any_name: tensor})

    def _analyze(self, image):
        height, width = image.shape[:2]
        output = {"face_detected": False, "bbox": None, "width": width, "height": height,
                  "head_pose": {"facing_camera": None, "yaw": None, "pitch": None, "roll": None},
                  "eye_contact": uncertain("face_unavailable")}
        predictions = next(iter(self._image_infer("face-detection-retail-0004", image).values())).reshape(-1, 7)
        rows = [row for row in predictions if np.isfinite(row).all() and row[2] >= .6]
        if len(rows) != 1:
            if rows:
                output["eye_contact"] = uncertain("multiple_faces")
            return output
        box = adjusted_face_box(rows[0], width, height)
        if box is None:
            output["eye_contact"] = uncertain("face_crop_unreliable")
            return output
        output.update(face_detected=True, bbox=box)
        x, y, w, h = (box[key] for key in ("x", "y", "w", "h"))
        crop = image[y:y + h, x:x + w]
        pose_model = self.models["head-pose-estimation-adas-0001"]
        pose_outputs = self._image_infer("head-pose-estimation-adas-0001", crop)
        yaw, pitch, roll = [float(pose_outputs[pose_model.output(key)].reshape(-1)[0])
                            for key in ("angle_y_fc", "angle_p_fc", "angle_r_fc")]
        if not np.isfinite([yaw, pitch, roll]).all():
            output["eye_contact"] = uncertain("head_pose_unreliable")
            return output
        output["head_pose"] = {"facing_camera": abs(yaw) <= 20 and abs(pitch) <= 20,
                               "yaw": round(yaw, 2), "pitch": round(pitch, 2), "roll": round(roll, 2)}
        if max(abs(yaw), abs(pitch)) > 45 or abs(roll) > 45:
            output["eye_contact"] = uncertain("head_pose_out_of_range")
            return output
        landmarks = next(iter(self._image_infer("facial-landmarks-35-adas-0002", crop).values())).reshape(-1, 2)
        if len(landmarks) != 35 or not np.isfinite(landmarks).all():
            output["eye_contact"] = uncertain("eyes_unavailable")
            return output
        points = (landmarks * [w, h] + [x, y]).astype(int)
        eyes = [eye_crop(image, points[i], points[i + 1], roll) for i in (0, 2)]
        if any(eye is None for eye in eyes):
            output["eye_contact"] = uncertain("eye_crop_unreliable")
            return output
        for eye in eyes:
            # Use a fixed-size blur check so it does not vary solely with crop size.
            gray = cv2.cvtColor(cv2.resize(eye, (60, 60)), cv2.COLOR_BGR2GRAY)
            if cv2.Laplacian(gray, cv2.CV_64F).var() < 12:
                output["eye_contact"] = uncertain("eyes_blurred")
                return output
            scores = next(iter(self._image_infer("open-closed-eye-0001", eye).values())).reshape(-1)
            # The reference demo uses index 1 as open (its README lists the order differently).
            if len(scores) != 2 or not np.isfinite(scores).all() or scores[1] < .8:
                output["eye_contact"] = uncertain("blink_or_unclear_eyes")
                return output
        model = self.models[MODEL_NAME]
        values = {"head_pose_angles": np.array([[yaw, pitch, 0]], dtype=np.float32)}
        for name, eye in zip(("left_eye_image", "right_eye_image"), eyes):
            values[name] = image_tensor(eye, model.input(name).shape)
        vector = next(iter(model(values).values()))
        output["eye_contact"] = gaze_result(vector, roll)
        return output

    def analyze(self, image, blocking=True):
        if not self.lock.acquire(blocking=blocking):
            raise GazeUnavailable("Gaze analysis is busy")
        try:
            return self._analyze(image)
        finally:
            self.lock.release()


def get_estimator():
    global _estimator
    with _load_lock:
        if _estimator is None:
            try:
                _estimator = IntelGazeEstimator()
            except Exception as exc:
                raise GazeUnavailable("Unable to load local gaze models") from exc
    return _estimator
