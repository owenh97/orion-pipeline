#!/usr/bin/env python3
"""Build a submission.json from a folder of documents.

Hand-writing the manifest is error-prone -- the URIs must match the storage
root exactly, and a typo surfaces as a missing document rather than as a clear
error. This generates it.

    mkdir -p data/submissions/MYFIRM-001/documents
    cp ~/Downloads/*.pdf data/submissions/MYFIRM-001/documents/
    python tools/make_submission.py \\
        --dir data/submissions/MYFIRM-001 \\
        --name "Acme Financial Services Ltd" \\
        --country MY

Then edit the declared_activities block, because what the firm says it will do
drives the assessment: a control that is missing only matters relative to an
activity that requires it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx"}
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    p = argparse.ArgumentParser(description="Generate submission.json from a documents folder.")
    p.add_argument("--dir", required=True, type=Path,
                   help="Submission directory; documents must be in <dir>/documents/")
    p.add_argument("--name", required=True, help="Applicant legal name")
    p.add_argument("--country", default="MY", help="Applicant country code (default: MY)")
    p.add_argument("--id", default=None, help="Submission id (default: the directory name)")
    p.add_argument("--incorporated", type=int, default=None, help="Year of incorporation")
    p.add_argument("--employees", type=int, default=None, help="Declared headcount")
    p.add_argument("--force", action="store_true", help="Overwrite an existing submission.json")
    args = p.parse_args()

    sub_dir = args.dir.resolve()
    docs_dir = sub_dir / "documents"
    if not docs_dir.is_dir():
        print(f"No documents folder at {docs_dir}", file=sys.stderr)
        print("Create it and put the PDF/DOCX/XLSX files inside.", file=sys.stderr)
        return 2

    submission_id = args.id or sub_dir.name
    out_path = sub_dir / "submission.json"
    if out_path.exists() and not args.force:
        print(f"{out_path} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 2

    # URIs are relative to the storage root (data/), which is what
    # LocalObjectStore resolves against.
    try:
        prefix = sub_dir.relative_to(ROOT / "data")
    except ValueError:
        print(f"{sub_dir} is not inside {ROOT / 'data'}", file=sys.stderr)
        print("Submissions must live under data/ so the object store can resolve them.", file=sys.stderr)
        return 2

    documents, skipped = [], []
    for i, path in enumerate(sorted(docs_dir.iterdir()), start=1):
        if path.name.startswith("."):
            continue
        media = SUPPORTED.get(path.suffix.lower())
        if media is None:
            skipped.append(path.name)
            continue
        documents.append({
            "doc_id": f"DOC-{i:03d}",
            "filename": path.name,
            "uri": f"local://{prefix}/documents/{path.name}",
            "media_type": media,
            "declared_type": path.stem.replace("_", " ").replace("-", " ").title(),
        })

    if not documents:
        print(f"No supported documents found in {docs_dir}", file=sys.stderr)
        print("Supported: .pdf .docx .xlsx", file=sys.stderr)
        return 2

    payload = {
        "submission_id": submission_id,
        "applicant_legal_name": args.name,
        "applicant_country": args.country,
        "incorporation_year": args.incorporated,
        "employee_count": args.employees,
        "contact_email": None,
        "declared_activities": [
            {
                "activity_code": "EDIT-ME-01",
                "description": "Replace this with what the applicant actually intends to do",
                "jurisdictions": [args.country],
                "estimated_annual_volume_eur": None,
            }
        ],
        "documents": documents,
    }

    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"  {len(documents)} document(s):")
    for d in documents:
        print(f"    {d['doc_id']}  {d['media_type']:<5} {d['filename']}")
    if skipped:
        print(f"  skipped (unsupported): {', '.join(skipped)}")
    print()
    print("Next: edit declared_activities in that file, then run")
    print(f"  python run.py --submission {args.dir} --llm openai --model gpt-4o-mini")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
