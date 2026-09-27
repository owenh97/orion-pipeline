# ORION — Authorisation Risk Assessment Pipeline

A pipeline that reviews an authorisation submission to ORION (Operational Risk & Integrity Office) and produces a structured, evidence-backed risk assessment for a human analyst to validate or override.

It ingests a submission's metadata and its document set, extracts and retrieves the material that matters, asks an LLM for a judgement on each risk dimension separately, then derives the composite score and the recommended authorisation level **in deterministic code**. Every rating carries citations that resolve to a specific passage of a specific document, and every run emits a full audit trail.

```bash
pip install -r requirements.txt
python tools/make_sample_documents.py        # builds the sample document set
python run.py --submission data/submissions/ACME-2026-001
```

That runs with no API key: the default `mock` provider exercises every stage offline and deterministically. For a real assessment:

```bash
export OPENAI_API_KEY=sk-...
python run.py --submission data/submissions/ACME-2026-001 --llm openai
```

Or in a container:

```bash
docker build -t orion . && docker run --rm orion
```

---

## The central design decision

**The model judges evidence. Python makes the decision.**

The LLM is asked for one thing only: a risk rating on a single dimension, grounded in passages it is shown, with citations, gaps and a confidence. It never computes the composite score and never selects the authorisation level. Those are produced by `src/orion/stages/aggregate.py`, which contains no model calls and no randomness.

This is not stylistic caution. An authorisation outcome has to be explainable to the applicant and defensible to an oversight body. If a model chose the outcome, the same submission could yield a different decision on a different day and the reasoning would be unfalsifiable. Because the decision is a weighted score plus a set of explicit gates, a reviewer can be handed the file and verify the outcome by reading it.

---

## Pipeline

```
submission.json ──┐
                  ├─► ingest ──► extract ──► select ──► assess ──► questions ──► aggregate ──► emit ──► review API
document set   ───┘  validate    PDF/DOCX/    per-dim    1 LLM      consolidate   weights +     validate
(object storage)     + hash      XLSX →       retrieval  call per   the flagged   gates +       + deliver
                                 chunks                  dimension  gaps          thresholds
                                 (+provenance)           (parallel) (1 LLM call)  (no LLM)
```

| Stage | Responsibility | Key decision |
|---|---|---|
| **ingest** | Validate metadata, confirm every referenced document resolves, hash all inputs | A malformed `submission.json` aborts; a *missing document* does not — it becomes a coverage gap, because a broken reference is itself a finding |
| **extract** | PDF / DOCX / XLSX → chunks carrying document, page and section | Provenance is attached at extraction, not reconstructed later; without it a citation cannot be checked |
| **select** | Per-dimension retrieval over the corpus | The brief allows 100+ pages. Each dimension gets its own ~8 passages, so no prompt is diluted and every conclusion traces to a passage |
| **assess** | One LLM call per dimension, run concurrently | Short focused prompts, isolated failures, parallel latency, independently re-runnable |
| **questions** | Consolidate flagged gaps into a sendable clarification request | Deduplication across dimensions, because the same omission surfaces three times |
| **aggregate** | Weighted composite, hard gates, authorisation level | Deterministic. See above |
| **emit** | Final schema validation, persist, deliver | Idempotent by `run_id`; dry run by default |

`questions` runs *before* `aggregate` because a blocking clarification is an input to the decision (gate G3), not a footnote appended after it.

---

## How the decision is made

**Composite score.** A weight-normalised mean of the per-dimension risk scores (0 = no concern, 100 = severe). Weights live in `dimensions.yaml` and reflect consequence: financial crime and capital adequacy weigh more than a thin continuity annex.

The composite is deliberately **not** weighted by the model's self-reported confidence. Doing so would shrink the influence of exactly those dimensions where evidence is thin — which is where risk hides. Low confidence is handled by a gate that stops the pipeline issuing a verdict at all.

**Bands.** `<20` full · `<40` with conditions · `<60` provisional · `<80` refer to committee · `≥80` decline.

**Gates** are evaluated after the bands and may only make the outcome *stricter*:

| Gate | Fires when | Effect |
|---|---|---|
| `G1_CRITICAL_FINDING` | Any dimension rated critical | Refer to committee |
| `G2_INSUFFICIENT_EVIDENCE` | Mean confidence < 0.35, or fewer than half the dimensions evidence-backed | Insufficient information |
| `G3_BLOCKING_CLARIFICATION` | A blocking follow-up question is outstanding | Cap at provisional |
| `G4_MISSING_DOCUMENTS` | A referenced document could not be retrieved | Insufficient information |
| `G5_INCOMPLETE_RUN` | A dimension failed to assess | Insufficient information |

Gates exist because averaging has a specific, dangerous failure mode: five strong dimensions can bury one disqualifying finding. `G1` is what stops that, and `tests/test_aggregate.py::test_critical_finding_escalates_a_clean_average` pins the behaviour.

`INSUFFICIENT_INFORMATION` is absorbing. If we lack the evidence to judge, no favourable arithmetic should produce an authorisation.

---

## Meeting the stated constraints

**Serverless workflow with a fixed execution limit.** The pipeline is seven discrete stages, each checkpointed to `runs/<run_id>/state/<stage>.json` before the next begins. `--resume <run_id>` restarts from the first incomplete stage, and the content-addressed LLM cache means a resumed run re-spends no tokens. The mapping to real infrastructure is mechanical: each stage becomes one task in a Step Functions state machine, the run directory becomes an object-storage prefix, and the state files become the payloads between states. Only the storage adapter would change.

**Documents referenced from object storage.** All document access goes through the `ObjectStore` interface in `storage.py`. Locally it is filesystem-backed with path-traversal protection; `S3ObjectStore` is a deliberately unimplemented stub, because adding `boto3` to satisfy a constraint that cannot be exercised here would be decoration rather than engineering.

**Results delivered to an external review API.** `emit.py` POSTs the validated payload with an `Idempotency-Key` of the `run_id`, so a serverless retry updates rather than duplicates. Dry run is the default; a pipeline that silently POSTs to a live compliance system on first clone is a bad neighbour.

**Auditable and reproducible.** Temperature 0, a fixed seed, a pinned model, and a cache keyed on `sha256(provider + model + params + prompt)`. Every run emits `trace.jsonl` (one JSON object per event) and an `audit` block recording input hashes, the `dimensions.yaml` hash, prompt versions, token counts and per-stage timings. `tests/test_pipeline.py::test_run_is_reproducible` asserts that two runs of the same input agree exactly.

---

## Grounding and failure behaviour

Citations are verified, not trusted. After each assessment call, every returned chunk id is checked against the real chunk index; ids that do not resolve are dropped and logged (`assess.evidence_hallucinated`). A dimension that ends up with no evidence has its confidence capped at 0.4, so an unevidenced opinion cannot carry full weight into the composite.

Absence of evidence is treated as a finding. The assessment prompt states this explicitly, because the default model behaviour is to read a missing continuity plan as "no concern" and score it low — the single most dangerous failure mode for this use case.

Failures degrade rather than crash. A corrupt document is logged and skipped. A dimension whose LLM call fails is scored at the neutral midpoint with **zero confidence** and an explicit "not assessed" rationale — never zero, which would read as "no risk found" — and `G5` then forces the whole run to `INSUFFICIENT_INFORMATION`. If question synthesis fails, the raw gaps are passed through unpolished rather than dropped.

Schema violations are retried once with the validation error appended, which is cheaper and more honest than coercing a malformed response into shape.

---

## Output

`runs/<run_id>/assessment.json` — the reviewer-ready payload (`RiskAssessment` in `models.py`): per-dimension ratings with rationale, citations, confidence, gaps and inconsistencies; the composite and its per-dimension breakdown; the authorisation level with the gates that produced it; the consolidated follow-up questions; coverage statistics; and the audit record.

It carries a `reviewer_actions` block — status, reviewer id, decision override, per-dimension overrides, notes — which is the validation-and-override surface the brief asks for. The pipeline writes a recommendation; the analyst owns the decision.

`runs/<run_id>/trace.jsonl` — the structured event log for the run.

---

## Layout

```
run.py                      CLI entry point
src/orion/
  config.py                 every tunable, in one place
  models.py                 Pydantic contracts for every boundary
  llm.py                    provider abstraction, caching, retry, validation
  storage.py                object-storage interface
  logging_setup.py          run-scoped structured logging
  pipeline.py               orchestration and checkpointing
  stages/                   one module per stage
  prompts/
    dimensions.yaml         risk policy: dimensions, weights, vocabularies
    dimension_assessment.md assessment prompt (versioned)
    followup_questions.md   question synthesis prompt (versioned)
tools/make_sample_documents.py   generates the worked example
tests/                      29 tests, no network required
```

`dimensions.yaml` is the policy surface. Adding a dimension, reweighting one, or changing its retrieval vocabulary requires no code change — a risk officer should not need a developer to amend policy. The file's hash is recorded in every audit record, so any assessment traces back to the exact policy revision that produced it.

---

## Worked example

`data/submissions/ACME-2026-001` is a fictional clearing firm with deliberately planted flaws: headcount in the business plan contradicts the metadata, the financials are explicitly unaudited, the AML policy names no MLRO and describes no transaction monitoring, the security overview asserts controls it does not evidence, and nothing in the set addresses business continuity or outsourcing exit planning. The documents are generated by a script rather than committed as binaries so a reviewer can see exactly what is in them.

---

## Limitations

No evaluation set. The honest position is that this pipeline's judgement quality is currently unmeasured: with a labelled set of submissions and analyst-assigned ratings, the right next step is per-dimension agreement scoring plus a regression suite over the prompts, with `dimensions.yaml` and the prompt versions as the axes being tuned. The deterministic aggregation layer is already fully covered by tests; the model-dependent layer is not, and pretending otherwise would be the wrong claim to make.

Retrieval is lexical by default. `--retrieval embedding` swaps in OpenAI embeddings, which handle paraphrase better. Keyword is the default because it is free, deterministic and inspectable — you can read a chunk's score and see why it was selected — and because the dimension vocabularies are domain jargon, which is where lexical matching is strongest.

Chunking is character-based and structure-aware only at the page, heading and worksheet level. Tables inside PDFs are extracted as text and can lose column alignment.

Scanned or image-only PDFs are not handled; they would need an OCR step ahead of extraction.
