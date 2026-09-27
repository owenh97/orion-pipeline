"""Stage 1 - Ingest.

Load and validate the submission metadata, confirm every referenced document
actually exists in object storage, and hash each input so the audit record can
prove which bytes produced which assessment.

Failure policy: a malformed submission.json aborts the run (we cannot assess
what we cannot parse). A missing *document* does not abort -- it is recorded as
a coverage gap, because in the real world a broken reference is itself a
finding the reviewer needs to see.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..models import SubmissionMetadata
from ..storage import ObjectStore


def run(submission_dir: Path, store: ObjectStore, logger) -> tuple[SubmissionMetadata, dict[str, str], list[str]]:
    meta_path = Path(submission_dir) / "submission.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"no submission.json in {submission_dir}")

    raw = meta_path.read_bytes()
    metadata = SubmissionMetadata.model_validate_json(raw.decode("utf-8"))

    input_hashes = {"submission.json": hashlib.sha256(raw).hexdigest()}
    missing: list[str] = []

    for doc in metadata.documents:
        if store.exists(doc.uri):
            input_hashes[doc.doc_id] = store.sha256(doc.uri)
        else:
            missing.append(doc.doc_id)
            logger.warn("ingest.document_missing", doc_id=doc.doc_id, uri=doc.uri)

    logger.log(
        "ingest.ok",
        submission_id=metadata.submission_id,
        applicant=metadata.applicant_legal_name,
        documents=len(metadata.documents),
        missing=len(missing),
        activities=len(metadata.declared_activities),
    )
    return metadata, input_hashes, missing
