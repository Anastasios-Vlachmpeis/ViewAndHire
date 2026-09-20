import io
import math
import unittest
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.routers import gaze as api
from backend.services import face, intel_gaze as gaze


class GazeMathTests(unittest.TestCase):
    def test_camera_axis_and_angle_bands_match_reference_demo(self):
        self.assertEqual(gaze.gaze_result([0, 0, -2], 0)["state"], "toward_lens")
        self.assertEqual(gaze.gaze_result([0, 0, 2], 0)["state"], "away")
        for angle, expected in [(3, "toward_lens"), (10, "uncertain"), (25, "away")]:
            a = math.radians(angle)
            for vector in ([math.sin(a), 0, -math.cos(a)], [0, math.sin(a), -math.cos(a)]):
                result = gaze.gaze_result(vector, 0)
                self.assertEqual(result["state"], expected)
                self.assertAlmostEqual(result["angle_degrees"], angle)
                self.assertFalse(result["scoring_eligible"])

    def test_roll_compensation_and_invalid_vectors(self):
        result = gaze.gaze_result([.6, 0, -.8], 90)
        np.testing.assert_allclose(result["vector"], [0, -.6, -.8], atol=1e-5)
        for vector in ([0, 0, 0], [float("nan"), 0, -1], [0, 1]):
            self.assertEqual(gaze.gaze_result(vector, 0)["state"], "uncertain")

    def test_bgr_order_and_public_eye_model_normalization(self):
        image = np.full((2, 2, 3), [0, 127, 255], dtype=np.uint8)
        tensor = gaze.image_tensor(image, [1, 3, 2, 2])
        np.testing.assert_array_equal(tensor[0, :, 0, 0], [0, 127, 255])
        normalized = gaze.image_tensor(image, [1, 3, 2, 2], eye_state=True)
        np.testing.assert_allclose(normalized[0, :, 0, 0], [-127/255, 0, 128/255])

    def test_crop_rejects_small_and_out_of_frame_eyes(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        self.assertIsNone(gaze.eye_crop(image, np.array([0, 0]), np.array([30, 0]), 0))
        self.assertIsNone(gaze.eye_crop(image, np.array([40, 50]), np.array([45, 50]), 0))
        self.assertEqual(gaze.eye_crop(image, np.array([40, 50]), np.array([60, 50]), 0).shape, (36, 36, 3))

    def test_temporal_state_resets_on_blinks_gaps_and_new_recordings(self):
        smoother = gaze.GazeSmoother()
        known = gaze.gaze_result([0, 0, -1], 0)
        self.assertEqual(smoother.update(known, 0)["state"], "uncertain")
        self.assertEqual(smoother.update(known, .2)["state"], "toward_lens")
        self.assertEqual(smoother.update(gaze.uncertain("blink"), .4)["state"], "uncertain")
        self.assertEqual(smoother.update(known, .6)["state"], "uncertain")
        self.assertEqual(smoother.update(known, .8)["state"], "toward_lens")
        self.assertEqual(smoother.update(known, 2)["state"], "uncertain")
        self.assertEqual(gaze.GazeSmoother().update(known, 0)["state"], "uncertain")

    def test_model_estimates_never_enter_aggregate_scores(self):
        frames = [{"face_detected": True, "eye_contact": gaze.gaze_result([0, 0, -1], 0)} for _ in range(30)]
        result = face.summarize_frames(frames)
        self.assertEqual(result["eye_contact_ratio"], 1)
        self.assertEqual(result["eye_contact_coverage"], 1)
        self.assertIsNone(result["score"])
        self.assertFalse(result["scoring_enabled"])


class InferenceWiringTests(unittest.TestCase):
    def setUp(self):
        # Real image crop/preprocessing math with deterministic neural outputs.
        self.estimator = gaze.IntelGazeEstimator.__new__(gaze.IntelGazeEstimator)
        self.estimator.lock = Lock()
        self.gaze_net = MagicMock()
        self.gaze_net.input.return_value.shape = [1, 3, 60, 60]
        self.gaze_net.return_value = {"gaze_vector": np.array([[0, 0, -1]])}
        self.estimator.models = {
            "head-pose-estimation-adas-0001": SimpleNamespace(output=lambda name: name),
            gaze.MODEL_NAME: self.gaze_net,
        }
        points = np.full((35, 2), .5)
        points[:4] = [[.3, .35], [.45, .35], [.55, .35], [.7, .35]]
        self.outputs = {
            "face-detection-retail-0004": {"detection": np.array([[0, 1, .99, .25, .2, .75, .8]])},
            "head-pose-estimation-adas-0001": {name: np.array([value]) for name, value in
                                                [("angle_y_fc", 2), ("angle_p_fc", -3), ("angle_r_fc", 10)]},
            "facial-landmarks-35-adas-0002": {"landmarks": points},
            "open-closed-eye-0001": {"19": np.array([.05, .95])},
        }
        self.estimator._image_infer = lambda name, image: self.outputs[name]
        pattern = ((np.indices((240, 320)).sum(axis=0) // 3) % 2 * 255).astype(np.uint8)
        self.image = np.repeat(pattern[..., None], 3, axis=2)

    def test_full_frame_runs_matching_pose_and_eye_models(self):
        output = self.estimator.analyze(self.image)
        self.assertTrue(output["face_detected"])
        self.assertTrue(output["head_pose"]["facing_camera"])
        self.assertEqual(output["eye_contact"]["state"], "toward_lens")
        inputs = self.gaze_net.call_args.args[0]
        np.testing.assert_array_equal(inputs["head_pose_angles"], [[2, -3, 0]])
        self.assertEqual(inputs["left_eye_image"].shape, (1, 3, 60, 60))

    def test_closed_eyes_skip_gaze_and_do_not_become_away(self):
        self.outputs["open-closed-eye-0001"]["19"] = np.array([.95, .05])
        output = self.estimator.analyze(self.image)
        self.assertEqual(output["eye_contact"]["reason"], "blink_or_unclear_eyes")
        self.gaze_net.assert_not_called()

    def test_large_head_turns_and_multiple_faces_abstain(self):
        self.outputs["head-pose-estimation-adas-0001"]["angle_y_fc"] = np.array([60])
        self.assertEqual(self.estimator.analyze(self.image)["eye_contact"]["reason"], "head_pose_out_of_range")
        detections = self.outputs["face-detection-retail-0004"]
        detections["detection"] = np.repeat(detections["detection"], 2, axis=0)
        self.assertEqual(self.estimator.analyze(self.image)["eye_contact"]["reason"], "multiple_faces")

    def test_busy_inference_is_rejected_instead_of_queued(self):
        with self.estimator.lock:
            with self.assertRaises(gaze.GazeUnavailable):
                self.estimator.analyze(self.image, blocking=False)


class LiveFrameApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)

    def jpeg(self, size=(64, 48)):
        stream = io.BytesIO()
        Image.new("RGB", size).save(stream, format="JPEG")
        return stream.getvalue()

    def post(self, data, content_type="image/jpeg"):
        return self.client.post("/api/gaze/frame", content=data, headers={"Content-Type": content_type})

    def test_valid_request_returns_model_data_without_persistence(self):
        estimator = MagicMock()
        estimator.analyze.return_value = {"face_detected": False, "eye_contact": gaze.uncertain("face_unavailable")}
        with patch.object(gaze, "get_estimator", return_value=estimator):
            result = self.post(self.jpeg())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["eye_contact"]["state"], "uncertain")
        self.assertGreaterEqual(result.json()["processing_ms"], 0)
        self.assertEqual(estimator.analyze.call_args.args[0].shape, (48, 64, 3))
        self.assertFalse(estimator.analyze.call_args.kwargs["blocking"])

    def test_bad_images_and_oversized_payloads_fail_before_inference(self):
        with patch.object(gaze, "get_estimator") as load:
            self.assertEqual(self.post(b"invalid").status_code, 400)
            self.assertEqual(self.post(self.jpeg((1281, 720))).status_code, 400)
            self.assertEqual(self.post(b"x" * (api.MAX_BYTES + 1)).status_code, 413)
            self.assertEqual(self.post(b"x", "text/plain").status_code, 415)
            load.assert_not_called()
        self.assertFalse(api._frame_lock.locked())

    def test_expression_is_returned_and_its_failure_does_not_hide_gaze(self):
        estimator = MagicMock()
        estimator.analyze.side_effect = lambda *args, **kwargs: {"face_detected": True, "eye_contact": gaze.uncertain("borderline_gaze")}
        with patch.object(gaze, "get_estimator", return_value=estimator), patch.object(api, "live_expression", return_value={"expression": "happy", "expression_confidence": .8}):
            result = self.post(self.jpeg()).json()
            self.assertEqual(result["expression"], "happy")
        with patch.object(gaze, "get_estimator", return_value=estimator), patch.object(api, "live_expression", side_effect=RuntimeError("model failed")), self.assertLogs(api.logger, "ERROR"):
            response = self.post(self.jpeg())
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json()["expression"])
            self.assertEqual(response.json()["eye_contact"]["state"], "uncertain")

    def test_model_failure_and_busy_requests_are_retryable(self):
        with api._frame_lock:
            self.assertEqual(self.post(self.jpeg()).status_code, 503)
        with patch.object(gaze, "get_estimator", side_effect=gaze.GazeUnavailable("missing weights")):
            self.assertEqual(self.post(self.jpeg()).status_code, 503)
        self.assertFalse(api._frame_lock.locked())
