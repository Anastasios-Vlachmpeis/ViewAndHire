import json
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Callable

import av
import numpy as np

from backend.config import settings
from backend.services import asr, face, llm, voice

ProgressCallback = Callable[[str, int, str], None]

AGGREGATE_WEIGHTS = {
    "answer_quality": 0.40,
    "speech_delivery": 0.35,
    "face_gaze": 0.25,
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=_json_default)


def _write_pcm_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def _decode_audio_with_av(source_path: Path, start: float = 0.0, duration: float | None = None) -> tuple[np.ndarray, int]:
    container = av.open(str(source_path))
    stream = next((s for s in container.streams if s.type == "audio"), None)
    if stream is None:
        container.close()
        return np.zeros(0, dtype=np.float32), 16000

    resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks: list[np.ndarray] = []
    end_time = None if duration is None else start + duration

    for frame in container.decode(audio=0):
        if frame.time is not None and frame.time < start:
            continue
        if end_time is not None and frame.time is not None and frame.time >= end_time:
            break
        for converted in resampler.resample(frame):
            arr = converted.to_ndarray()
            if arr.ndim == 2:
                arr = arr[0]
            chunks.append(arr.astype(np.float32))

    container.close()
    if not chunks:
        return np.zeros(0, dtype=np.float32), 16000
    return np.concatenate(chunks), 16000


def extract_wav(source_path: Path, wav_path: Path) -> None:
    if shutil.which("ffmpeg"):
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return
    samples, sample_rate = _decode_audio_with_av(source_path)
    _write_pcm_wav(wav_path, samples, sample_rate)


def extract_segment_wav(full_wav: Path, start: float, end: float, out_path: Path) -> None:
    duration = max(end - start, 0.1)
    if shutil.which("ffmpeg"):
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(full_wav),
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return
    with wave.open(str(full_wav), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        start_frame = int(start * sample_rate)
        end_frame = int(end * sample_rate)
        wav_file.setpos(max(0, start_frame))
        frames = wav_file.readframes(max(end_frame - start_frame, 1))
    segment_path = out_path
    with wave.open(str(segment_path), "wb") as out_wav:
        with wave.open(str(full_wav), "rb") as src:
            out_wav.setnchannels(src.getnchannels())
            out_wav.setsampwidth(src.getsampwidth())
            out_wav.setframerate(src.getframerate())
            out_wav.writeframes(frames)


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
                {"metric": f"{qid}_face", "label": "Face and gaze", "score": face_data.get("score", 0)},
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
        status_path.write_text(
            dumps({"stage": stage, "percent": percent, "message": message}),
            encoding="utf-8",
        )

    report("extract", 5, "Extracting audio...")
    recording = interview_dir / "recording.webm"
    wav_path = interview_dir / "recording.wav"
    if not recording.exists():
        raise FileNotFoundError("Recording not found")
    extract_wav(recording, wav_path)

    report("transcribe", 20, "Transcribing with local Whisper...")
    transcript = asr.transcribe_audio(wav_path)
    asr.save_transcript(interview_dir, transcript)

    report("face", 40, "Analyzing face and gaze...")
    face_result = {"frames": [], "summary": {"score": None}}
    if record_mode in {"both", "camera"} and recording.exists():
        face_result = face.analyze_video(recording)

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
        extract_segment_wav(wav_path, start, end, segment_wav)
        q_transcript = asr.slice_transcript(transcript, start, end)

        speech = {"score": None, "features": {}, "notes": "Skipped"}
        if record_mode in {"both", "mic", "camera"}:
            speech = voice.analyze_audio_segment(str(segment_wav), q_transcript)

        answer_quality = llm.score_answer(question["question"], question.get("scoring_hints", ""), q_transcript)

        q_face_frames = [f for f in face_result["frames"] if start <= f["time"] <= end]
        q_detected = [f for f in q_face_frames if f["face_detected"]]
        q_looking = [f for f in q_detected if f["looking_at_camera"]]
        face_visible = len(q_detected) / len(q_face_frames) if q_face_frames else 0.0
        looking_ratio = len(q_looking) / len(q_detected) if q_detected else 0.0
        negative = sum(1 for f in q_detected if f["expression"] in {"fearful", "sad", "angry", "disgust"})
        positive = sum(1 for f in q_detected if f["expression"] in {"happy", "neutral", "surprised"})
        expression_balance = positive / len(q_detected) if q_detected else 0.0
        face_score = None
        if record_mode in {"both", "camera"} and q_face_frames:
            face_score = _clamp(face_visible * 30 + looking_ratio * 45 + expression_balance * 25 - (negative / max(len(q_detected), 1)) * 15)

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
                },
                "speech_delivery": speech,
                "face_gaze": {
                    "score": round(face_score, 1) if face_score is not None else None,
                    "face_visible_ratio": round(face_visible, 3),
                    "looking_ratio": round(looking_ratio, 3),
                    "expression_balance": round(expression_balance, 3),
                },
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
        "per_question": per_question,
        "aggregate": aggregate,
        "weak_points": weak_points,
        "overview": overview,
    }
    analysis_path = interview_dir / "analysis.json"
    analysis_path.write_text(dumps(analysis), encoding="utf-8")
    report("done", 100, "Analysis complete")
    return analysis
