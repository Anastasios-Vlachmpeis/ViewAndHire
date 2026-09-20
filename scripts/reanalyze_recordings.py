"""Re-run saved recordings through every real analysis stage.

Run from the project root: python -m scripts.reanalyze_recordings INTERVIEW_ID [...]
Uses the configured feedback API. Backs up generated artifacts before replacing them.
"""
import argparse
import json
import shutil
import time
from datetime import datetime, timezone

from backend import db
from backend.config import settings
from backend.services import scoring
from backend.services.json_io import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interview_ids", nargs="+")
    args = parser.parse_args()
    for interview_id in args.interview_ids:
        interview = db.get_interview(interview_id)
        if not interview:
            raise ValueError(f"Unknown interview: {interview_id}")
        directory = settings.interviews_dir / interview_id
        timestamps = json.loads((directory / "timestamps.json").read_text(encoding="utf-8"))
        listing = db.get_listing(interview["listing_id"]) or {"job_text": ""}
        if not db.claim_analysis(interview_id):
            raise RuntimeError(f"Analysis already running for {interview_id}")
        try:
            backup = directory / "analysis_backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup.mkdir(parents=True)
            for name in ("analysis.json", "progress.json", "timestamps.json", "transcript.json", "calibration.json"):
                if (directory / name).exists():
                    shutil.copy2(directory / name, backup / name)
            db.update_interview_status(interview_id, "analyzing")
            started = time.monotonic()
            result = scoring.run_analysis(
                interview_id, directory, interview["selected_questions"], timestamps,
                interview["settings"]["record_mode"], listing["job_text"],
                progress=lambda stage, percent, message: print(f"{interview_id}: {percent}% {message}", flush=True),
            )
            db.update_interview_status(interview_id, "complete")
            report = {
                "interview_id": interview_id,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "audio_duration": result["transcript"]["duration"],
                "transcribed_words": len(result["transcript"]["words"]),
                "video_frames_analyzed": len(result["face_frames"]),
                "answer_word_counts": [len(q["transcript"].split()) for q in result["per_question"]],
                "aggregate": result["aggregate"],
                "warnings": result["warnings"],
                "overview_generated": bool(result["overview"].strip()),
            }
            write_json(directory / "verification.json", report)
            print(json.dumps(report), flush=True)
        except Exception as exc:
            db.update_interview_status(interview_id, "error")
            write_json(directory / "progress.json", {"stage": "error", "percent": 0, "message": str(exc)})
            raise


if __name__ == "__main__":
    main()
