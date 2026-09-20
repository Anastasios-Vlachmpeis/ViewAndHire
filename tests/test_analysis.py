import json
import tempfile
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import av
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db
from backend.config import settings
from backend.routers import interviews
from backend.services import asr, competencies, face, intel_gaze, llm, scoring, voice
from backend.services.json_io import dumps, write_json


QUESTIONS = [{"id": "q1", "question": "Describe your work", "scoring_hints": "Examples"},
             {"id": "q2", "question": "Why this role?", "scoring_hints": "Motivation"}]


def timestamp(start=0.0, end=1.0, index=0):
    return {"question_id": f"q{index + 1}", "question_index": index,
            "prep_start": start, "answer_start": start, "answer_end": end}


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def make_wav(self, samples):
        path = self.path / "audio.wav"
        scoring._write_pcm_wav(path, np.asarray(samples, dtype=np.float32))
        return path

    def test_decode_flush_preserves_length_and_exact_slice(self):
        source = self.make_wav(np.linspace(-0.5, 0.5, 16000))
        all_samples, rate = scoring._decode_audio_with_av(source)
        sliced, _ = scoring._decode_audio_with_av(source, 0.123, 0.456)
        self.assertEqual(rate, 16000)
        self.assertEqual(len(all_samples), 16000)
        np.testing.assert_array_equal(sliced, all_samples[1968:9264])

    def test_resampler_flush_at_different_rate(self):
        source = self.path / "stereo.wav"
        with wave.open(str(source), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(np.zeros((48000, 2), dtype=np.int16).tobytes())
        samples, _ = scoring._decode_audio_with_av(source)
        self.assertEqual(len(samples), 16000)

    def test_segment_clips_to_eof_and_does_not_invent_samples(self):
        source = self.make_wav(np.ones(16000) * 0.2)
        output = self.path / "segment.wav"
        for start, end, expected in [(0.25, 0.75, 8000), (0.75, 5, 4000), (2, 3, 0), (0, 0, 0)]:
            scoring.extract_segment_wav(source, start, end, output)
            with wave.open(str(output), "rb") as result:
                self.assertEqual(result.getnframes(), expected)
        with self.assertRaises(ValueError):
            scoring.extract_segment_wav(source, 1, 0, output)

    def test_silence_and_short_audio_do_not_score_as_confident(self):
        for samples in [np.zeros(16000), np.ones(900) * 0.1, np.zeros(1), np.zeros(0)]:
            result = voice.analyze_audio_segment(str(self.make_wav(samples)))
            self.assertIsNone(result["score"])
            dumps(result)

    def test_voiced_audio_metrics_are_finite(self):
        t = np.arange(32000) / 16000
        result = voice.analyze_audio_segment(str(self.make_wav(0.2 * np.sin(2 * np.pi * 150 * t))),
                                             "I built and tested the application")
        self.assertGreater(result["features"]["mean_f0"], 140)
        self.assertIsNone(result["score"])
        self.assertNotIn("confidence_proxy", result["features"])
        dumps(result)

    def test_corrupt_recording_fails(self):
        source = self.path / "broken.webm"
        source.write_bytes(b"not media")
        with self.assertRaises(av.error.InvalidDataError):
            scoring.extract_wav(source, self.path / "out.wav")


class TranscriptAndScoringTests(unittest.TestCase):
    def test_words_belong_to_one_answer_and_do_not_fall_back_to_whole_segment(self):
        transcript = {"words": [{"word": "first", "start": 0.5, "end": 1.0},
                                {"word": "second", "start": 1.0, "end": 1.5}],
                      "segments": [{"text": "first second", "start": 0, "end": 2}]}
        self.assertEqual(asr.slice_transcript(transcript, 0, 1), "first")
        self.assertEqual(asr.slice_transcript(transcript, 1, 2), "second")
        self.assertEqual(asr.slice_transcript(transcript, 1.6, 1.9), "")
        self.assertEqual(asr.slice_transcript(transcript, 1, 1), "")

    def test_asr_lazy_segments_and_numpy_values(self):
        word = SimpleNamespace(word=" hello", start=np.float32(0.2), end=np.float32(0.5))
        segment = SimpleNamespace(start=0, end=1, text=" hello ", words=[word])
        model = MagicMock()
        model.transcribe.return_value = (iter([segment]), SimpleNamespace(language="en", duration=np.float32(1)))
        with patch.object(asr, "_get_model", return_value=model):
            result = asr.transcribe_audio(Path("unused.wav"))
        self.assertEqual(result["text"], "hello")
        dumps(result)

    def test_recover_legacy_missing_end(self):
        ts = timestamp(30, 0)
        repaired, warnings = scoring.normalize_timestamps([ts], QUESTIONS, 149.5)
        self.assertEqual(repaired[0]["answer_end"], 149.5)
        self.assertEqual(ts["answer_end"], 0)
        self.assertEqual(len(warnings), 1)

    def test_invalid_timestamps_rejected(self):
        invalid = [[timestamp(2, 1)], [timestamp(-1, 2)], [timestamp(float("nan"), 2)],
                   [timestamp(0, 20)], [timestamp(), timestamp()], [],
                   [timestamp(0, 2), timestamp(1, 3, 1)]]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                scoring.normalize_timestamps(values, QUESTIONS, 10)

    def test_skipped_and_prep_only_answers_remain_empty(self):
        for ts in [timestamp(2, 2), timestamp(0, 0)]:
            result, _ = scoring.normalize_timestamps([ts], QUESTIONS, 10)
            self.assertEqual(result[0]["answer_start"], result[0]["answer_end"])

    def test_aggregate_weights_and_missing_modalities(self):
        item = {"question_id": "q1", "answer_quality": {"overall": 80},
                "speech_delivery": {"score": 60}, "face_gaze": {"score": 40}}
        self.assertEqual(scoring.aggregate_scores([item], "both")["overall"], 80)
        item["face_gaze"]["score"] = None
        self.assertEqual(scoring.aggregate_scores([item], "mic")["overall"], 80)
        self.assertNotIn("q1_face", [m["metric"] for m in scoring.compute_weak_points([item])])

    def test_json_rejects_nonfinite_numbers_and_handles_numpy(self):
        self.assertEqual(json.loads(dumps({"x": np.float32(1), "a": np.array([2])})), {"x": 1, "a": [2]})
        with self.assertRaises(ValueError):
            dumps({"x": float("nan")})

    def test_answer_scores_validated_and_empty_answers_do_not_call_service(self):
        with patch.object(llm, "_complete") as complete:
            self.assertEqual(llm.score_answer("Q", "", "")["overall"], 0)
            complete.assert_not_called()
        for payload in ["{}", "[]", '{"overall": "bad"}', '{"overall": 101}']:
            with patch.object(llm, "_complete", return_value=payload), self.assertRaises(ValueError):
                llm.score_answer("Q", "", "An answer")
        score = {"adequacy": 70, "specificity": 70, "structure": 70,
                 "ambiguity_penalty": 0, "overall": 70, "notes": "Useful example", "suggested_answer": "An answer. [Add the actual outcome.]"}
        with patch.object(llm, "_complete", return_value="```json\n" + json.dumps(score) + "\n```"):
            self.assertEqual(llm.score_answer("Q", "", "An answer"), score)

    def test_empty_completion_fails_explicitly(self):
        client = MagicMock()
        client.chat.completions.create.return_value.choices = []
        with patch.object(llm, "_client", return_value=client), self.assertRaises(ValueError):
            llm._complete([])


class FaceTests(unittest.TestCase):
    def setUp(self):
        estimator = MagicMock()
        estimator.analyze.return_value = {"face_detected": False, "eye_contact": intel_gaze.uncertain("face_unavailable")}
        self.gaze_patch = patch.object(intel_gaze, "get_estimator", return_value=estimator)
        self.gaze_patch.start()
        self.addCleanup(self.gaze_patch.stop)

    def fake_container(self):
        container = MagicMock()
        container.__enter__.return_value = container
        container.start_time = 0
        container.streams.video = [SimpleNamespace(average_rate=30)]
        container.decode.return_value = iter([SimpleNamespace(time=t, to_ndarray=lambda **_: np.zeros((16, 16, 3), np.uint8))
                                              for t in [0, 0, 0.001, 0.3, 0.2, 0.7]])
        return container

    def test_repeated_recordings_have_separate_trackers_and_monotonic_timestamps(self):
        trackers, containers = [], []
        def create_tracker():
            tracker = MagicMock()
            tracker.__enter__.return_value = tracker
            tracker.detect_for_video.return_value = SimpleNamespace(face_landmarks=[])
            trackers.append(tracker)
            return tracker
        def create_container(*args):
            container = self.fake_container()
            containers.append(container)
            return container
        with patch.object(face, "_get_landmarker", side_effect=create_tracker), patch.object(face.av, "open", side_effect=create_container):
            for _ in range(2):
                face.analyze_video(Path("clip.webm"), sample_fps=10000)
        self.assertEqual(len(trackers), 2)
        for tracker, container in zip(trackers, containers):
            times = [call.args[1] for call in tracker.detect_for_video.call_args_list]
            self.assertEqual(times[0], 0)
            self.assertTrue(all(a < b for a, b in zip(times, times[1:])))
            tracker.__exit__.assert_called_once()
            container.__exit__.assert_called_once()

    def test_tracker_and_container_close_when_inference_fails(self):
        tracker = MagicMock()
        tracker.__enter__.return_value = tracker
        tracker.detect_for_video.side_effect = RuntimeError("failure")
        container = self.fake_container()
        with patch.object(face, "_get_landmarker", return_value=tracker), patch.object(face.av, "open", return_value=container):
            with self.assertRaises(RuntimeError):
                face.analyze_video(Path("clip.webm"))
        tracker.__exit__.assert_called_once()
        container.__exit__.assert_called_once()

    def test_movement_coefficients_abstain_when_missing_and_never_describe_emotion(self):
        names = ["jawOpen", "mouthSmileLeft", "mouthSmileRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight", "eyeBlinkLeft", "eyeBlinkRight"]
        shapes = [SimpleNamespace(category_name=name, score=.1) for name in names]
        result = SimpleNamespace(face_blendshapes=[shapes])
        self.assertEqual(face.movement_summary(result)["state"], "low")
        shapes[0].score = .8
        movement = face.movement_summary(result)
        self.assertEqual(movement["observations"], ["jaw opening"])
        self.assertFalse(movement["scoring_enabled"])
        shapes[0].score = float("nan")
        self.assertEqual(face.movement_summary(result)["state"], "uncertain")
        self.assertEqual(face.movement_summary(None)["state"], "uncertain")

    def test_no_face_and_no_frames_are_unavailable_not_poor_eye_contact(self):
        self.assertIsNone(face.summarize_frames([])["score"])
        self.assertIsNone(face.summarize_frames([{"face_detected": False}])["score"])


class PipelineAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.config = patch.object(settings, "data_dir", self.path)
        self.config.start()
        self.addCleanup(self.config.stop)
        db.init_db()
        listing = db.create_listing("A sample role for testing")
        self.interview = db.create_interview(listing["id"], "bank", {"record_mode": "mic"}, QUESTIONS)
        self.iid = self.interview["id"]
        self.directory = settings.interviews_dir / self.iid
        app = FastAPI()
        app.include_router(interviews.router)
        self.client = TestClient(app)

    def test_full_pipeline_with_real_audio_and_stubbed_external_models(self):
        # WAV content is intentionally decoded through the same media entry point.
        t = np.arange(32000) / 16000
        scoring._write_pcm_wav(self.directory / "recording.webm", 0.2 * np.sin(2 * np.pi * 150 * t))
        transcript = {"text": "I built this", "words": [{"word": "I built this", "start": 0.2, "end": 1.5}]}
        score = {"adequacy": 80, "specificity": 70, "structure": 75, "ambiguity_penalty": 0, "overall": 75, "notes": "Good", "suggested_answer": "I built this. [Add the actual outcome.]"}
        for mode in ["mic", "both", "camera"]:
            with patch.object(asr, "transcribe_audio", return_value=transcript), \
                 patch.object(competencies, "assess_answer", return_value=competencies.unavailable()), \
                 patch.object(llm, "_complete", side_effect=[json.dumps(score), json.dumps({"keep": "Specific example", "actions": ["Describe your role.", "Explain the result.", "Practise once aloud."]})]), \
                 patch.object(face, "analyze_video", return_value={"frames": [], "summary": {"score": None}}) as video:
                result = scoring.run_analysis(self.iid, self.directory, QUESTIONS,
                                              [timestamp(0, 2), timestamp(2, 2, 1)], mode, "Role")
            self.assertEqual(result["per_question"][0]["answer_quality"]["overall"], 75)
            self.assertEqual(result["per_question"][0]["answer_quality"]["suggested_answer"], score["suggested_answer"])
            self.assertIsNone(result["per_question"][1]["answer_quality"]["suggested_answer"])
            self.assertEqual(result["per_question"][1]["transcript"], "")
            self.assertIsNone(result["per_question"][1]["speech_delivery"]["score"])
            self.assertEqual(video.call_count, int(mode != "mic"))
            self.assertEqual(json.loads((self.directory / "progress.json").read_text())["stage"], "done")
            json.loads((self.directory / "analysis.json").read_text())

    def test_invalid_upload_does_not_write_recording(self):
        for data in ["not json", "{}", "[]", json.dumps([timestamp(2, 1)])]:
            result = self.client.post(f"/api/interviews/{self.iid}/upload", data={"timestamps": data},
                                      files={"recording": ("recording.webm", b"media")})
            self.assertEqual(result.status_code, 400)
        self.assertFalse((self.directory / "recording.webm").exists())

    def test_upload_validates_and_persists_calibration(self):
        calibration = [{"target": target, "start": i * 3 + 1, "end": i * 3 + 3}
                       for i, target in enumerate(["lens", "screen", "lens_check", "screen_check"])]
        for invalid in ["null", "bad", json.dumps(calibration[:-1])]:
            result = self.client.post(f"/api/interviews/{self.iid}/upload",
                                      data={"timestamps": json.dumps([timestamp(12, 14)]), "calibration": invalid},
                                      files={"recording": ("recording.webm", b"media")})
            self.assertEqual(result.status_code, 400)
        with patch.object(interviews, "_run_analysis_job"):
            result = self.client.post(f"/api/interviews/{self.iid}/upload",
                                      data={"timestamps": json.dumps([timestamp(12, 14)]), "calibration": json.dumps(calibration)},
                                      files={"recording": ("recording.webm", b"media")})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(json.loads((self.directory / "calibration.json").read_text()), calibration)

    def test_retry_resets_stale_progress_and_blocks_duplicate_job(self):
        (self.directory / "recording.webm").write_bytes(b"media")
        write_json(self.directory / "timestamps.json", [timestamp()])
        write_json(self.directory / "progress.json", {"stage": "error"})
        with patch.object(interviews, "_run_analysis_job"):
            self.assertEqual(self.client.post(f"/api/interviews/{self.iid}/analyze").status_code, 200)
            self.assertEqual(self.client.get(f"/api/interviews/{self.iid}/progress").json()["stage"], "queued")
            self.assertEqual(self.client.post(f"/api/interviews/{self.iid}/analyze").status_code, 409)

    def test_claim_is_atomic(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: db.claim_analysis(self.iid), range(4)))
        self.assertEqual(sum(results), 1)

    def test_interrupted_job_becomes_retryable_after_restart(self):
        self.assertTrue(db.claim_analysis(self.iid))
        interviews.recover_interrupted_jobs()
        self.assertEqual(db.get_interview(self.iid)["status"], "error")
        self.assertIn("restart", self.client.get(f"/api/interviews/{self.iid}/progress").json()["message"])
        self.assertTrue(db.claim_analysis(self.iid))

    def test_done_progress_waits_for_database_completion(self):
        db.update_interview_status(self.iid, "analyzing")
        write_json(self.directory / "progress.json", {"stage": "done", "percent": 100})
        self.assertEqual(self.client.get(f"/api/interviews/{self.iid}/progress").json()["stage"], "saving")
        db.update_interview_status(self.iid, "complete")
        self.assertEqual(self.client.get(f"/api/interviews/{self.iid}/progress").json()["stage"], "done")

    def test_save_route_and_results_status(self):
        self.assertEqual(self.client.post(f"/api/interviews/{self.iid}/save").status_code, 200)
        self.assertTrue(db.get_interview(self.iid)["saved"])
        write_json(self.directory / "analysis.json", {"aggregate": {"overall": 75}})
        self.assertEqual(self.client.get(f"/api/interviews/{self.iid}/results").status_code, 409)
        db.update_interview_status(self.iid, "complete")
        self.assertEqual(self.client.get(f"/api/interviews/{self.iid}/results").status_code, 200)

    def test_failure_is_visible_and_retryable(self):
        with patch.object(scoring, "run_analysis", side_effect=ValueError("bad audio")), self.assertLogs(interviews.logger, "ERROR"):
            interviews._run_analysis_job(self.iid, [timestamp()])
        self.assertEqual(db.get_interview(self.iid)["status"], "error")
        self.assertEqual(self.client.get(f"/api/interviews/{self.iid}/progress").json()["message"], "bad audio")
        self.assertTrue(db.claim_analysis(self.iid))


if __name__ == "__main__":
    unittest.main()
