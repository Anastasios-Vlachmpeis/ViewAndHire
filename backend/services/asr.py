from pathlib import Path
from threading import Lock
from typing import Any

from faster_whisper import WhisperModel

from backend.config import settings
from backend.services.json_io import write_json

_model: WhisperModel | None = None
_model_lock = Lock()


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    return _model


def transcribe_audio(wav_path: Path) -> dict[str, Any]:
    # Whisper returns a lazy generator; protect loading and its consumption.
    with _model_lock:
        return _transcribe_audio(wav_path)


def _transcribe_audio(wav_path: Path) -> dict[str, Any]:
    model = _get_model()
    segments, info = model.transcribe(str(wav_path), word_timestamps=True, vad_filter=True)
    segment_list = []
    words = []
    full_text_parts = []
    for seg in segments:
        seg_words = []
        if seg.words:
            for w in seg.words:
                word = {"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                words.append(word)
                seg_words.append(word)
        segment_list.append(
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "text": seg.text.strip(),
                "words": seg_words,
            }
        )
        if seg.text.strip():
            full_text_parts.append(seg.text.strip())
    result = {
        "language": info.language,
        "duration": float(info.duration),
        "text": " ".join(full_text_parts),
        "segments": segment_list,
        "words": words,
    }
    return result


def save_transcript(interview_dir: Path, transcript: dict[str, Any]) -> Path:
    path = interview_dir / "transcript.json"
    write_json(path, transcript)
    return path


def slice_transcript(transcript: dict[str, Any], start: float, end: float) -> str:
    if end <= start:
        return ""
    words = [
        w["word"]
        for w in transcript.get("words", [])
        if start <= (w["start"] + w["end"]) / 2 < end
    ]
    if transcript.get("words"):
        return " ".join(words)
    parts = []
    for seg in transcript.get("segments", []):
        if seg["end"] > start and seg["start"] < end:
            parts.append(seg["text"])
    return " ".join(parts).strip()
