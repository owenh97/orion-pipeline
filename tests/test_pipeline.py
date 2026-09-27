"""Extraction, retrieval, contract and end-to-end tests.

The end-to-end test runs the real pipeline against the real sample documents
using the mock provider, so it exercises every stage and the full schema
without a network call or an API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orion.config import Settings  # noqa: E402
from orion.models import Chunk, RiskAssessment, SubmissionMetadata  # noqa: E402
from orion.pipeline import Pipeline, load_dimensions  # noqa: E402
from orion.stages import extract, select  # noqa: E402
from orion.storage import LocalObjectStore  # noqa: E402

SUBMISSION = ROOT / "data" / "submissions" / "ACME-2026-001"


class _NullLogger:
    def log(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def error(self, *a, **k): pass


# --- chunking --------------------------------------------------------------


def test_pack_respects_target_size():
    text = "\n".join(f"Paragraph number {i} with some filler content." * 3 for i in range(60))
    pieces = list(extract._pack(text, target=500, overlap=50))
    assert len(pieces) > 1
    # Allow overlap to push a piece slightly over target, but not unboundedly.
    assert all(len(p) <= 500 + 50 + 200 for p in pieces)


def test_pack_hard_splits_an_oversized_paragraph():
    pieces = list(extract._pack("x" * 5000, target=1000, overlap=0))
    assert len(pieces) == 5


def test_pack_returns_nothing_for_empty_input():
    assert list(extract._pack("   \n  ", target=100, overlap=10)) == []


# --- storage safety --------------------------------------------------------


def test_store_rejects_path_traversal(tmp_path):
    store = LocalObjectStore(tmp_path)
    with pytest.raises(ValueError):
        store.read_bytes("local://../../etc/passwd")


def test_store_reports_missing_object(tmp_path):
    store = LocalObjectStore(tmp_path)
    assert store.exists("local://nope.pdf") is False


# --- retrieval -------------------------------------------------------------


def test_retrieval_prefers_the_matching_dimension():
    chunks = [
        Chunk(chunk_id="c1", doc_id="D", filename="f", text=
              "The company performs customer due diligence and sanctions screening "
              "as part of its anti-money laundering programme."),
        Chunk(chunk_id="c2", doc_id="D", filename="f", text=
              "Own funds and capital adequacy are monitored against the regulatory "
              "capital requirement each quarter."),
    ]
    dims, _, _ = load_dimensions()
    settings = Settings(max_chunks_per_dimension=1)
    picked = select.run(chunks, dims, settings, _NullLogger())
    assert picked["financial_crime"][0].chunk_id == "c1"
    assert picked["financial_soundness"][0].chunk_id == "c2"


# --- contracts -------------------------------------------------------------


def test_submission_rejects_duplicate_doc_ids():
    payload = {
        "submission_id": "X", "applicant_legal_name": "Y", "applicant_country": "NL",
        "documents": [
            {"doc_id": "D1", "filename": "a.pdf", "uri": "local://a.pdf", "media_type": "pdf"},
            {"doc_id": "D1", "filename": "b.pdf", "uri": "local://b.pdf", "media_type": "pdf"},
        ],
    }
    with pytest.raises(Exception):
        SubmissionMetadata.model_validate(payload)


def test_dimension_config_is_wellformed():
    dims, digest, version = load_dimensions()
    assert len(dims) >= 3
    assert len(digest) == 64
    assert version
    for d in dims:
        assert d["keywords"], f"{d['key']} has no retrieval vocabulary"
        assert float(d.get("weight", 1.0)) > 0


# --- end to end ------------------------------------------------------------


@pytest.mark.skipif(not (SUBMISSION / "documents" / "business_plan.pdf").exists(),
                    reason="run tools/make_sample_documents.py first")
def test_end_to_end_mock_run(tmp_path):
    settings = Settings(
        llm_provider="mock",
        runs_root=tmp_path / "runs",
        cache_root=tmp_path / "cache",
        storage_root=ROOT / "data",
    )
    result = Pipeline(settings).run(SUBMISSION)

    # The payload must satisfy the published contract.
    RiskAssessment.model_validate(result.model_dump(mode="json"))

    assert result.submission_id == "ACME-2026-001"
    assert len(result.dimensions) == len(load_dimensions()[0])
    assert 0 <= result.composite_score <= 100
    assert result.coverage.chunks_total > 0
    assert result.audit.dimension_config_hash
    assert result.reviewer_actions["status"] == "PENDING_REVIEW"

    # Every citation must resolve to a chunk that actually exists.
    store = LocalObjectStore(ROOT / "data")
    metadata = SubmissionMetadata.model_validate_json(
        (SUBMISSION / "submission.json").read_text()
    )
    real_ids = {
        c.chunk_id
        for c in extract.run(metadata.documents, store, _NullLogger(),
                             target_chars=settings.chunk_target_chars,
                             overlap_chars=settings.chunk_overlap_chars)
    }
    for dim in result.dimensions:
        for ev in dim.evidence:
            assert ev.chunk_id in real_ids, f"unresolvable citation {ev.chunk_id}"


@pytest.mark.skipif(not (SUBMISSION / "documents" / "business_plan.pdf").exists(),
                    reason="run tools/make_sample_documents.py first")
def test_run_is_reproducible(tmp_path):
    """Same inputs, same policy, same output. This is the auditability claim,
    tested rather than asserted."""
    def once(tag):
        settings = Settings(
            llm_provider="mock",
            runs_root=tmp_path / f"runs-{tag}",
            cache_root=tmp_path / f"cache-{tag}",
            storage_root=ROOT / "data",
        )
        return Pipeline(settings).run(SUBMISSION)

    a, b = once("a"), once("b")
    assert a.composite_score == b.composite_score
    assert a.authorization_level == b.authorization_level
    assert [d.score for d in a.dimensions] == [d.score for d in b.dimensions]
    assert a.triggered_gates == b.triggered_gates
