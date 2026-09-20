import json
import re
from typing import Any

from openai import OpenAI

from backend.config import settings


def _client() -> OpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
    return OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def generate_questions(job_text: str, company: str | None, role_title: str | None) -> list[dict[str, Any]]:
    prompt = f"""You are an expert interview coach. Given this job listing, generate 12-15 likely pre-screening video interview questions.

Company: {company or "Unknown"}
Role: {role_title or "Unknown"}

Job listing:
{job_text}

Return JSON array only. Each item:
{{
  "id": "q1",
  "question": "...",
  "type": "behavioral|technical|motivation|situational|culture",
  "likelihood": 1-5,
  "rationale": "why this might appear",
  "scoring_hints": "what a strong answer should include"
}}
"""
    response = _client().chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
    )
    content = response.choices[0].message.content or "[]"
    questions = _extract_json(content)
    if not isinstance(questions, list):
        raise ValueError("LLM did not return a question list")
    for idx, q in enumerate(questions, start=1):
        q.setdefault("id", f"q{idx}")
    return questions


def score_answer(
    question: str,
    scoring_hints: str,
    transcript: str,
) -> dict[str, Any]:
    prompt = f"""Score this mock interview answer from transcript only.

Question: {question}
Scoring hints: {scoring_hints}
Transcript: {transcript or "(no speech detected)"}

Return JSON only:
{{
  "adequacy": 0-100,
  "specificity": 0-100,
  "structure": 0-100,
  "ambiguity_penalty": 0-100,
  "overall": 0-100,
  "notes": "2-3 sentences on strengths and gaps"
}}
"""
    response = _client().chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "Return valid JSON only. Be fair but constructive."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    content = response.choices[0].message.content or "{}"
    return _extract_json(content)


def generate_overview(
    job_text: str,
    per_question: list[dict[str, Any]],
    aggregate: dict[str, Any],
    weak_points: list[dict[str, Any]],
) -> str:
    payload = {
        "per_question": per_question,
        "aggregate": aggregate,
        "weak_points": weak_points,
    }
    prompt = f"""Write a concise mock interview feedback overview (3-5 short paragraphs).

Job context (truncated):
{job_text[:1500]}

Metrics JSON:
{json.dumps(payload, indent=2)}

Cover: overall impression, top strengths, top weak points to practice, and 3 concrete next steps.
Do not diagnose medical conditions. Use phrasing like "delivery signals" not clinical labels.
"""
    response = _client().chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "You are a supportive interview coach."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
    )
    return response.choices[0].message.content or "No overview generated."
