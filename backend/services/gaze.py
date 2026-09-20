"""Conservative, per-recording gaze comparison; not a general eye tracker.

MediaPipe supplies landmarks, not gaze targets. A lens/screen reference and a
separate repeat check are required before labelling an eye-contact estimate.
"""
import math
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, TypeAdapter, model_validator


STAGES = ["lens", "screen", "lens_check", "screen_check"]
MIN_SAMPLES = 6


class CalibrationWindow(BaseModel):
    target: Literal["lens", "screen", "lens_check", "screen_check"]
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_duration(self):
        if not 1 <= self.end - self.start <= 8:
            raise ValueError("Calibration windows must last 1 to 8 seconds")
        return self


def validate_calibration(payload: Any, before: float = math.inf) -> list[dict[str, Any]]:
    windows = TypeAdapter(list[CalibrationWindow]).validate_python(payload)
    if not windows:
        return []
    if [w.target for w in windows] != STAGES:
        raise ValueError("Complete all four eye-contact calibration steps")
    if any(a.end > b.start for a, b in zip(windows, windows[1:])) or windows[-1].end > before:
        raise ValueError("Calibration must be ordered and end before interview preparation")
    return [w.model_dump() for w in windows]


def head_orientation(matrix: Any) -> dict[str, Any]:
    unknown = {"facing_camera": None, "yaw": None, "pitch": None}
    rotation = np.asarray(matrix, dtype=float)
    if rotation.shape != (4, 4) or not np.isfinite(rotation).all():
        return unknown
    rotation = rotation[:3, :3]
    if abs(np.linalg.det(rotation)) < 1e-6:
        return unknown
    u, _, vt = np.linalg.svd(rotation)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        return unknown
    yaw = math.degrees(math.atan2(rotation[0, 2], rotation[2, 2]))
    pitch = math.degrees(math.atan2(-rotation[1, 2], math.hypot(rotation[0, 2], rotation[2, 2])))
    return {"facing_camera": bool(abs(yaw) <= 20 and abs(pitch) <= 20),
            "yaw": round(yaw, 2), "pitch": round(pitch, 2)}


def eye_features(landmarks: list, width: int, height: int) -> dict[str, Any]:
    if len(landmarks) < 478:
        return {"values": None, "reason": "eyes_unavailable"}
    points = np.array([(lm.x * width, lm.y * height) for lm in landmarks])
    values = []
    for outer, inner, upper, lower, iris in [(33, 133, 159, 145, 468), (362, 263, 386, 374, 473)]:
        eye = points[[outer, inner, upper, lower, iris]]
        if not np.isfinite(eye).all() or (eye < 0).any() or (eye[:, 0] > width).any() or (eye[:, 1] > height).any():
            return {"values": None, "reason": "eyes_out_of_frame"}
        horizontal = points[inner] - points[outer]
        eye_width = float(np.linalg.norm(horizontal))
        if eye_width < 14:
            return {"values": None, "reason": "eyes_too_small"}
        horizontal /= eye_width
        vertical = np.array([-horizontal[1], horizontal[0]])
        if vertical[1] < 0:
            vertical *= -1
        aperture = abs(float(np.dot(points[lower] - points[upper], vertical))) / eye_width
        if aperture < 0.14 or aperture > 0.6:
            return {"values": None, "reason": "blink_or_obscured_eyes"}
        center = (points[outer] + points[inner]) / 2
        lid_center = (points[upper] + points[lower]) / 2
        x = float(np.dot(points[iris] - center, horizontal)) / eye_width
        y = float(np.dot(points[iris] - lid_center, vertical)) / eye_width
        if abs(x) > 0.5 or abs(y) > aperture / 2 + 0.03:
            return {"values": None, "reason": "unreliable_iris"}
        values.extend([x, y])
    return {"values": values, "reason": None}


def _usable(frame: dict) -> bool:
    pose = frame.get("head_pose", {})
    return (frame.get("_eyes", {}).get("values") is not None
            and pose.get("yaw") is not None and pose.get("pitch") is not None
            and abs(pose["yaw"]) <= 35 and abs(pose["pitch"]) <= 35)


def _classify(frame: dict, reference: dict) -> tuple[str, str | None]:
    if not _usable(frame):
        return "uncertain", frame.get("_eyes", {}).get("reason") or "head_pose_unreliable"
    pose = np.array([frame["head_pose"]["yaw"], frame["head_pose"]["pitch"]])
    if np.max(np.abs(pose - reference["pose"])) > 8:
        return "uncertain", "head_moved_since_calibration"
    delta = (np.array(frame["_eyes"]["values"]) - reference["center"]) / reference["scale"]
    distance = float(np.sqrt(np.mean(delta ** 2)))
    if distance <= 1.75:
        return "toward_lens", None
    if distance >= 3.0:
        return "away", None
    return "uncertain", "borderline_gaze"


def fit_reference(frames: list[dict], windows: list[dict]) -> tuple[dict | None, dict]:
    def unavailable(reason):
        return None, {"status": "unavailable", "reason": reason}
    if not windows:
        return unavailable("No calibration was recorded. Retake with eye-contact calibration to enable this estimate.")
    samples = {}
    for window in windows:
        all_samples = [f for f in frames if window["start"] <= f["time"] < window["end"]]
        usable = [f for f in all_samples if _usable(f)]
        if len(usable) < MIN_SAMPLES or len(usable) < len(all_samples) * 0.65:
            return unavailable("Too few clear eye samples during calibration. Use brighter lighting and keep both eyes visible.")
        samples[window["target"]] = usable
    lens = np.array([f["_eyes"]["values"] for f in samples["lens"]])
    center = np.median(lens, axis=0)
    scale = np.maximum(np.median(abs(lens - center), axis=0) * 1.4826, [0.018, 0.012, 0.018, 0.012])
    if np.any(scale > 0.06):
        return unavailable("Eye tracking was unstable during calibration. Keep your head still and retry.")
    pose = np.median([[f["head_pose"]["yaw"], f["head_pose"]["pitch"]] for f in samples["lens"]], axis=0)
    reference = {"center": center, "scale": scale, "pose": pose}
    # Separate repeat windows are checks, never used to fit the reference.
    agreement = {}
    for target, expected in [("lens", "toward_lens"), ("screen", "away"),
                             ("lens_check", "toward_lens"), ("screen_check", "away")]:
        agreement[target] = sum(_classify(f, reference)[0] == expected for f in samples[target]) / len(samples[target])
    if min(agreement.values()) < 0.75:
        return unavailable("Calibration could not reliably distinguish lens from screen gaze. Retake and follow each gaze prompt while keeping your head still.")
    return reference, {"status": "ready", "reason": "Lens and screen repeat checks passed. This remains an estimated gaze signal.",
                       "repeat_check_agreement": {key: round(agreement[key], 3) for key in STAGES[2:]}}


def apply_eye_contact(frames: list[dict], windows: list[dict]) -> dict:
    reference, status = fit_reference(frames, windows)
    previous_state, previous_time = None, -math.inf
    for frame in frames:
        frame["calibration_frame"] = bool(windows and frame["time"] < windows[-1]["end"])
        state, reason = ("uncertain", "calibration_unavailable") if reference is None else _classify(frame, reference)
        # Require two consecutive observations; never carry a 'Yes' over a blink.
        raw_state = state
        if state != "uncertain" and (state != previous_state or frame["time"] - previous_time > 0.5):
            state, reason = "uncertain", "gaze_transition"
        previous_state, previous_time = raw_state, frame["time"]
        frame["eye_contact"] = {"state": state, "reason": reason}
        frame.pop("_eyes", None)
    return status
