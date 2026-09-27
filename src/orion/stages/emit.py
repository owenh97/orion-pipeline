"""Stage 7 - Emit.

The payload is validated one final time against the schema before it leaves the
process, then written to the run directory and delivered to the external review
API.

Delivery is idempotent by run_id: the review API is expected to treat a repeat
POST of the same run_id as an update, not a duplicate submission. Under a
serverless retry -- which a fixed execution limit makes likely, not
hypothetical -- that property is what stops one submission appearing three
times in a reviewer's queue.

Dry run is the default. A pipeline that silently POSTs to a live compliance
system the first time someone clones it is a bad neighbour.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import RiskAssessment


def deliver(payload: RiskAssessment, settings, logger) -> dict:
    body = payload.model_dump(mode="json")

    if settings.emit_dry_run or not settings.review_api_url:
        target = Path(settings.runs_root) / payload.run_id / "review_api_payload.json"
        target.write_text(json.dumps(body, indent=2), encoding="utf-8")
        logger.log("emit.dry_run", path=str(target), bytes=target.stat().st_size,
                   reason="no review API configured" if not settings.review_api_url else "dry run enabled")
        return {"delivered": False, "path": str(target)}

    import httpx

    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": payload.run_id,
    }
    if settings.review_api_token:
        headers["Authorization"] = f"Bearer {settings.review_api_token}"

    last_error = None
    for attempt in range(1, settings.max_retries + 1):
        try:
            response = httpx.post(
                settings.review_api_url,
                json=body,
                headers=headers,
                timeout=settings.request_timeout,
            )
            response.raise_for_status()
            logger.log("emit.delivered", status=response.status_code,
                       url=settings.review_api_url, attempt=attempt)
            return {"delivered": True, "status": response.status_code}
        except Exception as exc:
            last_error = exc
            logger.warn("emit.retry", attempt=attempt, error=repr(exc))

    logger.error("emit.failed", error=repr(last_error))
    fallback = Path(settings.runs_root) / payload.run_id / "review_api_payload.undelivered.json"
    fallback.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return {"delivered": False, "path": str(fallback), "error": repr(last_error)}


def run(payload: RiskAssessment, settings, logger) -> dict:
    # Re-validate: cheap, and it guarantees nothing mutated the object between
    # construction and delivery.
    RiskAssessment.model_validate(payload.model_dump(mode="json"))

    run_dir = Path(settings.runs_root) / payload.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    local = run_dir / "assessment.json"
    local.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    logger.log("emit.written", path=str(local))

    return deliver(payload, settings, logger)
