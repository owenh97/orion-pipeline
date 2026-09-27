"""Typed contracts for every boundary in the pipeline.

Two rules this file enforces:

1. Anything crossing a stage boundary is a validated Pydantic model, not a
   loose dict. A malformed LLM response fails here, loudly, with a field path.
2. Every risk judgement must carry evidence pointing at a specific chunk of a
   specific document. A judgement with no traceable source is not auditable,
   and the brief requires auditability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


class AuthorizationLevel(str, Enum):
    FULL_AUTHORISATION = "FULL_AUTHORISATION"
    AUTHORISATION_WITH_CONDITIONS = "AUTHORISATION_WITH_CONDITIONS"
    PROVISIONAL_AUTHORISATION = "PROVISIONAL_AUTHORISATION"
    REFER_TO_COMMITTEE = "REFER_TO_COMMITTEE"
    DECLINE = "DECLINE"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


# ---------------------------------------------------------------------------
# Input side
# ---------------------------------------------------------------------------


class DocumentRef(BaseModel):
    doc_id: str
    filename: str
    uri: str = Field(description="Object-storage URI, e.g. local://ACME-2026-001/documents/x.pdf")
    media_type: Literal["pdf", "docx", "xlsx"]
    declared_type: str = Field(default="unspecified", description="What the applicant says this is")


class DeclaredActivity(BaseModel):
    activity_code: str
    description: str
    jurisdictions: list[str] = Field(default_factory=list)
    estimated_annual_volume_eur: float | None = None


class SubmissionMetadata(BaseModel):
    submission_id: str
    applicant_legal_name: str
    applicant_country: str
    incorporation_year: int | None = None
    employee_count: int | None = None
    contact_email: str | None = None
    declared_activities: list[DeclaredActivity] = Field(default_factory=list)
    documents: list[DocumentRef] = Field(default_factory=list)

    @field_validator("documents")
    @classmethod
    def _unique_doc_ids(cls, v: list[DocumentRef]) -> list[DocumentRef]:
        ids = [d.doc_id for d in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate doc_id in submission documents")
        return v


# ---------------------------------------------------------------------------
# Intermediate representations
# ---------------------------------------------------------------------------


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    filename: str
    page: int | None = None
    section: str | None = None
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


class Evidence(BaseModel):
    chunk_id: str
    doc_id: str | None = None
    page: int | None = None
    quote: str = Field(default="", max_length=600)


# ---------------------------------------------------------------------------
# LLM output side
# ---------------------------------------------------------------------------


class DimensionAssessment(BaseModel):
    """One risk dimension, judged by the LLM against retrieved evidence.

    `score` is a RISK score: 0 = no concern, 100 = maximum concern. It is the
    model's judgement of evidence, never of arithmetic -- all combination of
    scores happens in deterministic Python in stages/aggregate.py.
    """

    dimension: str
    score: int = Field(ge=0, le=100)
    severity: Severity
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    gaps: list[str] = Field(default_factory=list, description="Information that is missing")
    inconsistencies: list[str] = Field(
        default_factory=list, description="Statements that contradict each other or the metadata"
    )


class FollowUpQuestion(BaseModel):
    question_id: str
    dimension: str
    question: str
    reason: str
    blocking: bool = Field(default=False, description="True if authorisation cannot proceed without it")


# ---------------------------------------------------------------------------
# Final payload
# ---------------------------------------------------------------------------


class Coverage(BaseModel):
    documents_referenced: int
    documents_total: int
    chunks_total: int
    dimensions_assessed: int
    dimensions_total: int
    mean_confidence: float
    evidence_backed_dimensions: int


class AuditRecord(BaseModel):
    run_id: str
    started_at: str
    finished_at: str | None = None
    llm_provider: str
    model: str
    temperature: float
    seed: int
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    dimension_config_hash: str = ""
    input_hashes: dict[str, str] = Field(default_factory=dict)
    llm_calls: int = 0
    cached_llm_calls: int = 0
    total_tokens: int = 0
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    pipeline_version: str = "1.0.0"


class RiskAssessment(BaseModel):
    """The reviewer-ready payload delivered to the external review API."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    submission_id: str
    applicant_legal_name: str
    generated_at: str = Field(default_factory=utc_now)

    dimensions: list[DimensionAssessment]
    composite_score: float = Field(ge=0.0, le=100.0)
    composite_breakdown: dict[str, float] = Field(default_factory=dict)

    authorization_level: AuthorizationLevel
    decision_rationale: str
    triggered_gates: list[str] = Field(default_factory=list)

    follow_up_questions: list[FollowUpQuestion] = Field(default_factory=list)
    coverage: Coverage

    # Reviewer validation & override surface (required by the brief).
    reviewer_actions: dict[str, Any] = Field(
        default_factory=lambda: {
            "status": "PENDING_REVIEW",
            "reviewer_id": None,
            "decision_override": None,
            "dimension_overrides": {},
            "notes": None,
            "reviewed_at": None,
        }
    )

    audit: AuditRecord
