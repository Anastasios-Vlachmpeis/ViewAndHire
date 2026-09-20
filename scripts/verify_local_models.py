"""Read saved recordings and test local models without API calls or changing results.

Run from the project root: python -m scripts.verify_local_models INTERVIEW_ID [...]
"""
import argparse
import json
import tempfile
import time
import wave
from pathlib import Path

from backend import db
from backend.config import settings
from backend.services import asr, face, scoring, voice


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interview_ids", nargs="+")
    args = parser.parse_args()
    for interview_id in args.interview_ids:
        interview = db.get_interview(interview_id)
        if not interview:
            raise ValueError(f"Unknown interview: {interview_id}")
        directory = settings.interviews_dir / interview_id
        recording = directory / "recording.webm"
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="viewandhire-check-") as temporary:
            wav = Path(temporary) / "audio.wav"
            print(f"{interview_id}: extracting audio", flush=True)
            scoring.extract_wav(recording, wav)
            with wave.open(str(wav), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            timestamps, warnings = scoring.normalize_timestamps(
                json.loads((directory / "timestamps.json").read_text(encoding="utf-8")),
                interview["selected_questions"], duration,
            )
            print(f"{interview_id}: transcribing locally", flush=True)
            transcript = asr.transcribe_audio(wav)
            print(f"{interview_id}: analyzing video locally", flush=True)
            video = face.analyze_video(recording) if interview["settings"]["record_mode"] != "mic" else {"frames": []}
            answer_checks = []
            for index, ts in enumerate(timestamps):
                segment = Path(temporary) / f"segment_{index}.wav"
                text = asr.slice_transcript(transcript, ts["answer_start"], ts["answer_end"])
                scoring.extract_segment_wav(wav, ts["answer_start"], ts["answer_end"], segment)
                delivery = voice.analyze_audio_segment(str(segment), text) if ts["answer_end"] - ts["answer_start"] >= 0.1 else None
                # Ensure the real payload can be serialized without NaN/Infinity.
                scoring.dumps(delivery)
                answer_checks.append({"word_count": len(text.split()), "voice_analyzed": delivery is not None,
                                      "duration": None if delivery is None else delivery["duration"]})
            times = [frame["time"] for frame in video["frames"]]
            assert all(a < b for a, b in zip(times, times[1:]))
            scoring.dumps({"transcript": transcript, "video": video})
            print(json.dumps({"interview_id": interview_id, "duration": duration,
                              "video_samples": len(times), "last_video_time": times[-1] if times else None,
                              "detected_faces": sum(f["face_detected"] for f in video["frames"]),
                              "answers": answer_checks, "warnings": warnings,
                              "elapsed_seconds": round(time.monotonic() - started, 1),
                              "feedback_api_called": False, "saved_results_changed": False}), flush=True)


if __name__ == "__main__":
    main()
