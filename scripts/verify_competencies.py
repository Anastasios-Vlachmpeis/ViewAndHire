"""Exercise the configured feedback service with synthetic answers only; no saved recordings are read or changed."""
import json
from datetime import datetime, timezone

from backend.config import settings
from backend.services import competencies
from backend.services.json_io import write_json

CASES = [
    ("complete_setback", "Describe a setback and how you responded.",
     "Our demo failed just before the deadline. I paused and checked the logs. I chose a simpler fallback because fixing the full feature would risk missing the deadline. We delivered a working demo on time. I learned to schedule a full rehearsal before future demos."),
    ("assertion_only", "Describe a setback and how you responded.", "I am resilient and always stay calm."),
    ("technical", "Explain how you would design an A/B test for a recommendation feature.",
     "I would randomly assign users to treatment and control. I would define retention as the primary metric and monitor errors as a guardrail. I would determine sample size before starting and compare confidence intervals after the planned duration."),
]


def main():
    results = []
    for name, question, transcript in CASES:
        result = competencies.assess_answer(question, transcript)
        if result.get("status") == "unavailable":
            raise RuntimeError(f"Evidence extraction unavailable for {name}")
        items = result["assessments"]
        if name == "complete_setback":
            assert items["resilience"]["status"] == "assessed", items
        elif name == "assertion_only":
            assert all(item["level"] is None for item in items.values()), items
        else:
            assert all(items[key]["status"] == "not_assessed" for key in ["empathy", "resilience"]), items
        results.append({"name": name, "question": question, "transcript": transcript, "competency_evidence": result})
        print(json.dumps({"case": name, "states": {key: item["status"] for key, item in items.items()}}), flush=True)
    directory = settings.data_dir / "competency_verification"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"), results)


if __name__ == "__main__":
    main()
