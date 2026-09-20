import json
import re
from typing import Any

from openai import OpenAI

from backend.config import settings
from backend.models import AnswerScore, QuestionItem
from backend.services.json_io import dumps


def _client() -> OpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
    return OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=120.0)


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _complete(messages: list[dict[str, str]], json_mode: bool = False) -> str:
    kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "messages": messages,
        "max_completion_tokens": 4000,
    }
    # GPT-5.6 family only allows default temperature (1) and is a reasoning model.
    model = settings.openai_model.lower()
    if model.startswith("gpt-5") or model.startswith("gpt-6"):
        kwargs["reasoning_effort"] = "none"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = _client().chat.completions.create(**kwargs)
    if not response.choices or not response.choices[0].message.content:
        raise ValueError("The feedback service returned an empty response. Please retry analysis.")
    return response.choices[0].message.content


def generate_questions(job_text: str, company: str | None, role_title: str | None) -> list[dict[str, Any]]:
    prompt = f"""You are an expert interview coach. Given this job listing, generate 12-15 likely pre-screening video interview questions.

Company: {company or "Unknown"}
Role: {role_title or "Unknown"}

Job listing:
{job_text}

Return a JSON object with a "questions" array. Each item:
{{
  "id": "q1",
  "question": "...",
  "type": "behavioral|technical|motivation|situational|culture",
  "likelihood": 1-5,
  "rationale": "why this might appear",
  "scoring_hints": "what a strong answer should include"
}}
"""
    content = _complete(
        [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        json_mode=True,
    )
    parsed = _extract_json(content or "{}")
    questions = parsed.get("questions", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(questions, list) or not questions:
        raise ValueError("LLM did not return a question list")
    for idx, q in enumerate(questions, start=1):
        if not isinstance(q, dict):
            raise ValueError("LLM returned an invalid question")
        q.setdefault("id", f"q{idx}")
    questions = [QuestionItem.model_validate(q).model_dump() for q in questions]
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("LLM returned duplicate question IDs")
    return questions


def score_answer(
    question: str,
    scoring_hints: str,
    transcript: str,
) -> dict[str, Any]:
    if not transcript.strip():
        return {"adequacy": 0.0, "specificity": 0.0, "structure": 0.0,
                "ambiguity_penalty": 0.0, "overall": 0.0, "notes": "No answer was recorded."}
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
    content = _complete(
        [
            {"role": "system", "content": "Return valid JSON only. Be fair but constructive."},
            {"role": "user", "content": prompt},
        ],
        json_mode=True,
    )
    return AnswerScore.model_validate(_extract_json(content)).model_dump()


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
{dumps(payload)}

Cover: overall impression, top strengths, top weak points to practice, and 3 concrete next steps.
Do not diagnose medical conditions. Use phrasing like "delivery signals" not clinical labels.
"""
    return _complete(
        [
            {"role": "system", "content": "You are a supportive interview coach."},
            {"role": "user", "content": prompt},
        ]
    ) or "No overview generated."
