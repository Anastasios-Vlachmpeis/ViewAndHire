"""Check real local gaze inference and latency without calling the feedback API.

python -m scripts.verify_gaze INTERVIEW_ID [INTERVIEW_ID ...]
Writes numeric diagnostics under data/gaze_verification; never replaces results.
Unlabelled recordings establish runtime behavior, not eye-contact accuracy.
"""
import argparse
import time
from collections import Counter
from datetime import datetime, timezone

import av
import numpy as np

from backend import db
from backend.config import settings
from backend.services import intel_gaze
from backend.services.json_io import dumps, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interview_ids", nargs="+")
    args = parser.parse_args()
    loaded = time.perf_counter()
    model = intel_gaze.get_estimator()
    load_seconds = round(time.perf_counter() - loaded, 3)
    reports = []
    for interview_id in args.interview_ids:
        if not db.get_interview(interview_id):
            raise ValueError("Unknown interview")
        latencies, angles, rows = [], [], []
        smoother = intel_gaze.GazeSmoother()
        with av.open(str(settings.interviews_dir / interview_id / "recording.webm")) as video:
            next_time = 0
            started = time.perf_counter()
            for decoded in video.decode(video=0):
                timestamp = float(decoded.time or 0)
                if timestamp < next_time:
                    continue
                next_time = timestamp + .2
                frame = decoded.to_ndarray(format="bgr24")
                start = time.perf_counter()
                result = model.analyze(frame)
                latencies.append((time.perf_counter() - start) * 1000)
                contact = smoother.update(result["eye_contact"], timestamp)
                if contact["angle_degrees"] is not None:
                    angles.append(contact["angle_degrees"])
                rows.append({"time": timestamp, "eye_contact": contact, "head_pose": result["head_pose"]})
        report = {"interview_id": interview_id, "samples": len(rows), "model_load_seconds": load_seconds,
                  "elapsed_seconds": round(time.perf_counter() - started, 2),
                  "inference_median_ms": round(float(np.median(latencies)), 2),
                  "inference_p95_ms": round(float(np.percentile(latencies, 95)), 2),
                  "states": dict(Counter(row["eye_contact"]["state"] for row in rows)),
                  "uncertain_reasons": dict(Counter(row["eye_contact"]["reason"] for row in rows if row["eye_contact"]["reason"])),
                  "angle_range_degrees": [min(angles), max(angles)] if angles else [],
                  "accuracy_validated": False, "feedback_api_called": False, "saved_results_changed": False}
        print(dumps(report), flush=True)
        reports.append({"summary": report, "frames": rows})
    target = settings.data_dir / "gaze_verification" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ.json")
    write_json(target, reports)
    print(f"Diagnostics: {target}", flush=True)


if __name__ == "__main__":
    main()
