"""Stage 4 - Assess.

One LLM call per risk dimension, run concurrently.

Why per-dimension rather than one big call:
  * Each prompt stays short and focused, which is where judgement quality is
    best and where citations stay accurate.
  * A failure or a malformed response costs one dimension, not the whole run.
  * Dimensions run in parallel, which matters under a fixed serverless
    execution limit.
  * Each dimension is independently testable and independently re-runnable.

The model is asked for judgement only. It never computes the composite score
and never chooses the authorisation level -- that is deterministic Python in
stages/aggregate.py. Keeping arithmetic out of the model is what makes the
decision reproducible and explainable to a regulator.

Every citation is verified against the real chunk index after the call.
Fabricated chunk ids are dropped and logged rather than silently trusted.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..models import Chunk, DimensionAssessment, Severity, SubmissionMetadata

_MAX_WORKERS = 6


def _evidence_block(chunks: list[Chunk]) -> str:
    if not chunks:
        return "(no passages retrieved for this dimension)"
    parts = []
    for c in chunks:
        locator = f"{c.filename}"
        if c.page is not None:
            locator += f", page {c.page}"
        elif c.section:
            locator += f", {c.section}"
        parts.append(f"[chunk:{c.chunk_id}] ({locator})\n{c.text}")
    return "\n\n---\n\n".join(parts)


def _applicant_block(meta: SubmissionMetadata) -> str:
    lines = [
        f"Legal name: {meta.applicant_legal_name}",
        f"Country: {meta.applicant_country}",
        f"Incorporated: {meta.incorporation_year or 'not stated'}",
        f"Declared headcount: {meta.employee_count if meta.employee_count is not None else 'not stated'}",
    ]
    return "\n".join(lines)


def _activities_block(meta: SubmissionMetadata) -> str:
    if not meta.declared_activities:
        return "(none declared)"
    out = []
    for a in meta.declared_activities:
        vol = f"{a.estimated_annual_volume_eur:,.0f} EUR/yr" if a.estimated_annual_volume_eur else "volume not stated"
        out.append(f"- {a.activity_code}: {a.description} | jurisdictions: "
                   f"{', '.join(a.jurisdictions) or 'not stated'} | {vol}")
    return "\n".join(out)


def _fallback(dim: dict, reason: str) -> DimensionAssessment:
    """If a dimension cannot be assessed we emit an explicit unknown rather
    than a zero. A zero would read as 'no risk found', which is a dangerous
    lie; this reads as 'we could not tell', which is the truth."""
    return DimensionAssessment(
        dimension=dim["key"],
        score=50,
        severity=Severity.MODERATE,
        rationale=f"Not assessed: {reason}. Scored at the neutral midpoint with zero confidence; "
                  f"this dimension requires manual review.",
        evidence=[],
        confidence=0.0,
        gaps=[f"Dimension '{dim['name']}' could not be assessed automatically."],
        inconsistencies=[],
    )


def run(metadata: SubmissionMetadata, selected: dict[str, list[Chunk]], dimensions: list[dict],
        llm, prompt_path: Path, logger) -> list[DimensionAssessment]:
    template = prompt_path.read_text(encoding="utf-8")
    valid_ids = {c.chunk_id for chunks in selected.values() for c in chunks}
    chunk_index = {c.chunk_id: c for chunks in selected.values() for c in chunks}

    def assess_one(dim: dict) -> DimensionAssessment:
        chunks = selected.get(dim["key"], [])
        prompt = template.format(
            dimension_key=dim["key"],
            dimension_name=dim["name"],
            dimension_description=dim["description"],
            rating_guidance=dim.get("rating_guidance", "(none supplied)"),
            applicant_block=_applicant_block(metadata),
            activities_block=_activities_block(metadata),
            evidence_block=_evidence_block(chunks),
        )
        try:
            result: DimensionAssessment = llm.complete_json(
                prompt, DimensionAssessment, tag=f"assess:{dim['key']}"
            )
        except Exception as exc:
            logger.error("assess.dimension_failed", dimension=dim["key"], error=repr(exc))
            return _fallback(dim, reason=repr(exc))

        # The model must use our dimension key, not a paraphrase of it.
        result.dimension = dim["key"]

        kept, dropped = [], []
        for ev in result.evidence:
            if ev.chunk_id in valid_ids:
                source = chunk_index[ev.chunk_id]
                ev.doc_id = source.doc_id
                ev.page = source.page
                kept.append(ev)
            else:
                dropped.append(ev.chunk_id)
        if dropped:
            logger.warn("assess.evidence_hallucinated", dimension=dim["key"], dropped=dropped)
        result.evidence = kept

        # An unevidenced judgement is capped in confidence. We keep the
        # judgement (absence of evidence is itself meaningful) but we refuse to
        # let it carry full weight into the composite.
        if not kept and result.confidence > 0.4:
            logger.warn("assess.unevidenced_confidence_capped", dimension=dim["key"],
                        was=result.confidence)
            result.confidence = 0.4

        logger.log("assess.dimension", dimension=dim["key"], score=result.score,
                   severity=result.severity.value, confidence=result.confidence,
                   evidence=len(kept), gaps=len(result.gaps))
        return result

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, max(len(dimensions), 1))) as pool:
        results = list(pool.map(assess_one, dimensions))

    logger.log("assess.ok", dimensions=len(results))
    return results
