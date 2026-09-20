import json
import unittest
from unittest.mock import patch

from backend.services import llm


class AnswerSuggestionTests(unittest.TestCase):
    def test_rewrite_uses_transcript_and_repairs_missing_or_overlong_suggestions(self):
        transcript = "At a Hamburg hackathon we built a Parkinson's app using microphones and phone sensors."
        good = dict(adequacy=70, specificity=70, structure=70, ambiguity_penalty=0, overall=70,
                    notes="Specific example. Clarify your contribution.",
                    suggested_answer="At a Hamburg hackathon, we built a Parkinson's app using microphones and phone sensors. [Add your contribution.] [Add the actual outcome.]")
        for invalid in (None, "", "word " * 121):
            with self.subTest(invalid=invalid), patch.object(llm, "_complete", side_effect=[json.dumps({**good, "suggested_answer": invalid}), json.dumps(good)]) as complete:
                result = llm.score_answer("Describe a challenge", "Specific actions", transcript)
                self.assertEqual(result["suggested_answer"], good["suggested_answer"])
                self.assertEqual(complete.call_count, 2)
                prompt = complete.call_args_list[0].args[0][1]["content"]
                self.assertIn(transcript, prompt)
                self.assertIn('never turn "we" into "I"', prompt)
                self.assertIn("Do not invent", prompt)

    def test_empty_answer_does_not_invent_a_suggestion_or_call_the_api(self):
        with patch.object(llm, "_complete") as complete:
            self.assertIsNone(llm.score_answer("Question", "", "  ")["suggested_answer"])
            complete.assert_not_called()
