import json
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from backend.config import settings

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    return _model


def transcribe_audio(wav_path: Path) -> dict[str, Any]:
    model = _get_model()
    segments, info = model.transcribe(str(wav_path), word_timestamps=True, vad_filter=True)
    segment_list = []
    words = []
    full_text_parts = []
    for seg in segments:
        seg_words = []
        if seg.words:
            for w in seg.words:
                word = {"word": w.word.strip(), "start": w.start, "end": w.end}
                words.append(word)
                seg_words.append(word)
        segment_list.append(
            {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip(),
                "words": seg_words,
            }
        )
        if seg.text.strip():
            full_text_parts.append(seg.text.strip())
    result = {
        "language": info.language,
        "duration": info.duration,
        "text": " ".join(full_text_parts),
        "segments": segment_list,
        "words": words,
    }
    return result


def save_transcript(interview_dir: Path, transcript: dict[str, Any]) -> Path:
    path = interview_dir / "transcript.json"
    path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    return path


def slice_transcript(transcript: dict[str, Any], start: float, end: float) -> str:
    words = [
        w["word"]
        for w in transcript.get("words", [])
        if w["end"] >= start and w["start"] <= end
    ]
    if words:
        return " ".join(words)
    parts = []
    for seg in transcript.get("segments", []):
        if seg["end"] >= start and seg["start"] <= end:
            parts.append(seg["text"])
    return " ".join(parts).strip()
