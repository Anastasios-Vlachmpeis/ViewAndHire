from typing import Any

from fastapi import APIRouter, HTTPException
from openai import APIError, AuthenticationError

from backend import db
from backend.models import ListingCreate
from backend.services import llm

router = APIRouter(prefix="/api/listings", tags=["listings"])


@router.post("")
def create_listing(payload: ListingCreate) -> dict[str, Any]:
    listing = db.create_listing(payload.job_text, payload.company, payload.role_title)
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
    bank = db.save_question_bank(listing_id, questions)
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
