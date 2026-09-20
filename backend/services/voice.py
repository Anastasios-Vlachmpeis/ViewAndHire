import re
from typing import Any

import parselmouth
from parselmouth.praat import call


FILLER_PATTERN = re.compile(r"\b(uh|um|erm|like|you know|sort of|kind of)\b", re.I)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _score_from_range(value: float, ideal_low: float, ideal_high: float, penalty_scale: float = 1.0) -> float:
    if ideal_low <= value <= ideal_high:
        return 100.0
    if value < ideal_low:
        dist = ideal_low - value
    else:
        dist = value - ideal_high
    return _clamp(100.0 - dist * penalty_scale)


def analyze_audio_segment(wav_path: str, transcript_text: str = "") -> dict[str, Any]:
    sound = parselmouth.Sound(wav_path)
    duration = sound.get_total_duration()
    if duration <= 0.05:
        return {
            "duration": duration,
            "score": 0.0,
            "features": {},
            "notes": "No audible speech detected.",
        }

    pitch = call(sound, "To Pitch", 0.0, 75, 600)
    mean_f0 = call(pitch, "Get mean", 0, 0, "Hertz")
    stdev_f0 = call(pitch, "Get standard deviation", 0, 0, "Hertz")

    intensity = call(sound, "To Intensity", 75, 0.0, "yes")
    mean_intensity = call(intensity, "Get mean", 0, 0, "energy")
    stdev_intensity = call(intensity, "Get standard deviation", 0, 0)

    harmonicity = call(sound, "To Harmonicity (cc)", 0.01, 75, 0.1, 1.0)
    hnr = call(harmonicity, "Get mean", 0, 0)

    point_process = call(sound, "To PointProcess (periodic, cc)", 75, 600)
    local_jitter = call(point_process, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
    local_shimmer = call([sound, point_process], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)

    silence_tg = call(intensity, "To TextGrid (silences)", -25, 0.3, 0.1, "silent", "sounding")
    num_intervals = call(silence_tg, "Get number of intervals", 1)
    pauses = []
    for i in range(1, num_intervals + 1):
        label = call(silence_tg, "Get label of interval", 1, i)
        if label == "silent":
            start = call(silence_tg, "Get start time of interval", 1, i)
            end = call(silence_tg, "Get end time of interval", 1, i)
            pauses.append({"start": start, "end": end, "duration": end - start})

    pause_durations = [p["duration"] for p in pauses]
    total_pause = sum(pause_durations)
    long_pauses = [p for p in pause_durations if p >= 1.5]
    pause_ratio = total_pause / duration if duration else 0.0
    pause_count = len(pauses)
    mean_pause = (total_pause / pause_count) if pause_count else 0.0

    word_count = len(re.findall(r"\b\w+\b", transcript_text))
    speaking_time = max(duration - total_pause, 0.01)
    speaking_rate = word_count / speaking_time if speaking_time else 0.0

    fillers = len(FILLER_PATTERN.findall(transcript_text or ""))

    confidence_proxy = (
        _score_from_range(mean_f0, 110, 220, 0.25)
        + _score_from_range(stdev_f0, 10, 45, 1.5)
        + _score_from_range(stdev_intensity, 2, 12, 2.0)
    ) / 3

    pause_score = _clamp(100 - len(long_pauses) * 18 - pause_ratio * 80)
    fluency_score = _clamp(100 - pause_count * 4 - fillers * 6)
    articulation_score = _score_from_range(speaking_rate, 1.8, 3.5, 18)

    delivery_score = (
        confidence_proxy * 0.35
        + pause_score * 0.30
        + fluency_score * 0.20
        + articulation_score * 0.15
    )

    features = {
        "duration": round(duration, 3),
        "mean_f0": round(float(mean_f0) if mean_f0 == mean_f0 else 0.0, 2),
        "f0_stdev": round(float(stdev_f0) if stdev_f0 == stdev_f0 else 0.0, 2),
        "mean_intensity": round(float(mean_intensity) if mean_intensity == mean_intensity else 0.0, 2),
        "intensity_stdev": round(float(stdev_intensity) if stdev_intensity == stdev_intensity else 0.0, 2),
        "hnr": round(float(hnr) if hnr == hnr else 0.0, 2),
        "local_jitter": round(float(local_jitter) if local_jitter == local_jitter else 0.0, 5),
        "local_shimmer": round(float(local_shimmer) if local_shimmer == local_shimmer else 0.0, 5),
        "pause_count": pause_count,
        "pause_ratio": round(pause_ratio, 3),
        "mean_pause_duration": round(mean_pause, 3),
        "long_pause_count": len(long_pauses),
        "speaking_rate_wps": round(speaking_rate, 2),
        "filler_count": fillers,
        "confidence_proxy": round(confidence_proxy, 1),
        "pause_score": round(pause_score, 1),
        "fluency_score": round(fluency_score, 1),
        "articulation_score": round(articulation_score, 1),
    }

    return {
        "duration": duration,
        "score": round(delivery_score, 1),
        "features": features,
        "pauses": pauses,
        "notes": "Delivery signals are acoustic proxies, not clinical assessments.",
    }
