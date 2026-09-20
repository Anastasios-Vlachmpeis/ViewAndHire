import math
import json
import wave
from pathlib import Path
from typing import Any, Callable

import av
import numpy as np

from backend.config import settings
from backend.services import asr, face, gaze, llm, voice
from backend.services.json_io import dumps, write_json

ProgressCallback = Callable[[str, int, str], None]

AGGREGATE_WEIGHTS = {
    "answer_quality": 0.40,
    "speech_delivery": 0.35,
    "face_gaze": 0.25,
}


def _write_pcm_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def _decode_audio_with_av(source_path: Path, start: float = 0.0, duration: float | None = None) -> tuple[np.ndarray, int]:
    sample_rate = 16000
    chunks: list[np.ndarray] = []
    cursor = 0
    with av.open(str(source_path)) as container:
        if not container.streams.audio:
            raise ValueError("Recording has no audio track")
        origin = float(container.start_time or 0) / av.time_base
        resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=sample_rate)

        def append_frame(converted: av.AudioFrame) -> None:
            nonlocal cursor
            values = converted.to_ndarray().reshape(-1).astype(np.float32)
            position = round((float(converted.time) - origin) * sample_rate) if converted.time is not None else cursor
            # Preserve silence/gaps so audio and video use the same recording timeline.
            if position > cursor:
                chunks.append(np.zeros(position - cursor, dtype=np.float32))
                cursor = position
            overlap = max(0, cursor - position)
            values = values[overlap:]
            chunks.append(values)
            cursor += len(values)

        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                append_frame(converted)
        for converted in resampler.resample(None):
            append_frame(converted)
    if not chunks:
        raise ValueError("Recording contains no decodable audio samples")
    samples = np.concatenate(chunks)
    first = max(0, round(start * sample_rate))
    last = len(samples) if duration is None else first + max(0, round(duration * sample_rate))
    return samples[first:last], sample_rate


def extract_wav(source_path: Path, wav_path: Path) -> None:
    # Use one decoder on every machine, including exact timestamps and resampler flush.
    samples, sample_rate = _decode_audio_with_av(source_path)
    if not samples.size:
        raise ValueError("Recording contains no audio samples")
    _write_pcm_wav(wav_path, samples, sample_rate)


def extract_segment_wav(full_wav: Path, start: float, end: float, out_path: Path) -> None:
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
        raise ValueError("Invalid audio segment boundaries")
    with wave.open(str(full_wav), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        start_frame = min(round(start * sample_rate), wav_file.getnframes())
        end_frame = min(round(end * sample_rate), wav_file.getnframes())
        wav_file.setpos(start_frame)
        frames = wav_file.readframes(end_frame - start_frame)
    segment_path = out_path
    with wave.open(str(segment_path), "wb") as out_wav:
        with wave.open(str(full_wav), "rb") as src:
            out_wav.setnchannels(src.getnchannels())
            out_wav.setsampwidth(src.getsampwidth())
            out_wav.setframerate(src.getframerate())
            out_wav.writeframes(frames)


def normalize_timestamps(timestamps: list[dict[str, Any]], questions: list[dict[str, Any]],
                         duration: float) -> tuple[list[dict[str, Any]], list[str]]:
    """Recover the legacy stop-button bug, then validate against actual media duration."""
    from backend.models import QuestionTimestamp

    if not isinstance(timestamps, list):
        raise ValueError("Timestamps must be a list")
    ids = {q["id"]: i for i, q in enumerate(questions)}
    normalized, warnings, seen = [], [], set()
    previous_end = 0.0
    for index, raw in enumerate(timestamps):
        ts = QuestionTimestamp.model_validate(raw).model_dump()
        qid = ts["question_id"]
        if qid not in ids or qid in seen or ts["question_index"] != ids[qid]:
            raise ValueError("Timestamps contain an unknown, duplicate or misplaced question")
        seen.add(qid)
        start, end = ts["answer_start"], ts["answer_end"]
        if start > 0 and end == 0:
            if index != len(timestamps) - 1:
                raise ValueError("Only the last unfinished answer can be recovered")
            end = duration
            warnings.append(f"Recovered the missing end time for {qid} from recording duration.")
        if start == end == 0:
            # Legacy recordings stopped during preparation have no answer.
            start = end = min(ts["prep_start"], duration)
        if ts["prep_start"] > start or end < start or start < previous_end:
            raise ValueError("Answer timestamps must be ordered and non-overlapping")
        if start > duration + 0.5 or end > duration + 0.5:
            raise ValueError("Answer timestamps exceed the recording duration")
        ts.update(answer_start=min(start, duration), answer_end=min(end, duration))
        previous_end = ts["answer_end"]
        normalized.append(ts)
    if not normalized:
        raise ValueError("No question timestamps were recorded")
    return normalized, warnings


def compute_weak_points(per_question: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for q in per_question:
        qid = q["question_id"]
        answer = q.get("answer_quality", {})
        speech = q.get("speech_delivery", {})
        face_data = q.get("face_gaze", {})
        metrics.extend(
            [
                {"metric": f"{qid}_adequacy", "label": "Answer adequacy", "score": answer.get("adequacy", 0)},
                {"metric": f"{qid}_specificity", "label": "Answer specificity", "score": answer.get("specificity", 0)},
                {"metric": f"{qid}_structure", "label": "Answer structure", "score": answer.get("structure", 0)},
                {"metric": f"{qid}_delivery", "label": "Speech delivery", "score": speech.get("score", 0)},
                {"metric": f"{qid}_face", "label": "Estimated eye contact", "score": face_data.get("score")},
            ]
        )
    metrics = [m for m in metrics if m["score"] is not None]
    metrics.sort(key=lambda m: m["score"])
    return metrics[:3]


def aggregate_scores(per_question: list[dict[str, Any]], record_mode: str) -> dict[str, Any]:
    answer_scores = [q["answer_quality"]["overall"] for q in per_question if q.get("answer_quality")]
    speech_scores = [
        q["speech_delivery"]["score"]
        for q in per_question
        if q.get("speech_delivery") and q["speech_delivery"].get("score") is not None
    ]
    face_scores = [
        q["face_gaze"]["score"]
        for q in per_question
        if q.get("face_gaze") and q["face_gaze"].get("score") is not None
    ]

    answer_avg = sum(answer_scores) / len(answer_scores) if answer_scores else 0.0
    speech_avg = sum(speech_scores) / len(speech_scores) if speech_scores else None
    face_avg = sum(face_scores) / len(face_scores) if face_scores else None

    parts = [(answer_avg, AGGREGATE_WEIGHTS["answer_quality"])]
    weight_sum = AGGREGATE_WEIGHTS["answer_quality"]
    if speech_avg is not None and record_mode in {"both", "mic", "camera"}:
        parts.append((speech_avg, AGGREGATE_WEIGHTS["speech_delivery"]))
        weight_sum += AGGREGATE_WEIGHTS["speech_delivery"]
    if face_avg is not None and record_mode in {"both", "camera"}:
        parts.append((face_avg, AGGREGATE_WEIGHTS["face_gaze"]))
        weight_sum += AGGREGATE_WEIGHTS["face_gaze"]

    overall = sum(score * weight for score, weight in parts) / weight_sum if weight_sum else answer_avg
    return {
        "overall": round(overall, 1),
        "answer_quality": round(answer_avg, 1),
        "speech_delivery": round(speech_avg, 1) if speech_avg is not None else None,
        "face_gaze": round(face_avg, 1) if face_avg is not None else None,
    }


def run_analysis(
    interview_id: str,
    interview_dir: Path,
    selected_questions: list[dict[str, Any]],
    timestamps: list[dict[str, Any]],
    record_mode: str,
    job_text: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    def report(stage: str, percent: int, message: str) -> None:
        if progress:
            progress(stage, percent, message)
        status_path = interview_dir / "progress.json"
        write_json(status_path, {"stage": stage, "percent": percent, "message": message})

    report("extract", 5, "Extracting audio...")
    recording = interview_dir / "recording.webm"
    wav_path = interview_dir / "recording.wav"
    if not recording.exists():
        raise FileNotFoundError("Recording not found")
    extract_wav(recording, wav_path)
    with wave.open(str(wav_path), "rb") as wav_file:
        duration = wav_file.getnframes() / wav_file.getframerate()
    timestamps, warnings = normalize_timestamps(timestamps, selected_questions, duration)
    calibration_path = interview_dir / "calibration.json"
    calibration = gaze.validate_calibration(
        json.loads(calibration_path.read_text(encoding="utf-8")) if calibration_path.exists() else [],
        before=min(duration, min(t["prep_start"] for t in timestamps)))

    report("transcribe", 20, "Transcribing with local Whisper...")
    transcript = asr.transcribe_audio(wav_path)
    asr.save_transcript(interview_dir, transcript)

    report("face", 40, "Analyzing face and gaze...")
    face_result = {"frames": [], "summary": {"score": None}}
    if record_mode in {"both", "camera"} and recording.exists():
        face_result = face.analyze_video(recording, calibration=calibration)
        if face_result.get("gaze_model"):
            warnings.append(face_result["gaze_model"]["reason"])

    per_question: list[dict[str, Any]] = []
    total = max(len(selected_questions), 1)

    for idx, question in enumerate(selected_questions):
        ts = next((t for t in timestamps if t["question_id"] == question["id"]), None)
        if not ts:
            continue
        start = ts["answer_start"]
        end = ts["answer_end"]
        pct = 45 + int((idx / total) * 40)
        report("score", pct, f"Scoring question {idx + 1} of {total}...")

        segment_wav = interview_dir / f"segment_{idx}.wav"
        q_transcript = asr.slice_transcript(transcript, start, end)

        speech = {"score": None, "features": {}, "notes": "Skipped"}
        if end - start < 0.1:
            speech = {"score": None, "features": {}, "notes": "Answer skipped or too short to measure."}
        elif record_mode in {"both", "mic", "camera"}:
            extract_segment_wav(wav_path, start, end, segment_wav)
            speech = voice.analyze_audio_segment(str(segment_wav), q_transcript)

        answer_quality = llm.score_answer(question["question"], question.get("scoring_hints", ""), q_transcript)

        q_face_frames = [f for f in face_result["frames"] if start <= f["time"] < end]
        face_summary = face.summarize_frames(q_face_frames)

        per_question.append(
            {
                "question_id": question["id"],
                "question": question["question"],
                "timestamps": ts,
                "transcript": q_transcript,
                "answer_quality": {
                    "adequacy": answer_quality.get("adequacy", 0),
                    "specificity": answer_quality.get("specificity", 0),
                    "structure": answer_quality.get("structure", 0),
                    "ambiguity_penalty": answer_quality.get("ambiguity_penalty", 0),
                    "overall": answer_quality.get("overall", 0),
                    "notes": answer_quality.get("notes", ""),
                    "suggested_answer": answer_quality.get("suggested_answer"),
                },
                "speech_delivery": speech,
                "face_gaze": face_summary,
            }
        )

    aggregate = aggregate_scores(per_question, record_mode)
    weak_points = compute_weak_points(per_question)

    report("overview", 92, "Generating feedback overview...")
    overview = llm.generate_overview(job_text, per_question, aggregate, weak_points)

    analysis = {
        "interview_id": interview_id,
        "transcript": transcript,
        "face_frames": face_result["frames"],
        "face_summary": face_result["summary"],
        "eye_contact_model": face_result.get("gaze_model"),
        "analysis_version": 3,
        "per_question": per_question,
        "aggregate": aggregate,
        "weak_points": weak_points,
        "overview": overview,
        "warnings": warnings,
    }
    analysis_path = interview_dir / "analysis.json"
    write_json(analysis_path, analysis)
    report("done", 100, "Analysis complete")
    return analysis
