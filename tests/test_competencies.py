import copy
import json
import unittest
from unittest.mock import patch

from backend.services import competencies as c, llm, scoring

QUESTION = "Describe a setback and how you responded."
TEXT = "Our demo failed. I checked the logs. I chose a fallback to meet the deadline. We delivered the demo. I learned to test earlier."


def payload():
    return {key: {"question_fit": "good", "question_excerpt": "Describe a setback", "question_fit_reason": "This question asks about adapting after a setback.",
                  "level": 3, "quotes": dict(zip(c.ANCHORS, ["Our demo failed.", "I checked the logs.", "I chose a fallback to meet the deadline.", "We delivered the demo.", "I learned to test earlier."])),
                  "missing_evidence": [], "coaching_action": "Explain your decision and its result."} for key in c.RUBRICS}


class CompetencyTests(unittest.TestCase):
    def test_complete_evidence_has_exact_spans_and_separate_levels(self):
        result = c.validate_assessments(payload(), QUESTION, TEXT)
        self.assertEqual(set(result["assessments"]), set(c.RUBRICS))
        for item in result["assessments"].values():
            self.assertEqual(item["level"], 3)
            for span in item["evidence"]:
                self.assertEqual(TEXT[span["start"]:span["end"]], span["quote"])
        self.assertNotIn("overall", result)
        self.assertFalse(result["validated"])

    def test_wrong_question_and_missing_anchor_abstain_instead_of_scoring_low(self):
        for fit in ["weak", "not_applicable"]:
            raw = payload()
            for item in raw.values(): item["question_fit"] = fit
            result = c.validate_assessments(raw, QUESTION, TEXT)
            self.assertTrue(all(v["level"] is None and v["status"] == "not_assessed" for v in result["assessments"].values()))
        for anchor in c.ANCHORS[:4]:
            raw = payload(); raw["resilience"]["quotes"][anchor] = ""
            item = c.validate_assessments(raw, QUESTION, TEXT)["assessments"]["resilience"]
            self.assertIsNone(item["level"])
            self.assertEqual(item["status"], "insufficient_evidence")
            self.assertTrue(any(anchor in s for s in item["missing_evidence"]))

    def test_fabricated_quotes_and_question_support_rejected(self):
        for change in ["quote", "question"]:
            raw = payload()
            if change == "quote": raw["resilience"]["quotes"]["outcome"] = "We won first prize."
            else: raw["resilience"]["question_excerpt"] = "Why do you love this company?"
            with self.assertRaises(ValueError): c.validate_assessments(raw, QUESTION, TEXT)

    def test_missing_reflection_caps_level_and_assertions_abstain(self):
        raw = payload(); raw["resilience"]["quotes"]["reflection"] = ""
        self.assertEqual(c.validate_assessments(raw, QUESTION, TEXT)["assessments"]["resilience"]["level"], 2)
        raw["resilience"]["level"] = 1
        self.assertIsNone(c.validate_assessments(raw, QUESTION, TEXT)["assessments"]["resilience"]["level"])

    def test_empty_answer_and_api_failure_do_not_produce_judgments(self):
        with patch.object(llm, "_complete") as complete:
            self.assertTrue(all(v["level"] is None for v in c.assess_answer(QUESTION, "")["assessments"].values()))
            complete.assert_not_called()
        with patch.object(llm, "_complete", return_value="{}") as complete, self.assertLogs(c.logger, "ERROR"):
            result = c.assess_answer(QUESTION, TEXT)
            self.assertEqual(complete.call_count, 2)
            self.assertEqual(result["status"], "unavailable")

    def test_repair_and_semantic_coaching_receive_no_delivery_data(self):
        with patch.object(llm, "_complete", side_effect=["{}", json.dumps(payload())]):
            self.assertEqual(c.assess_answer(QUESTION, TEXT)["assessments"]["resilience"]["level"], 3)
        answer = {"question_id": "q1", "question": QUESTION, "transcript": TEXT, "answer_quality": {"overall": 80, "notes": "Specific example"},
                  "speech_delivery": {"score": 10, "features": {"confidence_proxy": 1}}, "face_gaze": {"score": 0}, "competency_evidence": c.unavailable()}
        baseline = scoring.aggregate_scores([answer], "both")
        other = copy.deepcopy(answer); other["speech_delivery"]["score"] = 100; other["face_gaze"]["score"] = 100
        self.assertEqual(baseline, scoring.aggregate_scores([other], "both"))
        with patch.object(llm, "_complete", return_value=json.dumps({"keep": "Specific example", "actions": ["Describe actions.", "State reasoning.", "Name outcomes."]})) as complete:
            llm.generate_overview("job", [answer], baseline, [])
            prompt = complete.call_args.args[0][1]["content"]
            self.assertNotIn("confidence_proxy", prompt)
            self.assertNotIn('"face_gaze": {', prompt)
