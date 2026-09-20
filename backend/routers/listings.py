from typing import Any

from fastapi import APIRouter, HTTPException
from openai import APIError, AuthenticationError

from backend import db
from backend.models import ListingCreate
from backend.services import llm

router = APIRouter(prefix="/api/listings", tags=["listings"])


@router.post("")
def create_listing(payload: ListingCreate) -> dict[str, Any]:
    listing = db.create_listing(payload.job_text, payload.company, payload.role_title, payload.custom_questions)
    return listing


@router.post("/{listing_id}/questions")
def generate_questions(listing_id: str) -> dict[str, Any]:
    listing = db.get_listing(listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    try:
        questions = llm.generate_questions(listing["job_text"], listing.get("company"), listing.get("role_title"))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail="OpenAI rejected the API key. Check OPENAI_API_KEY in .env, then restart the server.",
        ) from exc
    except APIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Could not parse generated questions: {exc}") from exc
    custom_questions = []
    seen_text = set()
    used_ids = {q["id"] for q in questions}
    for text in listing.get("custom_questions", []):
        key = " ".join(text.split()).casefold()
        if key in seen_text:
            continue
        seen_text.add(key)
        question_id = f"custom{len(custom_questions) + 1}"
        while question_id in used_ids:
            question_id += "_"
        used_ids.add(question_id)
        custom_questions.append({
            "id": question_id,
            "question": text,
            "type": "custom",
            "source": "custom",
            "likelihood": 3,
            "rationale": "Added by you.",
            "scoring_hints": "Answer the question directly, with relevant detail and concrete examples where appropriate.",
        })
    generated = [q for q in questions if " ".join(q["question"].split()).casefold() not in seen_text]
    bank = db.save_question_bank(listing_id, custom_questions + generated)
    return bank


@router.get("/{listing_id}")
def get_listing(listing_id: str) -> dict[str, Any]:
    listing = db.get_listing(listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing


@router.get("/banks/{bank_id}")
def get_question_bank(bank_id: str) -> dict[str, Any]:
    bank = db.get_question_bank(bank_id)
    if not bank:
        raise HTTPException(status_code=404, detail="Question bank not found")
    return bank
