import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from backend.services import face, gaze, llm


def windows():
    return [{"target": target, "start": i * 3 + 1, "end": i * 3 + 3}
            for i, target in enumerate(gaze.STAGES)]


def frame(time, values=None):
    return {"time": time, "face_detected": True,
            "head_pose": {"facing_camera": True, "yaw": 0, "pitch": 0},
            "_eyes": {"values": [0, 0, 0, 0] if values is None else values, "reason": None}}


def calibration_frames():
    return [frame(w["start"] + i * .2, [0, 0, 0, 0] if "lens" in w["target"] else [0, .1, 0, .1])
            for w in windows() for i in range(10)]


class GazeTests(unittest.TestCase):
    def test_calibration_requires_ordered_complete_finite_windows_before_prep(self):
        self.assertEqual(gaze.validate_calibration([], 0), [])
        self.assertEqual(gaze.validate_calibration(windows(), 12), windows())
        bad = [windows()[:-1], list(reversed(windows()))]
        overlap = windows(); overlap[1]["start"] = 2
        bad.append(overlap)
        nonfinite = windows(); nonfinite[0]["start"] = float("nan")
        bad.append(nonfinite)
        for value in bad:
            with self.assertRaises(ValueError):
                gaze.validate_calibration(value)
        with self.assertRaises(ValueError):
            gaze.validate_calibration(windows(), 11.9)

    def test_vertical_and_horizontal_away_are_separate_from_forward_head(self):
        frames = calibration_frames()
        for i, vector in enumerate([[0, 0, 0, 0], [0, .1, 0, .1], [.12, 0, .12, 0]]):
            frames.extend([frame(12 + i + j * .2, vector) for j in range(2)])
        self.assertEqual(gaze.apply_eye_contact(frames, windows())["status"], "ready")
        self.assertEqual([frames[i]["eye_contact"]["state"] for i in [-5, -3, -1]],
                         ["toward_lens", "away", "away"])
        self.assertTrue(all(f["head_pose"]["facing_camera"] for f in frames))
        self.assertTrue(all("_eyes" not in f for f in frames))

    def test_missing_calibration_and_failed_repeat_check_abstain(self):
        frames = [frame(1), frame(1.2)]
        self.assertEqual(gaze.apply_eye_contact(frames, [])["status"], "unavailable")
        self.assertTrue(all(f["eye_contact"]["state"] == "uncertain" for f in frames))
        frames = calibration_frames()
        for f in frames:
            if f["time"] >= 10:
                f["_eyes"]["values"] = [0, 0, 0, 0]
        self.assertEqual(gaze.apply_eye_contact(frames, windows())["status"], "unavailable")
        self.assertTrue(all(f["eye_contact"]["state"] == "uncertain" for f in frames))

    def test_blinks_head_changes_and_transitions_are_uncertain(self):
        frames = calibration_frames() + [frame(12 + i * .2) for i in range(7)]
        frames[-5]["_eyes"] = {"values": None, "reason": "blink_or_obscured_eyes"}
        frames[-2]["head_pose"]["pitch"] = 12
        gaze.apply_eye_contact(frames, windows())
        self.assertEqual([f["eye_contact"]["state"] for f in frames[-7:]],
                         ["uncertain", "toward_lens", "uncertain", "uncertain", "toward_lens", "uncertain", "uncertain"])

    def test_insufficient_or_indistinguishable_calibration_abstains(self):
        self.assertEqual(gaze.fit_reference(calibration_frames()[:5], windows())[1]["status"], "unavailable")
        frames = calibration_frames()
        for f in frames:
            f["_eyes"]["values"] = [0, 0, 0, 0]
        self.assertEqual(gaze.fit_reference(frames, windows())[1]["status"], "unavailable")

    def test_summary_excludes_calibration_and_unknown_samples_without_double_counting(self):
        frames = calibration_frames() + [frame(12 + i * .2) for i in range(21)]
        gaze.apply_eye_contact(frames, windows())
        result = face.summarize_frames(frames)
        self.assertEqual(result["sample_count"], 21)
        self.assertIsNone(result["score"])
        self.assertEqual(result["eye_contact_ratio"], 1)
        self.assertEqual(result["eye_contact_sample_count"], 20)
        for f in frames[-15:]:
            f["eye_contact"]["state"] = "uncertain"
        self.assertIsNone(face.summarize_frames(frames)["score"])
        self.assertEqual(face.summarize_frames(frames)["head_facing_ratio"], 1)

    def test_head_rotation_matrix_has_independent_yaw_and_pitch(self):
        self.assertTrue(gaze.head_orientation(np.eye(4))["facing_camera"])
        for axis in ["yaw", "pitch"]:
            angle = np.deg2rad(30)
            matrix = np.eye(4)
            if axis == "yaw":
                matrix[:3, :3] = [[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]]
            else:
                matrix[:3, :3] = [[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]]
            result = gaze.head_orientation(matrix)
            self.assertFalse(result["facing_camera"])
            self.assertAlmostEqual(result[axis], 30)
        self.assertIsNone(gaze.head_orientation(None)["facing_camera"])

    def test_eye_geometry_vertical_horizontal_scale_and_blink(self):
        points = np.full((478, 2), .5)
        for outer, inner, upper, lower, iris, x in [(33, 133, 159, 145, 468, .3), (362, 263, 386, 374, 473, .6)]:
            points[outer], points[inner] = [x, .4], [x + .1, .4]
            points[upper], points[lower], points[iris] = [x + .05, .38], [x + .05, .42], [x + .05, .4]
        def extract(values):
            return gaze.eye_features([SimpleNamespace(x=x, y=y) for x, y in values], 1000, 1000)
        np.testing.assert_allclose(extract(points)["values"], [0, 0, 0, 0], atol=1e-10)
        points[[468, 473], 1] += .01
        for scale in [.5, 1]:
            np.testing.assert_allclose(extract(points * scale + .1)["values"], [0, .1, 0, .1], atol=1e-10)
        points[[468, 473], 0] += .01
        np.testing.assert_allclose(extract(points)["values"], [.1, .1, .1, .1], atol=1e-10)
        points[[159, 145], 1] = .4
        self.assertIsNone(extract(points)["values"])


class ConciseFeedbackTests(unittest.TestCase):
    def test_overview_repairs_long_output_and_renders_three_actions(self):
        good = {"keep": "You explained your own role.", "actions": ["Name the problem.", "Describe your action.", "State the measured result."]}
        bad = copy.deepcopy(good); bad["actions"][0] = "word " * 26
        with patch.object(llm, "_complete", side_effect=[json.dumps(bad), json.dumps(good)]) as complete:
            output = llm.generate_overview("job", [], {}, [])
        self.assertEqual(complete.call_count, 2)
        self.assertIn("\n3. State the measured result.", output)
        self.assertLessEqual(len(output.split()), 107)

    def test_invalid_overview_stops_after_one_repair(self):
        with patch.object(llm, "_complete", return_value='{"keep":"Fine","actions":["Try again"]}') as complete:
            with self.assertRaises(ValueError):
                llm.generate_overview("", [], {}, [])
        self.assertEqual(complete.call_count, 2)

    def test_overlong_answer_notes_are_repaired(self):
        good = {"adequacy": 70, "specificity": 70, "structure": 70, "ambiguity_penalty": 0, "overall": 70, "notes": "Clear example. Name the result next time.", "suggested_answer": "My example. [Add the actual outcome.]"}
        bad = {**good, "notes": "word " * 46}
        with patch.object(llm, "_complete", side_effect=[json.dumps(bad), json.dumps(good)]):
            self.assertEqual(llm.score_answer("question", "", "answer"), good)
