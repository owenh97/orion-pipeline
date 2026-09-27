"""Stage 5 - Aggregate.

This module contains no LLM calls and no randomness. Given the same dimension
assessments it always produces the same composite score and the same
authorisation level. That is deliberate and it is the single most important
design decision in the pipeline:

    The model judges evidence. Python makes the decision.

A regulator can be shown this file and can verify, by reading it, exactly how a
set of risk ratings becomes an authorisation outcome. If an LLM produced the
final level, that guarantee would not exist -- the same submission could yield
a different outcome on a different day, and the reasoning would be unfalsifiable.

Two mechanisms combine:

1. A weighted composite score. Dimensions carry different weights because they
   are not equally consequential: a financial-crime control failure is not
   equivalent to a thin business-continuity annex.

2. Hard gates, evaluated *before* the thresholds and able to override them. A
   gate encodes a rule that no averaging should be allowed to dilute -- for
   example, a single critical finding must reach a human regardless of how
   clean the other dimensions look. Averaging alone would let five strong
   dimensions bury one disqualifying one, which is precisely the failure mode
   a risk committee exists to prevent.
"""

from __future__ import annotations

from ..models import (
    AuthorizationLevel,
    Coverage,
    DimensionAssessment,
    FollowUpQuestion,
    Severity,
)

# ---------------------------------------------------------------------------
# Tunable policy. Everything the outcome depends on is visible here.
# ---------------------------------------------------------------------------

# Composite score bands (risk score: 0 = clean, 100 = severe).
#
# FULL_AUTHORISATION is deliberately absent from this table: the pipeline will
# never recommend unconditional approval. A firm applying to hold client money
# or run settlement infrastructure is being admitted to a supervised activity,
# and admission in practice always carries reporting and notification
# conditions. A system that could output "approved, nothing further required"
# would be modelling a decision this authority does not actually make.
#
# The level still exists in the enum, because a human reviewer may override to
# it. What is removed is the pipeline's ability to arrive there on its own.
THRESHOLDS: list[tuple[float, AuthorizationLevel]] = [
    (40.0, AuthorizationLevel.AUTHORISATION_WITH_CONDITIONS),
    (60.0, AuthorizationLevel.PROVISIONAL_AUTHORISATION),
    (80.0, AuthorizationLevel.REFER_TO_COMMITTEE),
    (100.1, AuthorizationLevel.DECLINE),
]

# Evidence quality floors. Below these the pipeline refuses to express an
# opinion rather than expressing a badly-founded one.
MIN_MEAN_CONFIDENCE = 0.35
MIN_EVIDENCED_FRACTION = 0.5

# A severity at this level in any single dimension cannot be averaged away.
#
# HIGH is included alongside CRITICAL. The trade-off is explicit: including it
# sends more files to committee and therefore costs reviewer time, and some of
# those files will turn out fine. That is the right side to err on. The two
# error types are not symmetric -- an unnecessary committee review costs an
# hour, while a high-severity control failure waved through by averaging costs
# the authority its credibility and, potentially, somebody's client money.
ESCALATION_SEVERITIES = {Severity.HIGH, Severity.CRITICAL}

# Ordering used when a gate caps (never raises) the outcome.
_SEVERITY_ORDER = [
    AuthorizationLevel.FULL_AUTHORISATION,
    AuthorizationLevel.AUTHORISATION_WITH_CONDITIONS,
    AuthorizationLevel.PROVISIONAL_AUTHORISATION,
    AuthorizationLevel.REFER_TO_COMMITTEE,
    AuthorizationLevel.DECLINE,
]


def _rank(level: AuthorizationLevel) -> int:
    return _SEVERITY_ORDER.index(level) if level in _SEVERITY_ORDER else len(_SEVERITY_ORDER)


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------


def composite_score(assessments: list[DimensionAssessment],
                    dimensions: list[dict]) -> tuple[float, dict[str, float]]:
    """Weighted mean of dimension risk scores.

    Weights come from the dimension config and are normalised here, so removing
    a dimension from the config does not silently rescale the result.

    Note the deliberate choice *not* to weight by the model's self-reported
    confidence. Doing so would quietly shrink the influence of exactly those
    dimensions where the evidence is thin -- which is where risk most often
    hides. Low confidence is handled instead by the coverage gate below, which
    stops the pipeline from issuing a verdict at all.
    """
    weights = {d["key"]: float(d.get("weight", 1.0)) for d in dimensions}
    by_key = {a.dimension: a for a in assessments}

    total_weight = sum(weights.get(a.dimension, 1.0) for a in assessments)
    if total_weight <= 0:
        return 0.0, {}

    breakdown: dict[str, float] = {}
    accumulated = 0.0
    for key, weight in weights.items():
        assessment = by_key.get(key)
        if assessment is None:
            continue
        contribution = assessment.score * weight / total_weight
        breakdown[key] = round(contribution, 2)
        accumulated += contribution

    return round(min(max(accumulated, 0.0), 100.0), 2), breakdown


def level_from_score(score: float) -> AuthorizationLevel:
    for ceiling, level in THRESHOLDS:
        if score < ceiling:
            return level
    return AuthorizationLevel.DECLINE


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def evaluate_gates(assessments: list[DimensionAssessment], coverage: Coverage,
                   questions: list[FollowUpQuestion], missing_docs: list[str]) -> list[tuple[str, AuthorizationLevel, str]]:
    """Return (gate_id, enforced_ceiling, human_readable_reason) for each gate
    that fires. A gate can only make the outcome stricter, never more lenient."""
    gates: list[tuple[str, AuthorizationLevel, str]] = []

    # G1 - a single severe finding always reaches a human.
    severe = [
        f"{a.dimension} ({a.severity.value})"
        for a in assessments
        if a.severity in ESCALATION_SEVERITIES
    ]
    if severe:
        gates.append((
            "G1_SEVERE_FINDING",
            AuthorizationLevel.REFER_TO_COMMITTEE,
            f"Severity at or above 'high' recorded in: {', '.join(severe)}.",
        ))

    # G2 - not enough confident, evidence-backed assessment to justify a verdict.
    evidenced_fraction = (
        coverage.evidence_backed_dimensions / coverage.dimensions_total
        if coverage.dimensions_total else 0.0
    )
    if coverage.mean_confidence < MIN_MEAN_CONFIDENCE or evidenced_fraction < MIN_EVIDENCED_FRACTION:
        gates.append((
            "G2_INSUFFICIENT_EVIDENCE",
            AuthorizationLevel.INSUFFICIENT_INFORMATION,
            f"Mean confidence {coverage.mean_confidence:.2f} "
            f"(floor {MIN_MEAN_CONFIDENCE}), evidence-backed dimensions "
            f"{coverage.evidence_backed_dimensions}/{coverage.dimensions_total} "
            f"(floor {MIN_EVIDENCED_FRACTION:.0%}).",
        ))

    # G3 - a blocking clarification must be answered before authorisation.
    blocking = [q.question_id for q in questions if q.blocking]
    if blocking:
        gates.append((
            "G3_BLOCKING_CLARIFICATION",
            AuthorizationLevel.PROVISIONAL_AUTHORISATION,
            f"Blocking clarification(s) outstanding: {', '.join(blocking)}.",
        ))

    # G4 - referenced documents that could not be retrieved.
    if missing_docs:
        gates.append((
            "G4_MISSING_DOCUMENTS",
            AuthorizationLevel.INSUFFICIENT_INFORMATION,
            f"Referenced but unretrievable: {', '.join(missing_docs)}.",
        ))

    # G5 - a materially incomplete assessment run.
    if coverage.dimensions_assessed < coverage.dimensions_total:
        gates.append((
            "G5_INCOMPLETE_RUN",
            AuthorizationLevel.INSUFFICIENT_INFORMATION,
            f"Only {coverage.dimensions_assessed} of {coverage.dimensions_total} "
            f"dimensions were assessed.",
        ))

    return gates


def apply_gates(base_level: AuthorizationLevel,
                gates: list[tuple[str, AuthorizationLevel, str]]) -> tuple[AuthorizationLevel, list[str]]:
    """INSUFFICIENT_INFORMATION is absorbing: if we do not have the evidence to
    judge, no amount of favourable arithmetic should produce an authorisation."""
    if not gates:
        return base_level, []

    if any(g[1] == AuthorizationLevel.INSUFFICIENT_INFORMATION for g in gates):
        return AuthorizationLevel.INSUFFICIENT_INFORMATION, [g[0] for g in gates]

    strictest = max((g[1] for g in gates), key=_rank)
    final = strictest if _rank(strictest) > _rank(base_level) else base_level
    return final, [g[0] for g in gates]


# ---------------------------------------------------------------------------
# Coverage + entry point
# ---------------------------------------------------------------------------


def build_coverage(assessments: list[DimensionAssessment], dimensions: list[dict],
                   chunks_total: int, documents_total: int) -> Coverage:
    referenced_docs = {ev.doc_id for a in assessments for ev in a.evidence if ev.doc_id}
    confidences = [a.confidence for a in assessments] or [0.0]
    return Coverage(
        documents_referenced=len(referenced_docs),
        documents_total=documents_total,
        chunks_total=chunks_total,
        dimensions_assessed=sum(1 for a in assessments if a.confidence > 0.0),
        dimensions_total=len(dimensions),
        mean_confidence=round(sum(confidences) / len(confidences), 3),
        evidence_backed_dimensions=sum(1 for a in assessments if a.evidence),
    )


def run(assessments: list[DimensionAssessment], dimensions: list[dict], questions: list[FollowUpQuestion],
        chunks_total: int, documents_total: int, missing_docs: list[str], logger):
    score, breakdown = composite_score(assessments, dimensions)
    coverage = build_coverage(assessments, dimensions, chunks_total, documents_total)
    base_level = level_from_score(score)
    gates = evaluate_gates(assessments, coverage, questions, missing_docs)
    final_level, gate_ids = apply_gates(base_level, gates)

    reasons = [f"Composite risk score {score:.1f}/100 falls in the '{base_level.value}' band."]
    reasons += [f"[{gid}] {reason}" for gid, _, reason in gates]
    if final_level != base_level:
        reasons.append(f"Outcome escalated from '{base_level.value}' to '{final_level.value}' by the gates above.")
    rationale = " ".join(reasons)

    logger.log("aggregate.ok", composite=score, base_level=base_level.value,
               final_level=final_level.value, gates=gate_ids,
               mean_confidence=coverage.mean_confidence)

    return score, breakdown, final_level, rationale, gate_ids, coverage
