"""Stage 6 - Follow-up questions.

Gaps and inconsistencies are surfaced per-dimension during assessment, but they
must not be shipped to the reviewer raw: the same missing document typically
produces near-identical complaints across three dimensions, and a reviewer
handed twenty overlapping bullet points will act on none of them.

So this stage takes the union of flagged gaps and asks the model to do one
narrow job it is genuinely good at -- deduplicate, merge, and phrase them as
questions an analyst could send to the applicant unedited. The `blocking` flag
is what feeds gate G3 in aggregation, so it materially affects the outcome and
is defined tightly in the prompt.

If there are no gaps, no call is made. Spending a request to be told there is
nothing to ask is waste.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..models import DimensionAssessment, FollowUpQuestion

MAX_QUESTIONS = 8


class FollowUpQuestionList(BaseModel):
    questions: list[FollowUpQuestion] = Field(default_factory=list)


def _gaps_block(assessments: list[DimensionAssessment]) -> str:
    lines: list[str] = []
    for a in assessments:
        for gap in a.gaps:
            lines.append(f"- [{a.dimension}] MISSING: {gap}")
        for issue in a.inconsistencies:
            lines.append(f"- [{a.dimension}] INCONSISTENT: {issue}")
    return "\n".join(lines)


def run(assessments: list[DimensionAssessment], llm, prompt_path: Path, logger) -> list[FollowUpQuestion]:
    block = _gaps_block(assessments)
    if not block.strip():
        logger.log("questions.skipped", reason="no gaps or inconsistencies flagged")
        return []

    prompt = prompt_path.read_text(encoding="utf-8").format(
        gaps_block=block,
        max_questions=MAX_QUESTIONS,
    )

    try:
        result: FollowUpQuestionList = llm.complete_json(
            prompt, FollowUpQuestionList, tag="questions"
        )
    except Exception as exc:
        # Degrade to the raw gaps rather than losing them entirely: a clumsy
        # question list is far better than a silent omission on a compliance
        # deliverable.
        logger.error("questions.failed", error=repr(exc))
        return [
            FollowUpQuestion(
                question_id=f"Q{i + 1:02d}",
                dimension=a.dimension,
                question=f"Please clarify: {gap}",
                reason="Auto-generated fallback after question synthesis failed.",
                blocking=False,
            )
            for i, (a, gap) in enumerate(
                [(a, g) for a in assessments for g in (a.gaps + a.inconsistencies)]
            )[:MAX_QUESTIONS]
        ]

    questions = result.questions[:MAX_QUESTIONS]
    for i, q in enumerate(questions, start=1):
        q.question_id = f"Q{i:02d}"

    logger.log("questions.ok", count=len(questions),
               blocking=sum(1 for q in questions if q.blocking))
    return questions
