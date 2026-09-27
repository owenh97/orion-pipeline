"""Pipeline orchestration.

The brief constrains the system to a serverless workflow with a fixed
execution limit. That single constraint drives the shape of this file.

A long-running monolithic function is the wrong answer: if it is killed at 90%
you lose everything, including the money already spent on LLM calls. So the
pipeline is a sequence of discrete, individually-checkpointed stages. Each
stage writes its output to the run directory before the next begins, and
`--resume <run_id>` restarts from the first incomplete stage. Combined with the
content-addressed LLM cache, a resumed run re-does no work and re-spends no
tokens.

That also makes the mapping to real infrastructure mechanical rather than
aspirational: each stage is one task in a Step Functions / Durable Functions
state machine, the run directory becomes an object-storage prefix, and the
`state/*.json` files become the payloads passed between states. Nothing about
the code would need to change except the storage adapter.

Stage order, and why:

  ingest    -> validate metadata, confirm documents exist, hash inputs
  extract   -> documents to provenance-carrying chunks
  select    -> per-dimension retrieval, so no prompt sees 100+ pages
  assess    -> one LLM judgement per dimension, run concurrently
  questions -> consolidate the flagged gaps into a clarification request
  aggregate -> deterministic scoring, gates, authorisation level
  emit      -> validate, persist, deliver

`questions` precedes `aggregate` because a blocking clarification is an input
to the authorisation decision (gate G3), not an afterthought appended to it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import PROMPTS_DIR, Settings
from .llm import build_client
from .logging_setup import RunLogger
from .models import (
    AuditRecord,
    Chunk,
    DimensionAssessment,
    FollowUpQuestion,
    RiskAssessment,
    SubmissionMetadata,
    utc_now,
)
from .stages import aggregate, assess, emit, extract, ingest, questions, select
from .storage import LocalObjectStore

PIPELINE_VERSION = "1.0.0"
STAGES = ["ingest", "extract", "select", "assess", "questions", "aggregate", "emit"]


def load_dimensions(path: Path | None = None) -> tuple[list[dict], str, str]:
    path = path or (PROMPTS_DIR / "dimensions.yaml")
    raw = path.read_bytes()
    parsed = yaml.safe_load(raw.decode("utf-8"))
    dims = parsed["dimensions"]
    for d in dims:
        for field in ("key", "name", "description"):
            if field not in d:
                raise ValueError(f"dimension missing '{field}': {d}")
    keys = [d["key"] for d in dims]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate dimension keys in dimensions.yaml")
    return dims, hashlib.sha256(raw).hexdigest(), parsed.get("version", "unknown")


def _prompt_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return hashlib.sha256(text.encode()).hexdigest()[:8]


class Pipeline:
    def __init__(self, settings: Settings, run_id: str | None = None) -> None:
        self.settings = settings
        self.run_id = run_id or f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.run_dir = Path(settings.runs_root) / self.run_id
        self.state_dir = self.run_dir / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log = RunLogger(self.run_id, self.run_dir)
        self.llm = build_client(settings, self.log)
        self.timings: dict[str, float] = {}

    # --- checkpointing ---------------------------------------------------

    def _ckpt_path(self, stage: str) -> Path:
        return self.state_dir / f"{stage}.json"

    def _save(self, stage: str, payload: Any) -> None:
        self._ckpt_path(stage).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _load(self, stage: str) -> Any | None:
        path = self._ckpt_path(stage)
        if not path.exists():
            return None
        self.log.log("stage.resumed", stage=stage)
        return json.loads(path.read_text(encoding="utf-8"))

    # --- main ------------------------------------------------------------

    def run(self, submission_dir: Path, resume: bool = False) -> RiskAssessment:
        started_at = utc_now()
        settings = self.settings
        dimensions, dim_hash, dim_version = load_dimensions()
        store = LocalObjectStore(settings.storage_root)

        assess_prompt = PROMPTS_DIR / "dimension_assessment.md"
        questions_prompt = PROMPTS_DIR / "followup_questions.md"

        self.log.log(
            "run.start",
            submission_dir=str(submission_dir),
            provider=settings.llm_provider,
            model=settings.model,
            retrieval=settings.retrieval,
            dimensions=len(dimensions),
            dimensions_version=dim_version,
            resume=resume,
        )

        # --- ingest ------------------------------------------------------
        with self.log.stage("ingest") as t:
            metadata, input_hashes, missing_docs = ingest.run(submission_dir, store, self.log)
            self._save("ingest", {
                "metadata": metadata.model_dump(mode="json"),
                "input_hashes": input_hashes,
                "missing_docs": missing_docs,
            })
        self.timings["ingest"] = t["elapsed_ms"]

        # --- extract -----------------------------------------------------
        with self.log.stage("extract") as t:
            cached = self._load("extract") if resume else None
            if cached:
                chunks = [Chunk.model_validate(c) for c in cached]
            else:
                chunks = extract.run(
                    metadata.documents, store, self.log,
                    target_chars=settings.chunk_target_chars,
                    overlap_chars=settings.chunk_overlap_chars,
                    skip_doc_ids=set(missing_docs),
                )
                self._save("extract", [c.model_dump(mode="json") for c in chunks])
        self.timings["extract"] = t["elapsed_ms"]

        # --- select ------------------------------------------------------
        with self.log.stage("select") as t:
            selected = select.run(chunks, dimensions, settings, self.log)
            self._save("select", {k: [c.chunk_id for c in v] for k, v in selected.items()})
        self.timings["select"] = t["elapsed_ms"]

        # --- assess ------------------------------------------------------
        with self.log.stage("assess") as t:
            cached = self._load("assess") if resume else None
            if cached:
                assessments = [DimensionAssessment.model_validate(a) for a in cached]
            else:
                assessments = assess.run(metadata, selected, dimensions, self.llm, assess_prompt, self.log)
                self._save("assess", [a.model_dump(mode="json") for a in assessments])
        self.timings["assess"] = t["elapsed_ms"]

        # --- questions ---------------------------------------------------
        with self.log.stage("questions") as t:
            cached = self._load("questions") if resume else None
            if cached is not None:
                follow_ups = [FollowUpQuestion.model_validate(q) for q in cached]
            else:
                follow_ups = questions.run(assessments, self.llm, questions_prompt, self.log)
                self._save("questions", [q.model_dump(mode="json") for q in follow_ups])
        self.timings["questions"] = t["elapsed_ms"]

        # --- aggregate ---------------------------------------------------
        with self.log.stage("aggregate") as t:
            score, breakdown, level, rationale, gate_ids, coverage = aggregate.run(
                assessments, dimensions, follow_ups,
                chunks_total=len(chunks),
                documents_total=len(metadata.documents),
                missing_docs=missing_docs,
                logger=self.log,
            )
        self.timings["aggregate"] = t["elapsed_ms"]

        audit = AuditRecord(
            run_id=self.run_id,
            started_at=started_at,
            finished_at=utc_now(),
            llm_provider=settings.llm_provider,
            model=settings.model if settings.llm_provider != "mock" else "mock",
            temperature=settings.temperature,
            seed=settings.seed,
            prompt_versions={
                "dimension_assessment": _prompt_version(assess_prompt),
                "followup_questions": _prompt_version(questions_prompt),
                "dimensions_yaml": dim_version,
            },
            dimension_config_hash=dim_hash,
            input_hashes=input_hashes,
            llm_calls=self.llm.calls,
            cached_llm_calls=self.llm.cache_hits,
            total_tokens=self.llm.total_tokens,
            stage_timings_ms=self.timings,
            pipeline_version=PIPELINE_VERSION,
        )

        payload = RiskAssessment(
            run_id=self.run_id,
            submission_id=metadata.submission_id,
            applicant_legal_name=metadata.applicant_legal_name,
            dimensions=assessments,
            composite_score=score,
            composite_breakdown=breakdown,
            authorization_level=level,
            decision_rationale=rationale,
            triggered_gates=gate_ids,
            follow_up_questions=follow_ups,
            coverage=coverage,
            audit=audit,
        )

        # --- emit --------------------------------------------------------
        with self.log.stage("emit") as t:
            emit.run(payload, settings, self.log)
        self.timings["emit"] = t["elapsed_ms"]

        self.log.log(
            "run.done",
            composite=score,
            level=level.value,
            gates=gate_ids,
            questions=len(follow_ups),
            llm_calls=self.llm.calls,
            cached=self.llm.cache_hits,
            tokens=self.llm.total_tokens,
            total_ms=round(sum(self.timings.values()), 1),
        )
        self.log.close()
        return payload


def find_incomplete_stage(run_dir: Path) -> str | None:
    state = run_dir / "state"
    for stage in STAGES:
        if not (state / f"{stage}.json").exists():
            return stage
    return None
