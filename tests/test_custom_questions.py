import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db
from backend.config import settings
from backend.routers import interviews, listings


class CustomQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = patch.object(settings, "data_dir", Path(self.temp.name))
        config.start()
        self.addCleanup(config.stop)
        # Start from the previous schema to exercise migration and existing rows.
        with sqlite3.connect(settings.db_path) as conn:
            conn.execute("CREATE TABLE listings (id TEXT PRIMARY KEY, job_text TEXT NOT NULL, company TEXT, role_title TEXT, created_at TEXT NOT NULL)")
            conn.execute("INSERT INTO listings VALUES ('old', 'An existing job listing', NULL, NULL, '2026-01-01')")
        db.init_db()
        app = FastAPI()
        app.include_router(listings.router)
        app.include_router(interviews.router)
        self.client = TestClient(app)
        self.generated = [{"id": "q1", "question": "Why this role?", "type": "motivation",
                           "likelihood": 4, "rationale": "Role motivation", "scoring_hints": "Be specific"}]

    def test_existing_listings_and_optional_field(self):
        db.init_db()  # Migration can safely run again.
        self.assertEqual(db.get_listing("old")["custom_questions"], [])
        listing = self.client.post("/api/listings", json={"job_text": "A software engineering job listing"}).json()
        self.assertEqual(listing["custom_questions"], [])
        with patch.object(listings.llm, "generate_questions", return_value=self.generated):
            bank = self.client.post(f"/api/listings/{listing['id']}/questions").json()
        self.assertEqual(bank["questions"], self.generated)

    def test_custom_question_survives_storage_and_interview_selection(self):
        text = 'Explain a < b and "trade-offs".'
        response = self.client.post("/api/listings", json={"job_text": "A software engineering job listing",
                                                          "custom_questions": [f"  {text}  "]})
        self.assertEqual(response.status_code, 200)
        listing = response.json()
        self.assertEqual(self.client.get(f"/api/listings/{listing['id']}").json()["custom_questions"], [text])
        with patch.object(listings.llm, "generate_questions", return_value=self.generated):
            bank = self.client.post(f"/api/listings/{listing['id']}/questions").json()
        question = bank["questions"][0]
        self.assertEqual(question["question"], text)
        self.assertEqual(question["source"], "custom")
        self.assertTrue(question["scoring_hints"])
        self.assertEqual(self.client.get(f"/api/listings/banks/{bank['id']}").json(), bank)
        interview = self.client.post("/api/interviews", json={
            "listing_id": listing["id"], "question_bank_id": bank["id"],
            "settings": {"question_count": 1, "selection_mode": "predetermined", "selected_question_ids": [question["id"]]},
        })
        self.assertEqual(interview.status_code, 200)
        self.assertEqual(interview.json()["selected_questions"], [question])

    def test_duplicates_and_id_collisions(self):
        listing = db.create_listing("A software engineering job listing", custom_questions=["Why this role?", "why  this role?", "My own question?"])
        generated = self.generated + [dict(self.generated[0], id="custom1", question="Another question?")]
        with patch.object(listings.llm, "generate_questions", return_value=generated):
            questions = self.client.post(f"/api/listings/{listing['id']}/questions").json()["questions"]
        self.assertEqual([q["question"] for q in questions], ["Why this role?", "My own question?", "Another question?"])
        self.assertEqual(len({q["id"] for q in questions}), 3)

    def test_invalid_custom_question_limits(self):
        for questions in [[" "], ["x" * 2001], ["Question?"] * 21, "Question?"]:
            with self.subTest(questions=questions):
                response = self.client.post("/api/listings", json={"job_text": "A software engineering job listing",
                                                                  "custom_questions": questions})
                self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
