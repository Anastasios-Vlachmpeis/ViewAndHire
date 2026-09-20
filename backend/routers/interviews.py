import json
import random
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import TypeAdapter, ValidationError

from backend import db
from backend.config import settings
from backend.models import InterviewCreate, QuestionTimestamp
from backend.services import gaze, scoring
from backend.services.json_io import write_json

router = APIRouter(prefix="/api/interviews", tags=["interviews"])
logger = logging.getLogger(__name__)


def _parse_timestamps(raw: str, interview: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        parsed = TypeAdapter(list[QuestionTimestamp]).validate_json(raw)
        timestamps = [item.model_dump() for item in parsed]
        # Duration is checked after decoding; this validates structure and ordering now.
        scoring.normalize_timestamps(timestamps, interview["selected_questions"], float("inf"))
        return timestamps
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid question timestamps") from exc


def _queue_analysis(interview_id: str, timestamps: list[dict[str, Any]], background_tasks: BackgroundTasks) -> None:
    write_json(settings.interviews_dir / interview_id / "progress.json",
               {"stage": "queued", "percent": 0, "message": "Analysis queued..."})
    background_tasks.add_task(_run_analysis_job, interview_id, timestamps)


def recover_interrupted_jobs() -> None:
    """Called once at startup of the local, single-worker application."""
    for interview in db.list_interviews():
        if interview["status"] in {"uploaded", "analyzing"}:
            db.update_interview_status(interview["id"], "error")
            write_json(settings.interviews_dir / interview["id"] / "progress.json",
                       {"stage": "error", "percent": 0,
                        "message": "Analysis was interrupted by a server restart. Please retry analysis."})


def _select_questions(bank_questions: list[dict[str, Any]], settings_payload: dict[str, Any]) -> list[dict[str, Any]]:
    mode = settings_payload["selection_mode"]
    count = settings_payload["question_count"]
    if mode == "predetermined":
        selected_ids = settings_payload.get("selected_question_ids", [])
        by_id = {q["id"]: q for q in bank_questions}
        if len(selected_ids) != len(set(selected_ids)) or any(qid not in by_id for qid in selected_ids):
            raise HTTPException(status_code=400, detail="Select distinct questions from this question bank")
        if len(selected_ids) != count:
            raise HTTPException(status_code=400, detail="Question count must match the selected questions")
        return [by_id[qid] for qid in selected_ids]
    weights = [max(q.get("likelihood", 1), 1) for q in bank_questions]
    pool = bank_questions.copy()
    chosen: list[dict[str, Any]] = []
    while pool and len(chosen) < count:
        pick = random.choices(pool, weights=weights[: len(pool)], k=1)[0]
        chosen.append(pick)
        idx = pool.index(pick)
        pool.pop(idx)
        weights.pop(idx)
    return chosen


@router.post("")
def create_interview(payload: InterviewCreate) -> dict[str, Any]:
    listing = db.get_listing(payload.listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    bank = db.get_question_bank(payload.question_bank_id)
    if not bank:
        raise HTTPException(status_code=404, detail="Question bank not found")
    if bank["listing_id"] != payload.listing_id:
        raise HTTPException(status_code=400, detail="Question bank belongs to a different job listing")

    settings_payload = payload.settings.model_dump()
    selected = _select_questions(bank["questions"], settings_payload)
    if not selected:
        raise HTTPException(status_code=400, detail="No questions selected for this interview")
    settings_payload["question_count"] = len(selected)

    interview = db.create_interview(payload.listing_id, payload.question_bank_id, settings_payload, selected)
    return interview


@router.get("")
def list_interviews(saved: bool = False) -> list[dict[str, Any]]:
    interviews = db.list_interviews(saved_only=saved)
    summaries = []
    for item in interviews:
        listing = db.get_listing(item["listing_id"]) or {}
        bank = db.get_question_bank(item["question_bank_id"])
        aggregate_score = None
        analysis_path = settings.interviews_dir / item["id"] / "analysis.json"
        if item["status"] == "complete" and analysis_path.exists():
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            aggregate_score = analysis.get("aggregate", {}).get("answer_quality")
        summaries.append(
            {
                "id": item["id"],
                "status": item["status"],
                "saved": item["saved"],
                "created_at": item["created_at"],
                "settings": item["settings"],
                "question_count": len(item["selected_questions"]),
                "question_bank_count": len(bank["questions"]) if bank else 0,
                "aggregate_score": aggregate_score,
                "role_title": listing.get("role_title"),
                "company": listing.get("company"),
            }
        )
    return summaries


@router.get("/{interview_id}")
def get_interview(interview_id: str) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    return interview


@router.get("/{interview_id}/progress")
def get_progress(interview_id: str) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    path = settings.interviews_dir / interview_id / "progress.json"
    if not path.exists():
        return {"stage": "pending", "percent": 0, "message": "Waiting to start"}
    progress = json.loads(path.read_text(encoding="utf-8"))
    if progress["stage"] == "done" and interview["status"] in {"uploaded", "analyzing"}:
        return {"stage": "saving", "percent": 99, "message": "Finishing analysis..."}
    return progress


@router.get("/{interview_id}/results")
def get_results(interview_id: str) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview["status"] != "complete":
        raise HTTPException(status_code=409, detail="Analysis not complete")
    analysis_path = settings.interviews_dir / interview_id / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Analysis not ready")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    return {"interview": interview, "analysis": analysis}


@router.get("/{interview_id}/media/{filename}")
def get_media(interview_id: str, filename: str):
    allowed = {"recording.webm", "recording.wav"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="File not found")
    path = settings.interviews_dir / interview_id / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "video/webm" if filename.endswith(".webm") else "audio/wav"
    return FileResponse(path, media_type=media_type)


def _run_analysis_job(interview_id: str, timestamps: list[dict[str, Any]]) -> None:
    interview = db.get_interview(interview_id)
    if not interview:
        return
    listing = db.get_listing(interview["listing_id"]) or {"job_text": ""}
    interview_dir = settings.interviews_dir / interview_id
    try:
        db.update_interview_status(interview_id, "analyzing")
        scoring.run_analysis(
            interview_id,
            interview_dir,
            interview["selected_questions"],
            timestamps,
            interview["settings"]["record_mode"],
            listing["job_text"],
        )
        db.update_interview_status(interview_id, "complete")
    except Exception as exc:
        logger.exception("Analysis failed for interview %s", interview_id)
        progress_path = interview_dir / "progress.json"
        write_json(progress_path, {"stage": "error", "percent": 0, "message": str(exc)})
        db.update_interview_status(interview_id, "error")


@router.post("/{interview_id}/upload")
async def upload_recording(
    interview_id: str,
    background_tasks: BackgroundTasks,
    timestamps: str = Form(...),
    recording: UploadFile = File(...),
    calibration: str = Form("[]"),
) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    ts_payload = _parse_timestamps(timestamps, interview)
    try:
        calibration_payload = gaze.validate_calibration(json.loads(calibration), before=min(t["prep_start"] for t in ts_payload))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid eye-contact calibration") from exc
    content = await recording.read()
    if not content:
        raise HTTPException(status_code=400, detail="Recording is empty")
    if not db.claim_analysis(interview_id):
        raise HTTPException(status_code=409, detail="Analysis is already running")
    interview_dir = settings.interviews_dir / interview_id
    interview_dir.mkdir(parents=True, exist_ok=True)
    dest = interview_dir / "recording.webm"
    try:
        dest.write_bytes(content)
        write_json(interview_dir / "timestamps.json", ts_payload)
        write_json(interview_dir / "calibration.json", calibration_payload)
        _queue_analysis(interview_id, ts_payload, background_tasks)
    except Exception:
        db.update_interview_status(interview_id, "error")
        raise
    return {"status": "uploaded", "interview_id": interview_id}


@router.post("/{interview_id}/analyze")
def reanalyze_interview(interview_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    interview_dir = settings.interviews_dir / interview_id
    recording = interview_dir / "recording.webm"
    timestamps_path = interview_dir / "timestamps.json"
    if not recording.exists() or not timestamps_path.exists():
        raise HTTPException(status_code=400, detail="Recording or timestamps missing")
    ts_payload = _parse_timestamps(timestamps_path.read_text(encoding="utf-8"), interview)
    if not db.claim_analysis(interview_id):
        raise HTTPException(status_code=409, detail="Analysis is already running")
    try:
        _queue_analysis(interview_id, ts_payload, background_tasks)
    except Exception:
        db.update_interview_status(interview_id, "error")
        raise
    return {"status": "analyzing", "interview_id": interview_id}


@router.post("/{interview_id}/save")
def save_interview(interview_id: str) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    db.mark_interview_saved(interview_id)
    # Banks are persisted independently and immutable: keep the original bank link,
    # including questions that were not selected for this attempt.
    bank = db.get_question_bank(interview["question_bank_id"])
    return {"saved": True, "interview_id": interview_id,
            "question_bank_id": interview["question_bank_id"],
            "question_bank_count": len(bank["questions"]) if bank else 0}
