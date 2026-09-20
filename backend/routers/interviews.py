import json
import random
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend import db
from backend.config import settings
from backend.models import InterviewCreate
from backend.services import scoring

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


def _select_questions(bank_questions: list[dict[str, Any]], settings_payload: dict[str, Any]) -> list[dict[str, Any]]:
    mode = settings_payload["selection_mode"]
    count = settings_payload["question_count"]
    if mode == "predetermined":
        selected_ids = set(settings_payload.get("selected_question_ids", []))
        chosen = [q for q in bank_questions if q["id"] in selected_ids]
        return chosen[:count]
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

    settings_payload = payload.settings.model_dump()
    selected = _select_questions(bank["questions"], settings_payload)
    if not selected:
        raise HTTPException(status_code=400, detail="No questions selected for this interview")

    interview = db.create_interview(payload.listing_id, payload.question_bank_id, settings_payload, selected)
    return interview


@router.get("")
def list_interviews(saved: bool = False) -> list[dict[str, Any]]:
    interviews = db.list_interviews(saved_only=saved)
    summaries = []
    for item in interviews:
        listing = db.get_listing(item["listing_id"]) or {}
        aggregate_score = None
        analysis_path = settings.interviews_dir / item["id"] / "analysis.json"
        if analysis_path.exists():
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            aggregate_score = analysis.get("aggregate", {}).get("overall")
        summaries.append(
            {
                "id": item["id"],
                "status": item["status"],
                "saved": item["saved"],
                "created_at": item["created_at"],
                "settings": item["settings"],
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
    path = settings.interviews_dir / interview_id / "progress.json"
    if not path.exists():
        return {"stage": "pending", "percent": 0, "message": "Waiting to start"}
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/{interview_id}/results")
def get_results(interview_id: str) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
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
        progress_path = interview_dir / "progress.json"
        progress_path.write_text(
            json.dumps({"stage": "error", "percent": 100, "message": str(exc)}),
            encoding="utf-8",
        )
        db.update_interview_status(interview_id, "error")


@router.post("/{interview_id}/upload")
async def upload_recording(
    interview_id: str,
    background_tasks: BackgroundTasks,
    timestamps: str = Form(...),
    recording: UploadFile = File(...),
) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    interview_dir = settings.interviews_dir / interview_id
    interview_dir.mkdir(parents=True, exist_ok=True)
    dest = interview_dir / "recording.webm"
    content = await recording.read()
    dest.write_bytes(content)

    ts_payload = json.loads(timestamps)
    ts_path = interview_dir / "timestamps.json"
    ts_path.write_text(json.dumps(ts_payload, indent=2), encoding="utf-8")

    db.update_interview_status(interview_id, "uploaded")
    background_tasks.add_task(_run_analysis_job, interview_id, ts_payload)
    return {"status": "uploaded", "interview_id": interview_id}


@router.post("/{interview_id}/save")
def save_interview(interview_id: str) -> dict[str, Any]:
    interview = db.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    db.mark_interview_saved(interview_id)
    return {"saved": True, "interview_id": interview_id}
