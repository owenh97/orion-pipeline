"""Tests for the decision logic.

This is the part of the system that must never drift silently: it turns risk
ratings into a regulatory outcome. The LLM is not involved here, so these tests
are fully deterministic and they pin the policy, not the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orion.models import (  # noqa: E402
    AuthorizationLevel,
    Coverage,
    DimensionAssessment,
    Evidence,
    FollowUpQuestion,
    Severity,
)
from orion.stages import aggregate  # noqa: E402

DIMENSIONS = [
    {"key": "a", "name": "A", "description": "", "weight": 2.0},
    {"key": "b", "name": "B", "description": "", "weight": 1.0},
]


def make(dim: str, score: int, severity=Severity.MODERATE, confidence=0.8, evidenced=True):
    return DimensionAssessment(
        dimension=dim,
        score=score,
        severity=severity,
        rationale="test",
        evidence=[Evidence(chunk_id="X:001")] if evidenced else [],
        confidence=confidence,
    )


def healthy_coverage(**overrides):
    base = dict(
        documents_referenced=3,
        documents_total=3,
        chunks_total=40,
        dimensions_assessed=2,
        dimensions_total=2,
        mean_confidence=0.8,
        evidence_backed_dimensions=2,
    )
    base.update(overrides)
    return Coverage(**base)


# --- composite score -------------------------------------------------------


def test_composite_is_weight_normalised():
    # a=60 at weight 2, b=30 at weight 1 -> (60*2 + 30*1) / 3 = 50
    score, breakdown = aggregate.composite_score([make("a", 60), make("b", 30)], DIMENSIONS)
    assert score == pytest.approx(50.0, abs=0.01)
    assert breakdown["a"] == pytest.approx(40.0, abs=0.01)
    assert breakdown["b"] == pytest.approx(10.0, abs=0.01)


def test_composite_renormalises_when_a_dimension_is_absent():
    """Dropping a dimension must not silently deflate the score toward zero."""
    score, _ = aggregate.composite_score([make("a", 60)], DIMENSIONS)
    assert score == pytest.approx(60.0, abs=0.01)


def test_composite_is_bounded():
    score, _ = aggregate.composite_score([make("a", 100), make("b", 100)], DIMENSIONS)
    assert 0.0 <= score <= 100.0


# --- thresholds ------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.0, AuthorizationLevel.FULL_AUTHORISATION),
        (19.9, AuthorizationLevel.FULL_AUTHORISATION),
        (20.0, AuthorizationLevel.AUTHORISATION_WITH_CONDITIONS),
        (39.9, AuthorizationLevel.AUTHORISATION_WITH_CONDITIONS),
        (40.0, AuthorizationLevel.PROVISIONAL_AUTHORISATION),
        (60.0, AuthorizationLevel.REFER_TO_COMMITTEE),
        (80.0, AuthorizationLevel.DECLINE),
        (100.0, AuthorizationLevel.DECLINE),
    ],
)
def test_threshold_boundaries(score, expected):
    assert aggregate.level_from_score(score) is expected


# --- gates -----------------------------------------------------------------


def test_critical_finding_escalates_a_clean_average():
    """The failure mode this gate exists to prevent: five good dimensions
    averaging away one disqualifying one."""
    assessments = [make("a", 5, Severity.LOW), make("b", 5, Severity.CRITICAL)]
    gates = aggregate.evaluate_gates(assessments, healthy_coverage(), [], [])
    assert any(g[0] == "G1_CRITICAL_FINDING" for g in gates)

    final, ids = aggregate.apply_gates(AuthorizationLevel.FULL_AUTHORISATION, gates)
    assert final is AuthorizationLevel.REFER_TO_COMMITTEE


def test_low_confidence_forces_insufficient_information():
    coverage = healthy_coverage(mean_confidence=0.2)
    gates = aggregate.evaluate_gates([make("a", 10)], coverage, [], [])
    final, _ = aggregate.apply_gates(AuthorizationLevel.FULL_AUTHORISATION, gates)
    assert final is AuthorizationLevel.INSUFFICIENT_INFORMATION


def test_unevidenced_dimensions_force_insufficient_information():
    coverage = healthy_coverage(evidence_backed_dimensions=0)
    gates = aggregate.evaluate_gates([make("a", 10, evidenced=False)], coverage, [], [])
    final, _ = aggregate.apply_gates(AuthorizationLevel.FULL_AUTHORISATION, gates)
    assert final is AuthorizationLevel.INSUFFICIENT_INFORMATION


def test_missing_documents_force_insufficient_information():
    gates = aggregate.evaluate_gates([make("a", 10)], healthy_coverage(), [], ["DOC-009"])
    final, ids = aggregate.apply_gates(AuthorizationLevel.FULL_AUTHORISATION, gates)
    assert final is AuthorizationLevel.INSUFFICIENT_INFORMATION
    assert "G4_MISSING_DOCUMENTS" in ids


def test_blocking_question_caps_at_provisional():
    q = FollowUpQuestion(question_id="Q01", dimension="a", question="?", reason="", blocking=True)
    gates = aggregate.evaluate_gates([make("a", 5)], healthy_coverage(), [q], [])
    final, _ = aggregate.apply_gates(AuthorizationLevel.FULL_AUTHORISATION, gates)
    assert final is AuthorizationLevel.PROVISIONAL_AUTHORISATION


def test_gates_never_soften_an_outcome():
    """A gate may only make the result stricter. A blocking question must not
    rescue a submission that already scored in the DECLINE band."""
    q = FollowUpQuestion(question_id="Q01", dimension="a", question="?", reason="", blocking=True)
    gates = aggregate.evaluate_gates([make("a", 95)], healthy_coverage(), [q], [])
    final, _ = aggregate.apply_gates(AuthorizationLevel.DECLINE, gates)
    assert final is AuthorizationLevel.DECLINE


def test_insufficient_information_is_absorbing():
    """Even with a critical finding present, missing evidence means we report
    that we cannot judge rather than issuing a verdict."""
    coverage = healthy_coverage(mean_confidence=0.1)
    assessments = [make("a", 90, Severity.CRITICAL)]
    gates = aggregate.evaluate_gates(assessments, coverage, [], [])
    final, _ = aggregate.apply_gates(AuthorizationLevel.DECLINE, gates)
    assert final is AuthorizationLevel.INSUFFICIENT_INFORMATION


def test_clean_submission_passes_ungated():
    assessments = [make("a", 8, Severity.LOW), make("b", 12, Severity.LOW)]
    score, _ = aggregate.composite_score(assessments, DIMENSIONS)
    gates = aggregate.evaluate_gates(assessments, healthy_coverage(), [], [])
    final, ids = aggregate.apply_gates(aggregate.level_from_score(score), gates)
    assert ids == []
    assert final is AuthorizationLevel.FULL_AUTHORISATION
