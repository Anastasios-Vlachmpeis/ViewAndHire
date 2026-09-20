from typing import Any

from fastapi import APIRouter, HTTPException
from openai import APIError, AuthenticationError

from backend import db
from backend.models import CustomQuestionsAdd, ListingCreate
from backend.services import llm

router = APIRouter(prefix="/api/listings", tags=["listings"])


def _question_key(text: str) -> str:
    return " ".join(text.split()).casefold()


def _custom_question_item(text: str, used_ids: set[str]) -> dict[str, Any]:
    index = 1
    question_id = "custom1"
    while question_id in used_ids:
        index += 1
        question_id = f"custom{index}"
    used_ids.add(question_id)
    return {
        "id": question_id,
        "question": text,
        "type": "custom",
        "source": "custom",
        "likelihood": 3,
        "rationale": "Added by you.",
        "scoring_hints": "Answer the question directly, with relevant detail and concrete examples where appropriate.",
    }


def _append_custom_questions(existing: list[dict[str, Any]], texts: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen_text = {_question_key(q["question"]) for q in existing}
    used_ids = {q["id"] for q in existing}
    added: list[dict[str, Any]] = []
    for text in texts:
        key = _question_key(text)
        if key in seen_text:
            continue
        seen_text.add(key)
        added.append(_custom_question_item(text, used_ids))
    return existing + added, added


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
        key = _question_key(text)
        if key in seen_text:
            continue
        seen_text.add(key)
        custom_questions.append(_custom_question_item(text, used_ids))
    generated = [q for q in questions if _question_key(q["question"]) not in seen_text]
    bank = db.save_question_bank(listing_id, custom_questions + generated)
    return bank


@router.get("/{listing_id}")
def get_listing(listing_id: str) -> dict[str, Any]:
    listing = db.get_listing(listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing


@router.post("/banks/{bank_id}/custom-questions")
def add_custom_questions(bank_id: str, payload: CustomQuestionsAdd) -> dict[str, Any]:
    bank = db.get_question_bank(bank_id)
    if not bank:
        raise HTTPException(status_code=404, detail="Question bank not found")
    questions, added = _append_custom_questions(bank["questions"], payload.questions)
    if not added:
        raise HTTPException(status_code=400, detail="Those questions are already in the bank.")
    updated = db.update_question_bank(bank_id, questions)
    listing = db.get_listing(bank["listing_id"])
    if listing is not None:
        custom_texts = [q["question"] for q in questions if q.get("source") == "custom"]
        db.update_listing_custom_questions(bank["listing_id"], custom_texts)
    return {"bank": updated, "added": added}


@router.get("/banks/{bank_id}")
def get_question_bank(bank_id: str) -> dict[str, Any]:
    bank = db.get_question_bank(bank_id)
    if not bank:
        raise HTTPException(status_code=404, detail="Question bank not found")
    return bank
