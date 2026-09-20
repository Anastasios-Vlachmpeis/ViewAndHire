import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.config import settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.interviews_dir.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                job_text TEXT NOT NULL,
                company TEXT,
                role_title TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS question_banks (
                id TEXT PRIMARY KEY,
                listing_id TEXT NOT NULL,
                questions_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (listing_id) REFERENCES listings(id)
            );

            CREATE TABLE IF NOT EXISTS interviews (
                id TEXT PRIMARY KEY,
                listing_id TEXT NOT NULL,
                question_bank_id TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                selected_questions_json TEXT NOT NULL,
                status TEXT NOT NULL,
                saved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (listing_id) REFERENCES listings(id),
                FOREIGN KEY (question_bank_id) REFERENCES question_banks(id)
            );
            """
        )


@contextmanager
def get_connection():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_listing(job_text: str, company: str | None = None, role_title: str | None = None) -> dict[str, Any]:
    listing_id = str(uuid4())
    row = {
        "id": listing_id,
        "job_text": job_text,
        "company": company,
        "role_title": role_title,
        "created_at": _utc_now(),
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO listings (id, job_text, company, role_title, created_at) VALUES (?, ?, ?, ?, ?)",
            (listing_id, job_text, company, role_title, row["created_at"]),
        )
    return row


def save_question_bank(listing_id: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
    bank_id = str(uuid4())
    row = {
        "id": bank_id,
        "listing_id": listing_id,
        "questions": questions,
        "created_at": _utc_now(),
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO question_banks (id, listing_id, questions_json, created_at) VALUES (?, ?, ?, ?)",
            (bank_id, listing_id, json.dumps(questions), row["created_at"]),
        )
    return row


def get_listing(listing_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    return dict(row) if row else None


def get_question_bank(bank_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM question_banks WHERE id = ?", (bank_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["questions"] = json.loads(data.pop("questions_json"))
    return data


def create_interview(
    listing_id: str,
    question_bank_id: str,
    settings_payload: dict[str, Any],
    selected_questions: list[dict[str, Any]],
) -> dict[str, Any]:
    interview_id = str(uuid4())
    now = _utc_now()
    row = {
        "id": interview_id,
        "listing_id": listing_id,
        "question_bank_id": question_bank_id,
        "settings": settings_payload,
        "selected_questions": selected_questions,
        "status": "pending",
        "saved": 0,
        "created_at": now,
        "updated_at": now,
    }
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO interviews
            (id, listing_id, question_bank_id, settings_json, selected_questions_json, status, saved, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interview_id,
                listing_id,
                question_bank_id,
                json.dumps(settings_payload),
                json.dumps(selected_questions),
                "pending",
                0,
                now,
                now,
            ),
        )
    interview_dir = settings.interviews_dir / interview_id
    interview_dir.mkdir(parents=True, exist_ok=True)
    return row


def update_interview_status(interview_id: str, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE interviews SET status = ?, updated_at = ? WHERE id = ?",
            (status, _utc_now(), interview_id),
        )


def mark_interview_saved(interview_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE interviews SET saved = 1, updated_at = ? WHERE id = ?",
            (_utc_now(), interview_id),
        )


def get_interview(interview_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["settings"] = json.loads(data.pop("settings_json"))
    data["selected_questions"] = json.loads(data.pop("selected_questions_json"))
    data["saved"] = bool(data["saved"])
    return data


def list_interviews(saved_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM interviews"
    params: tuple[Any, ...] = ()
    if saved_only:
        query += " WHERE saved = 1"
    query += " ORDER BY created_at DESC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for row in rows:
        data = dict(row)
        data["settings"] = json.loads(data.pop("settings_json"))
        data["selected_questions"] = json.loads(data.pop("selected_questions_json"))
        data["saved"] = bool(data["saved"])
        results.append(data)
    return results
