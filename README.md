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

The AI reads the submitted documents and produces, for each risk area, a rating with the passages it used, what's missing, and how confident it is.

It never computes the composite score and never selects the authorisation level. That happens in `src/orion/stages/aggregate.py` — a weighted average of the six ratings plus a set of hard gates, containing no model calls and no randomness.

This is not stylistic caution. If ORION declines a firm, that firm's lawyers will ask why, and the answer has to be reproducible. A weighted score and an explicit rulebook can be handed over and checked line by line. A model's verdict cannot: the same submission could score differently on a different day, and there would be nothing to point at but the output itself.

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

**Bands.** `<40` authorise with conditions · `<60` provisional · `<80` refer to committee · `≥80` decline.

`FULL_AUTHORISATION` is deliberately unreachable. A firm applying to hold client money or run settlement infrastructure is being admitted to a *supervised* activity, and admission in practice always carries reporting and notification conditions. A system that could output "approved, nothing further required" would be modelling a decision this authority does not make. The level stays in the enum so a human reviewer can override to it; what is removed is the pipeline's ability to arrive there by itself.

**Gates** are evaluated after the bands and may only make the outcome *stricter*:

| Gate | Fires when | Effect |
|---|---|---|
| `G1_SEVERE_FINDING` | Any dimension rated **high or critical** | Refer to committee |
| `G2_INSUFFICIENT_EVIDENCE` | Mean confidence < 0.35, or fewer than half the dimensions evidence-backed | Insufficient information |
| `G3_BLOCKING_CLARIFICATION` | A blocking follow-up question is outstanding | Cap at provisional |
| `G4_MISSING_DOCUMENTS` | A referenced document could not be retrieved | Insufficient information |
| `G5_INCOMPLETE_RUN` | A dimension failed to assess | Insufficient information |

Gates exist because averaging has a specific, dangerous failure mode: five strong dimensions can bury one disqualifying finding. `G1` is what stops that, and `tests/test_aggregate.py::test_critical_finding_escalates_a_clean_average` pins the behaviour.

`G1` fires on **high** as well as critical, which is stricter than it needs to be and is a deliberate trade. Including high sends more files to committee and some of those will turn out fine, costing reviewer time. The two error types are not symmetric: an unnecessary committee review costs an hour, whereas a high-severity control failure averaged away costs the authority its credibility and potentially somebody's client money. `test_elevated_severity_does_not_escalate` pins the other side of the boundary, so the rule cannot quietly widen further.

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

## Worked examples

Two submissions ship with the repo, at opposite ends of the range. Both aregenerated by script rather than committed as binaries, so a reviewer can read exactly what is in them.

`data/submissions/ACME-2026-001` — Meridian Clearing, a firm designed to fail. Headcount in the business plan contradicts the metadata, the financials are explicitly unaudited, the AML policy names no MLRO and describes no transaction monitoring, the security overview asserts controls it does not evidence, and nothing in the set addresses business continuity or outsourcing exit planning.
Generated by `tools/make_sample_documents.py`.

`data/submissions/NORTHGATE-2026-002` — Northgate Settlement, the control case. A genuinely well-run firm: audited accounts with an unqualified opinion and 37% headroom, an executed credit facility, a dedicated MLRO since 2018 with automated transaction monitoring and continuous PEP screening, ISO 27001 with half-yearly penetration testing and retest evidence, a continuity plan with a 2-hour RTO tested twice yearly, three independent non-executive directors, and an outsourcing register with exit plans. Generated by
`tools/make_control_submission.py`.

A flawed case alone proves only that the pipeline can find problems. It cannot show whether the pipeline finds problems that are not there — the more dangerous failure for an authority, because a system that refers every applicant to committee is useless in a way that is harder to notice than one that approves everybody. The two cases together are the beginning of an evaluation set: known inputs, expected outcomes, at both ends of the range.

Observed, on `gpt-4o-mini`:

| | Meridian | Northgate |
|---|---|---|
| Composite | 76.2 | 23.4 |
| Dimension range | 66–86 | 10–45 |
| Mean confidence | 0.73 | 0.87 |
| `G1_SEVERE_FINDING` | fired | did not fire |
| Outcome | `REFER_TO_COMMITTEE` | `PROVISIONAL_AUTHORISATION` |

The system discriminates. It also, on the control case, does something itshould not — see below.

---

## What the control case revealed

Running the well-run applicant surfaced two defects that the flawed case could not have shown.

**A retrieval miss reported as a missing control.** `financial_crime` raised a blocking question demanding evidence of independent AML testing. Northgate's AML policy states plainly that independent testing was performed in September 2025, that four low-severity observations were raised, and that all four were closed by December with closure evidence supplied.

The passage carrying that evidence is chunk `DOC-002:007`, and it was never retrieved. The dimension's keyword vocabulary contained `anti-money laundering`, `MLRO`, `KYC`, `transaction monitoring` — and not the phrase `financial crime`, nor `independent testing`, nor `assurance`. The chunk scored zero and never entered the ranking. What the model did see was a weaker line in the business plan asserting that testing happens annually, with no dates and no findings — so it applied the prompt's assertion-versus-evidence rule correctly and asked for the proof. The model was right about what it had been shown. It had been shown the wrong thing.

The general lesson is recorded in `dimensions.yaml` next to the fix: a dimension's vocabulary must cover the language used to **evidence** a control, not only the language used to **name** it. "Anti-money laundering" and "MLRO" name the topic; "independent testing" and "assurance" are how a firm proves it. Retrieval that knows only the first set will systematically mistake proof for absence. And at the assessment stage a retrieval miss is indistinguishable from a genuine gap — the model cannot tell "this firm has no independent testing" from "you did not show me the page where they describe it". Fixed in `dimensions.yaml` `1.4.0`; the missed chunk now ranks first for its dimension, and follow-up questions on the control case dropped from three to one.

**A structural problem with gate G3.** The remaining question is not a retrieval failure. Having now seen the evidence, the model asks for independent testing *beyond* the annual review — a standard the rubric does not set, since the 0–20 band requires only that independent testing be performed. It then marks that question blocking, G3 fires, and the outcome is capped at `PROVISIONAL_AUTHORISATION`.

Follow that through and the consequence is structural. Any single blocking question caps the outcome. If the model will flag something blocking on an applicant this clean, `AUTHORISATION_WITH_CONDITIONS` is unreachable in practice — which means this pipeline now has two unreachable levels: one removed deliberately, and one lost by accident.

There is also a contradiction in the output that code is in a position to catch. `financial_crime` scored 45 with confidence 0.75 and five citations, while simultaneously asserting that something missing blocks authorisation outright. Evidence sufficient to support a moderate score cannot also be absent enough to block. The intended fix is therefore not a further prompt tevision but a rule in `aggregate.py`: G3 should fire only where the blocking question's dimension itself scored above a threshold, so that a low-scoring, well-evidenced dimension cannot cap the outcome on the strength of a flag that contradicts its own rating. Left unfixed here, and recorded rather than quietly patched, because the reasoning is the point.


## Limitations

The evaluation set is two cases wide. `ACME-2026-001` and `NORTHGATE-2026-002` have known expected outcomes at opposite ends of the range, and they have already earned their keep — the retrieval defect and the G3 problem above were both found by running them. But two synthetic cases are not measurement. With a labelled set of real submissions and analyst-assigned ratings, the next steps are per-dimension agreement scoring against the analyst, retrieval recall measured separately from judgement accuracy — the control case showed those fail differently and need to be diagnosed apart — and a regression suite over `dimensions.yaml` and the prompt versions, since editing a single citation instruction was observed to move unrelated dimensions by up to 11 points. The deterministic aggregation layer is fully covered by tests; the model-dependent layer is sampled, not measured, and claiming otherwise would be the wrong claim to make.

Retrieval is lexical by default. `--retrieval embedding` swaps in OpenAI embeddings, which handle paraphrase better. Keyword is the default because it is free, deterministic and inspectable — you can read a chunk's score and see why it was selected — and because the dimension vocabularies are domain jargon, which is where lexical matching is strongest.

Chunking is character-based and structure-aware only at the page, heading and worksheet level. Tables inside PDFs are extracted as text and can lose column alignment.

Scanned or image-only PDFs are not handled; they would need an OCR step ahead of extraction.