#!/usr/bin/env python3
"""Generate a second, well-run applicant: the control case.

ACME-2026-001 (Meridian) is a firm designed to fail. On its own it proves only
that the pipeline can find problems -- it cannot tell you whether the pipeline
finds problems that are not there, which is the more dangerous failure for a
regulator. A system that refers every applicant to committee is useless in a
different way from one that approves everybody.

NORTHGATE-2026-002 is the opposite case, built to be genuinely well run:
audited accounts with real headroom, a named MLRO with automated monitoring,
ISO 27001 with a remediated penetration test, a tested continuity plan with
stated objectives, independent board oversight, and an outsourcing register
with exit plans.

Expected outcome: low scores across all six dimensions, no G1 escalation, and
AUTHORISATION_WITH_CONDITIONS -- the best band this pipeline can reach.

Together the two submissions are the beginning of an evaluation set: two cases
with known expected outcomes, at opposite ends of the range. That is not a
substitute for labelled historical data, but it does catch the two failure
modes that matter most, and it makes a policy change's effect visible
immediately at both ends.

Run:  python tools/make_control_submission.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "data" / "submissions" / "NORTHGATE-2026-002"
OUT = SUB / "documents"


def make_pdf(path: Path, title: str, sections: list[tuple[str, list[str]]]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 18)]
    for heading, paragraphs in sections:
        story.append(Paragraph(heading, styles["Heading2"]))
        for para in paragraphs:
            story.append(Paragraph(para, styles["BodyText"]))
            story.append(Spacer(1, 6))
        story.append(PageBreak())
    SimpleDocTemplate(str(path), pagesize=A4, title=title).build(story)
    print(f"wrote {path.relative_to(ROOT)}")


def make_docx(path: Path, title: str, sections: list[tuple[str, list[str]]]) -> None:
    import docx

    document = docx.Document()
    document.add_heading(title, level=0)
    for heading, paragraphs in sections:
        document.add_heading(heading, level=1)
        for para in paragraphs:
            document.add_paragraph(para)
    document.save(str(path))
    print(f"wrote {path.relative_to(ROOT)}")


def make_xlsx(path: Path, sheets: dict[str, list[list]]) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(str(path))
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    make_pdf(
        OUT / "business_plan.pdf",
        "Northgate Settlement Services Ltd - Business Plan 2026",
        [
            ("1. Executive Summary", [
                "Northgate Settlement Services Ltd ('Northgate') seeks authorisation to "
                "operate payment clearing and client money custody within the zone. The "
                "company was incorporated in 2016 and employs 88 staff across three offices.",
                "Northgate processes an estimated EUR 1.1 billion in annualised settlement "
                "volume for 71 institutional clients and has operated under equivalent "
                "authorisation in two neighbouring jurisdictions since 2019.",
            ]),
            ("2. Corporate Structure and Ownership", [
                "Northgate is a wholly owned subsidiary of Northgate Group NV, incorporated "
                "in the Netherlands. Northgate Group NV is owned 62 per cent by Brackenridge "
                "Capital Partners LP, registered in Luxembourg, and 38 per cent by the "
                "founding management team. Ultimate beneficial owners holding above 10 per "
                "cent are identified in Appendix C of this submission, with certified "
                "passport and proof of address evidence for each.",
                "The board comprises seven directors, of whom three are independent "
                "non-executives. The Chief Risk Officer is a separate appointment from the "
                "Chief Executive Officer and reports directly to the board risk committee, "
                "which is chaired by an independent non-executive director.",
                "An internal audit function has been in place since 2019, staffed by two "
                "full-time auditors reporting to the audit committee. The 2025 internal "
                "audit plan and its completion report are provided.",
                "Fit and proper declarations, criminal record certificates and regulatory "
                "reference checks have been completed for all nine key function holders.",
            ]),
            ("3. Services and Activities", [
                "Core services are payment clearing, settlement netting and client money "
                "custody. Settlement runs four times daily against two independent "
                "correspondent banks, so that the failure of either does not halt settlement.",
                "Northgate operates its core ledger platform in-house. Cloud infrastructure "
                "is contracted directly with two providers under agreements that include "
                "right of audit clauses, supervisory access provisions and sub-outsourcing "
                "notification requirements.",
                "A documented exit plan exists for each material outsourcing arrangement, "
                "specifying the alternative provider, the migration sequence and a tested "
                "90-day exit window. The outsourcing register lists 14 arrangements, of "
                "which 4 are classified material and reassessed annually.",
                "No single provider supports more than one critical function.",
            ]),
            ("4. Capital and Funding", [
                "Own funds as at 31 December 2025 were EUR 19.4 million against a regulatory "
                "requirement of EUR 14.2 million, representing headroom of 36.6 per cent.",
                "The company recorded a profit of EUR 3.8 million in the 2025 financial year, "
                "its sixth consecutive profitable year.",
                "Financial statements for 2025 have been audited by Vandermeer & Klaas "
                "Registeraccountants and carry an unqualified opinion with no going-concern "
                "emphasis. The signed audit report is provided at Appendix A.",
                "A committed revolving credit facility of EUR 8 million with Rijnmond Bank NV "
                "was executed on 14 November 2025 and remains undrawn. The executed facility "
                "agreement is provided at Appendix B.",
            ]),
            ("5. Technology and Operations", [
                "The platform maintained 99.98 per cent availability across the 2025 calendar "
                "year. One unplanned outage of 11 minutes occurred in March 2025; the "
                "post-incident review and remediation are documented.",
                "Client data is encrypted in transit using TLS 1.3 and at rest using AES-256 "
                "with keys held in a hardware security module.",
                "Access to production systems follows least-privilege provisioning through a "
                "central directory. Multi-factor authentication is enforced for all accounts. "
                "Access recertification is performed quarterly and evidenced.",
            ]),
            ("6. Compliance", [
                "Northgate maintains a risk-based anti-money-laundering programme reviewed "
                "annually and last updated in February 2026.",
                "A dedicated Money Laundering Reporting Officer was appointed in 2018, reports "
                "directly to the board, and holds no operational responsibilities that would "
                "compromise independence. A deputy MLRO is appointed.",
                "The compliance function comprises five staff independent of the first line "
                "and is subject to annual independent testing by an external firm.",
            ]),
        ],
    )

    make_docx(
        OUT / "aml_policy.docx",
        "Northgate Settlement Services Ltd - AML and Financial Crime Policy v7.3",
        [
            ("Purpose and Scope", [
                "This policy sets out Northgate's risk-based approach to preventing money "
                "laundering and terrorist financing. It applies to all employees, "
                "contractors and agents.",
                "The policy was last reviewed and board-approved in February 2026 and is "
                "reviewed annually or upon material regulatory change.",
            ]),
            ("Governance", [
                "A dedicated Money Laundering Reporting Officer (MLRO) has been in post "
                "since 2018 and reports directly to the board. A deputy MLRO is appointed "
                "to ensure continuity of cover.",
                "The MLRO holds no first-line operational responsibilities. The compliance "
                "function is independent of client-facing teams and is resourced with five "
                "full-time staff.",
            ]),
            ("Customer Due Diligence", [
                "All institutional clients undergo due diligence prior to onboarding, "
                "including certificate of incorporation, register of directors, regulatory "
                "status verification and ownership structure mapping.",
                "Beneficial ownership is identified through the full ownership chain to "
                "natural persons holding 10 per cent or more, regardless of the number of "
                "intervening corporate layers. Where the chain cannot be resolved, the "
                "relationship is declined.",
                "Enhanced due diligence is applied where a client meets any criterion in the "
                "documented high-risk matrix at Annex 2, which covers jurisdiction, product, "
                "delivery channel and ownership opacity. Client risk is rescored annually "
                "and on trigger events.",
            ]),
            ("Sanctions and Screening", [
                "Clients and their beneficial owners are screened against applicable "
                "sanctions lists at onboarding and continuously thereafter using an "
                "automated screening system, with list updates applied within 24 hours of "
                "publication.",
                "Politically exposed person screening is performed at onboarding and on a "
                "continuous basis. PEP relationships require senior management approval and "
                "are subject to enhanced ongoing monitoring.",
            ]),
            ("Transaction Monitoring", [
                "Northgate operates an automated transaction monitoring system covering all "
                "client settlement activity. Rule thresholds are calibrated against client "
                "risk rating and reviewed semi-annually; the most recent tuning exercise was "
                "completed in November 2025.",
                "Alerts are triaged by the financial crime operations team within one "
                "business day and escalated to the MLRO where unresolved.",
                "System coverage, alert volumes and false positive rates are reported to the "
                "board risk committee quarterly.",
            ]),
            ("Reporting", [
                "Suspicious activity is reported by the MLRO to the relevant financial "
                "intelligence unit. In 2025, 46 internal escalations were raised, of which "
                "11 resulted in external reports.",
            ]),
            ("Training and Independent Assurance", [
                "All staff complete financial crime training on joining and annually "
                "thereafter. Completion in 2025 was 100 per cent. Role-specific training is "
                "delivered to client-facing and operations staff.",
                "Independent testing of the financial crime framework was performed by an "
                "external firm in September 2025. Four low-severity observations were "
                "raised; all four were closed by December 2025 and closure evidence is "
                "provided.",
            ]),
        ],
    )

    make_xlsx(
        OUT / "financial_summary.xlsx",
        {
            "Balance Sheet": [
                ["Northgate Settlement Services Ltd", "", ""],
                ["Position as at", "2025-12-31", "AUDITED - UNQUALIFIED OPINION"],
                ["Auditor", "Vandermeer & Klaas Registeraccountants", "Report dated 2026-03-11"],
                ["", "", ""],
                ["Item", "EUR 2025", "EUR 2024"],
                ["Cash and cash equivalents", 21400000, 18900000],
                ["Client money held (segregated)", 142700000, 128300000],
                ["Trade receivables", 3960000, 3410000],
                ["Intangible assets", 1880000, 2050000],
                ["Total assets", 169940000, 152660000],
                ["Client money liabilities", 142700000, 128300000],
                ["Trade payables", 7840000, 8770000],
                ["Total liabilities", 150540000, 137070000],
                ["Net assets / own funds", 19400000, 15590000],
            ],
            "Income Statement": [
                ["Item", "EUR 2025", "EUR 2024"],
                ["Revenue", 24600000, 21800000],
                ["Operating expenses", -19900000, -18200000],
                ["Operating profit", 4700000, 3600000],
                ["Finance costs", -310000, -340000],
                ["Profit for the year", 3800000, 2600000],
            ],
            "Capital": [
                ["Metric", "EUR", "Note"],
                ["Own funds", 19400000, "Audited"],
                ["Regulatory requirement", 14200000, "Calculated per applicable methodology"],
                ["Surplus", 5200000, "36.6% headroom"],
                ["Audit status 2025", "AUDITED - UNQUALIFIED", "Vandermeer & Klaas"],
                ["Committed undrawn facility", 8000000, "Executed 2025-11-14, Rijnmond Bank NV"],
            ],
        },
    )

    make_pdf(
        OUT / "security_and_resilience.pdf",
        "Northgate Settlement Services Ltd - Security and Operational Resilience",
        [
            ("Security Governance and Certification", [
                "Northgate has held ISO 27001 certification continuously since 2020. The "
                "current certificate, issued March 2025, is provided at Appendix D. The "
                "most recent surveillance audit raised no major non-conformities.",
                "A dedicated Chief Information Security Officer was appointed in 2021 and "
                "reports to the board risk committee, independently of the technology "
                "delivery function.",
            ]),
            ("Technical Controls", [
                "Data in transit is encrypted using TLS 1.3. Data at rest, including all "
                "production databases and backups, is encrypted using AES-256 with keys "
                "managed in a FIPS 140-2 Level 3 hardware security module.",
                "Access follows least-privilege provisioning. Multi-factor authentication is "
                "enforced for all accounts without exception. Privileged access is "
                "time-bound and session-recorded. Access recertification is quarterly and "
                "evidenced.",
                "Critical vulnerabilities are remediated within 7 days and high severity "
                "within 30 days under a documented SLA. Compliance against that SLA was 100 "
                "per cent for critical and 97 per cent for high severity during 2025.",
            ]),
            ("Testing and Assurance", [
                "Independent penetration testing is performed twice annually. The most "
                "recent test, completed October 2025, identified two medium-severity and "
                "five low-severity findings. All seven were remediated by December 2025 and "
                "retest evidence confirming closure is provided at Appendix E.",
                "Vulnerability scanning runs continuously against internet-facing systems "
                "and weekly against the internal estate.",
            ]),
            ("Business Continuity and Disaster Recovery", [
                "A documented business continuity plan and disaster recovery plan are "
                "maintained for all critical services and were last updated in January 2026.",
                "Stated recovery objectives are a recovery time objective of 2 hours and a "
                "recovery point objective of 15 minutes for settlement services.",
                "Full failover testing to the secondary site is performed twice yearly. The "
                "most recent test, in November 2025, achieved failover in 74 minutes against "
                "the 2 hour objective. The test report and lessons-learned log are provided.",
                "A documented single points of failure analysis is maintained and reviewed "
                "annually. No unremediated single point of failure exists in any critical "
                "settlement path; dual correspondent banking and dual cloud regions provide "
                "redundancy.",
            ]),
            ("Incident Management", [
                "A documented incident management process defines severity classification, "
                "escalation paths and regulatory notification timelines.",
                "One security incident was recorded in 2025: a phishing attempt that was "
                "blocked at the mail gateway with no account compromise. No client data was "
                "affected. A post-incident review was completed within 5 business days.",
            ]),
        ],
    )

    submission = {
        "submission_id": "NORTHGATE-2026-002",
        "applicant_legal_name": "Northgate Settlement Services Ltd",
        "applicant_country": "NL",
        "incorporation_year": 2016,
        "employee_count": 88,
        "contact_email": "authorisations@northgate-settlement.example",
        "declared_activities": [
            {
                "activity_code": "PAY-CLR-01",
                "description": "Payment clearing and settlement netting for institutional clients",
                "jurisdictions": ["NL", "DE", "LU"],
                "estimated_annual_volume_eur": 1100000000,
            },
            {
                "activity_code": "CUS-CLM-02",
                "description": "Custody and safeguarding of client money",
                "jurisdictions": ["NL"],
                "estimated_annual_volume_eur": 142700000,
            },
        ],
        "documents": [
            {
                "doc_id": "DOC-001",
                "filename": "business_plan.pdf",
                "uri": "local://submissions/NORTHGATE-2026-002/documents/business_plan.pdf",
                "media_type": "pdf",
                "declared_type": "Business plan",
            },
            {
                "doc_id": "DOC-002",
                "filename": "aml_policy.docx",
                "uri": "local://submissions/NORTHGATE-2026-002/documents/aml_policy.docx",
                "media_type": "docx",
                "declared_type": "AML and financial crime policy",
            },
            {
                "doc_id": "DOC-003",
                "filename": "financial_summary.xlsx",
                "uri": "local://submissions/NORTHGATE-2026-002/documents/financial_summary.xlsx",
                "media_type": "xlsx",
                "declared_type": "Audited financial statements summary",
            },
            {
                "doc_id": "DOC-004",
                "filename": "security_and_resilience.pdf",
                "uri": "local://submissions/NORTHGATE-2026-002/documents/security_and_resilience.pdf",
                "media_type": "pdf",
                "declared_type": "Security and operational resilience overview",
            },
        ],
    }

    (SUB / "submission.json").write_text(json.dumps(submission, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {(SUB / 'submission.json').relative_to(ROOT)}")
    print()
    print("Run it:")
    print("  python run.py --submission data/submissions/NORTHGATE-2026-002 --llm openai --model gpt-4o-mini")
    print()
    print("Expected: low scores across all six dimensions, no G1 escalation,")
    print("          AUTHORISATION_WITH_CONDITIONS.")


if __name__ == "__main__":
    main()
