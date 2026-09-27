#!/usr/bin/env python3
"""ORION risk assessment pipeline - command line entry point.

    python run.py --submission data/submissions/ACME-2026-001
    python run.py --submission data/submissions/ACME-2026-001 --llm openai
    python run.py --submission data/submissions/ACME-2026-001 --resume run-2026...
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from orion.config import Settings  # noqa: E402
from orion.pipeline import Pipeline  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Assess an ORION authorisation submission.")
    p.add_argument("--submission", required=True, type=Path,
                   help="Directory containing submission.json")
    p.add_argument("--llm", choices=["mock", "openai"], default=None,
                   help="LLM provider (default: mock, or ORION_LLM_PROVIDER)")
    p.add_argument("--model", default=None, help="Model name, e.g. gpt-4o-mini")
    p.add_argument("--retrieval", choices=["keyword", "embedding"], default=None)
    p.add_argument("--max-chunks", type=int, default=None,
                   help="Max passages retrieved per dimension")
    p.add_argument("--resume", metavar="RUN_ID", default=None,
                   help="Resume an interrupted run, reusing completed stages")
    p.add_argument("--review-api-url", default=None,
                   help="POST the result here instead of writing a dry-run file")
    p.add_argument("--quiet", action="store_true", help="Only print the final summary")
    return p.parse_args(argv)


def build_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    overrides = {}
    if args.llm:
        overrides["llm_provider"] = args.llm
    if args.model:
        overrides["model"] = args.model
    if args.retrieval:
        overrides["retrieval"] = args.retrieval
    if args.max_chunks:
        overrides["max_chunks_per_dimension"] = args.max_chunks
    if args.review_api_url:
        overrides["review_api_url"] = args.review_api_url
        overrides["emit_dry_run"] = False
    return dataclasses.replace(settings, **overrides) if overrides else settings


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = build_settings(args)

    if settings.llm_provider == "openai" and not settings.openai_api_key:
        print("OPENAI_API_KEY is not set. Export it, or run with --llm mock.", file=sys.stderr)
        return 2

    if not (args.submission / "submission.json").is_file():
        print(f"No submission.json found in {args.submission}", file=sys.stderr)
        return 2

    pipeline = Pipeline(settings, run_id=args.resume)
    result = pipeline.run(args.submission, resume=bool(args.resume))

    print()
    print("=" * 72)
    print(f"  Submission      {result.submission_id}  ({result.applicant_legal_name})")
    print(f"  Run             {result.run_id}")
    print(f"  Composite risk  {result.composite_score:.1f} / 100")
    print(f"  Recommendation  {result.authorization_level.value}")
    if result.triggered_gates:
        print(f"  Gates triggered {', '.join(result.triggered_gates)}")
    print("-" * 72)
    for d in sorted(result.dimensions, key=lambda x: x.score, reverse=True):
        bar = "#" * round(d.score / 5)
        print(f"  {d.score:>3}  {d.severity.value:<9} {bar:<20} {d.dimension}"
              f"   (conf {d.confidence:.2f}, {len(d.evidence)} cites)")
    print("-" * 72)
    if result.follow_up_questions:
        print(f"  {len(result.follow_up_questions)} follow-up question(s):")
        for q in result.follow_up_questions:
            flag = "[BLOCKING] " if q.blocking else ""
            print(f"    {q.question_id} {flag}{q.question}")
    else:
        print("  No follow-up questions raised.")
    print("=" * 72)
    print(f"  Payload  {Path(settings.runs_root) / result.run_id / 'assessment.json'}")
    print(f"  Trace    {Path(settings.runs_root) / result.run_id / 'trace.jsonl'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
