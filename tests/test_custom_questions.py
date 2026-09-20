import sqlite3
import tempfile
import unittest
from contextlib import closing
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
        with closing(sqlite3.connect(settings.db_path)) as conn:
            conn.execute("CREATE TABLE listings (id TEXT PRIMARY KEY, job_text TEXT NOT NULL, company TEXT, role_title TEXT, created_at TEXT NOT NULL)")
            conn.execute("INSERT INTO listings VALUES ('old', 'An existing job listing', NULL, NULL, '2026-01-01')")
            conn.commit()
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

    def test_saved_bank_supports_same_and_different_questions_without_replacing_attempt(self):
        listing = db.create_listing("A software engineering job listing", custom_questions=["My question?"])
        custom = dict(self.generated[0], id="custom1", question="My question?", source="custom", type="custom")
        other = dict(self.generated[0], id="q2", question="An unused question?")
        bank = db.save_question_bank(listing["id"], [self.generated[0], custom, other])
        settings_payload = {"question_count": 2, "selection_mode": "predetermined",
                            "selected_question_ids": ["custom1", "q1"], "prep_seconds": 15,
                            "answer_seconds": 90, "record_mode": "mic"}
        payload = {"listing_id": listing["id"], "question_bank_id": bank["id"], "settings": settings_payload}
        original = self.client.post("/api/interviews", json=payload).json()
        db.update_interview_status(original["id"], "complete")
        recording = settings.interviews_dir / original["id"] / "recording.webm"
        recording.write_bytes(b"original recording")
        saved = self.client.post(f"/api/interviews/{original['id']}/save").json()
        self.assertEqual(saved["question_bank_count"], 3)
        self.assertEqual(saved["question_bank_id"], bank["id"])
        # A later generation for the same listing must not replace the saved bank.
        db.save_question_bank(listing["id"], [dict(other, question="New generation")])
        db.init_db()
        stored = self.client.get(f"/api/interviews/{original['id']}").json()
        retained = self.client.get(f"/api/listings/banks/{stored['question_bank_id']}").json()
        self.assertEqual(retained["questions"], bank["questions"])
        same = self.client.post("/api/interviews", json=payload).json()
        self.assertNotEqual(same["id"], original["id"])
        self.assertEqual([q["id"] for q in same["selected_questions"]], ["custom1", "q1"])
        payload["settings"] = dict(settings_payload, question_count=1, selected_question_ids=["q2"])
        different = self.client.post("/api/interviews", json=payload).json()
        self.assertEqual(different["selected_questions"], [other])
        self.assertEqual(recording.read_bytes(), b"original recording")
        self.assertEqual(db.get_interview(original["id"])["status"], "complete")
        history = self.client.get("/api/interviews?saved=true").json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["question_count"], 2)
        self.assertEqual(history[0]["question_bank_count"], 3)

    def test_selection_rejects_unknown_questions_truncation_and_wrong_listing(self):
        listing = db.create_listing("A software engineering job listing")
        bank = db.save_question_bank(listing["id"], self.generated)
        payload = {"listing_id": listing["id"], "question_bank_id": bank["id"],
                   "settings": {"question_count": 1, "selection_mode": "predetermined"}}
        for ids in [["unknown"], ["q1", "q1"], ["q1", "unknown"]]:
            payload["settings"]["selected_question_ids"] = ids
            self.assertEqual(self.client.post("/api/interviews", json=payload).status_code, 400)
        payload["settings"]["selected_question_ids"] = ["q1"]
        payload["settings"]["question_count"] = 2
        self.assertEqual(self.client.post("/api/interviews", json=payload).status_code, 400)
        payload["settings"]["question_count"] = 1
        payload["listing_id"] = "old"
        self.assertEqual(self.client.post("/api/interviews", json=payload).status_code, 400)

    def test_retake_adds_user_questions_without_generation(self):
        listing = db.create_listing("A software engineering job listing")
        bank = db.save_question_bank(listing["id"], self.generated)
        with patch.object(listings.llm, "generate_questions") as generate:
            added = self.client.post(f"/api/listings/banks/{bank['id']}/custom-questions",
                                     json={"questions": ["  My follow-up?  ", "My follow-up?", "Another of mine?"]})
            generate.assert_not_called()
        self.assertEqual(added.status_code, 200)
        body = added.json()
        self.assertEqual([q["question"] for q in body["added"]], ["My follow-up?", "Another of mine?"])
        self.assertTrue(all(q["source"] == "custom" for q in body["added"]))
        refreshed = self.client.get(f"/api/listings/banks/{bank['id']}").json()
        self.assertEqual(refreshed["id"], bank["id"])
        self.assertEqual([q["question"] for q in refreshed["questions"]],
                         ["Why this role?", "My follow-up?", "Another of mine?"])
        self.assertEqual(self.client.get(f"/api/listings/{listing['id']}").json()["custom_questions"],
                         ["My follow-up?", "Another of mine?"])
        duplicate = self.client.post(f"/api/listings/banks/{bank['id']}/custom-questions", json={"questions": ["my follow-up?"]})
        self.assertEqual(duplicate.status_code, 400)

    def test_new_custom_question_can_start_retake_without_changing_original_attempt(self):
        listing = db.create_listing("A software engineering job listing")
        bank = db.save_question_bank(listing["id"], self.generated)
        settings = {"question_count": 1, "selection_mode": "predetermined", "prep_seconds": 15,
                    "answer_seconds": 90, "record_mode": "mic", "selected_question_ids": ["q1"]}
        original = db.create_interview(listing["id"], bank["id"], settings, self.generated)
        response = self.client.post(f"/api/listings/banks/{bank['id']}/custom-questions", json={"questions": ["What did I build at the hackathon?"]})
        new_question = response.json()["added"][0]
        retake = self.client.post("/api/interviews", json={"listing_id": listing["id"], "question_bank_id": bank["id"],
            "settings": {**settings, "selected_question_ids": [new_question["id"]]}})
        self.assertEqual(retake.status_code, 200)
        self.assertEqual(retake.json()["selected_questions"], [new_question])
        self.assertEqual(db.get_interview(original["id"])["selected_questions"], self.generated)


if __name__ == "__main__":
    unittest.main()
