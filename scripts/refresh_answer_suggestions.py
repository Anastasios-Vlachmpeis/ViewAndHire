"""Add grounded suggestions to completed results, preserving scores and backing up originals."""
import argparse
import json
import shutil
from datetime import datetime, timezone

from backend import db
from backend.config import settings
from backend.services import llm
from backend.services.json_io import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interview_ids", nargs="+")
    args = parser.parse_args()
    for interview_id in args.interview_ids:
        interview = db.get_interview(interview_id)
        if not interview or interview["status"] != "complete":
            raise ValueError(f"Expected completed interview: {interview_id}")
        directory = settings.interviews_dir / interview_id
        path = directory / "analysis.json"
        original = path.read_bytes()
        analysis = json.loads(original)
        questions = {q["id"]: q for q in interview["selected_questions"]}
        for answer in analysis["per_question"]:
            hints = questions.get(answer["question_id"], {}).get("scoring_hints", "")
            result = llm.score_answer(answer["question"], hints, answer.get("transcript", ""))
            answer["answer_quality"]["suggested_answer"] = result["suggested_answer"]
        # Do not overwrite a concurrent reanalysis.
        if path.read_bytes() != original or db.get_interview(interview_id)["status"] != "complete":
            raise RuntimeError("Results changed during generation; retry when analysis is idle")
        backup = directory / "analysis_backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup.mkdir(parents=True)
        shutil.copy2(path, backup / "analysis.json")
        write_json(path, analysis)
        print(json.dumps({"interview_id": interview_id, "suggestions": [q["answer_quality"]["suggested_answer"] for q in analysis["per_question"]]}), flush=True)


if __name__ == "__main__":
    main()
