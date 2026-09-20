from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints


class ListingCreate(BaseModel):
    job_text: str = Field(min_length=20)
    company: str | None = None
    role_title: str | None = None
    custom_questions: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]] = Field(
        default_factory=list, max_length=20
    )


class QuestionItem(BaseModel):
    id: str
    question: str
    type: str
    likelihood: int = Field(ge=1, le=5)
    rationale: str
    scoring_hints: str


class InterviewSettings(BaseModel):
    question_count: int = Field(ge=1, le=20)
    selection_mode: Literal["predetermined", "random"]
    prep_seconds: int = Field(ge=0, le=300, default=30)
    answer_seconds: int = Field(ge=15, le=600, default=120)
    record_mode: Literal["both", "mic", "camera"] = "both"
    selected_question_ids: list[str] = Field(default_factory=list)


class InterviewCreate(BaseModel):
    listing_id: str
    question_bank_id: str
    settings: InterviewSettings


class QuestionTimestamp(BaseModel):
    question_id: str
    question_index: int = Field(ge=0)
    prep_start: float = Field(ge=0, allow_inf_nan=False)
    answer_start: float = Field(ge=0, allow_inf_nan=False)
    answer_end: float = Field(ge=0, allow_inf_nan=False)


class AnswerScore(BaseModel):
    adequacy: float = Field(ge=0, le=100, allow_inf_nan=False)
    specificity: float = Field(ge=0, le=100, allow_inf_nan=False)
    structure: float = Field(ge=0, le=100, allow_inf_nan=False)
    ambiguity_penalty: float = Field(ge=0, le=100, allow_inf_nan=False)
    overall: float = Field(ge=0, le=100, allow_inf_nan=False)
    notes: str
    suggested_answer: str | None = None


class UploadPayload(BaseModel):
    timestamps: list[QuestionTimestamp]


class AnalysisProgress(BaseModel):
    stage: str
    percent: int
    message: str


class WeakPoint(BaseModel):
    metric: str
    score: float
    label: str


class InterviewSummary(BaseModel):
    id: str
    status: str
    saved: bool
    created_at: str
    settings: dict[str, Any]
    aggregate_score: float | None = None
    role_title: str | None = None
    company: str | None = None
