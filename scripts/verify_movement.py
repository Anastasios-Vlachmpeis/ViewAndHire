"""Benchmark MediaPipe with/without blendshapes on identical sampled frames; no API or saved-result changes."""
import argparse
import json
import time
from datetime import datetime, timezone

import av
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from backend.config import settings
from backend.services import face
from backend.services.json_io import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interview_ids", nargs="+")
    args = parser.parse_args()
    reports = []
    for iid in args.interview_ids:
        images = []
        with av.open(str(settings.interviews_dir / iid / "recording.webm")) as video:
            for index, decoded in enumerate(video.decode(video=0)):
                if index % 30 == 0:
                    images.append(mp.Image(image_format=mp.ImageFormat.SRGB, data=decoded.to_ndarray(format="rgb24")))
                if len(images) >= 100:
                    break
        report = {"interview_id": iid, "frames": len(images)}
        for enabled in [False, True]:
            timings, states = [], []
            options = vision.FaceLandmarkerOptions(base_options=python.BaseOptions(model_asset_path=face._landmarker_model_path()),
                running_mode=vision.RunningMode.VIDEO, num_faces=1, output_facial_transformation_matrixes=True, output_face_blendshapes=enabled)
            with vision.FaceLandmarker.create_from_options(options) as model:
                for index, image in enumerate(images):
                    start = time.perf_counter()
                    result = model.detect_for_video(image, index * 1000)
                    movement = face.movement_summary(result)
                    timings.append((time.perf_counter() - start) * 1000)
                    states.append(movement["state"])
            report["blendshapes_on" if enabled else "blendshapes_off"] = {
                "median_ms": round(float(np.median(timings)), 2), "p95_ms": round(float(np.percentile(timings, 95)), 2),
                "states": {state: states.count(state) for state in set(states)}}
        reports.append(report)
        print(json.dumps(report), flush=True)
    path = settings.data_dir / "movement_verification" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, reports)


if __name__ == "__main__":
    main()
