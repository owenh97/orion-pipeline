#!/usr/bin/env python3
"""Generate the sample document set for the worked example.

The fixtures are generated rather than committed as binaries so a reviewer can
see exactly what is in them, and so the deliberate flaws are explicit:

  * headcount in the business plan (45) contradicts submission.json (62)
    -> should surface as an inconsistency
  * financials are explicitly unaudited
    -> should raise financial_soundness
  * the AML policy names no MLRO and describes no transaction monitoring
    -> should raise financial_crime
  * security overview asserts controls but evidences no penetration test
    -> should exercise the "asserted vs evidenced" distinction in the prompt
  * no business continuity or exit planning content exists anywhere
    -> should be caught as absence of evidence, not scored as "no concern"

Run:  python tools/make_sample_documents.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "submissions" / "ACME-2026-001" / "documents"


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
        "Meridian Clearing Services Ltd - Business Plan 2026",
        [
            ("1. Executive Summary", [
                "Meridian Clearing Services Ltd ('Meridian') seeks authorisation to operate "
                "payment clearing and custody infrastructure within the zone. The company was "
                "incorporated in 2021 and currently employs 45 staff across two offices.",
                "Meridian processes an estimated EUR 480 million in annualised settlement volume "
                "on behalf of 34 institutional clients. The company intends to expand into "
                "digital asset custody during 2026 subject to authorisation.",
            ]),
            ("2. Corporate Structure and Ownership", [
                "Meridian is a wholly owned subsidiary of Meridian Holdings BV, incorporated in "
                "the Netherlands. Meridian Holdings BV is in turn majority owned by Castellan "
                "Partners LP, a limited partnership registered in a jurisdiction the company has "
                "not disclosed in this document.",
                "The board comprises four directors. The Chief Executive Officer also holds the "
                "role of Chief Risk Officer on an interim basis and chairs the risk committee.",
                "No internal audit function currently exists. The company intends to establish "
                "one within eighteen months of authorisation.",
            ]),
            ("3. Services and Activities", [
                "Core services are payment clearing, settlement netting and client money "
                "custody. Settlement runs three times daily against a single correspondent bank.",
                "Meridian outsources its core ledger platform to Halcyon Systems GmbH under a "
                "master services agreement dated March 2023. Halcyon in turn hosts the platform "
                "on a public cloud provider. Meridian holds no direct contractual relationship "
                "with that provider.",
                "The Halcyon agreement does not contain a right of audit clause. No exit plan "
                "has been prepared for the ledger platform.",
            ]),
            ("4. Capital and Funding", [
                "Own funds as at 31 December 2025 were EUR 6.2 million against an estimated "
                "regulatory requirement of EUR 5.8 million. The company recorded a loss of "
                "EUR 1.1 million in the 2025 financial year.",
                "Funding for 2026 is expected from a shareholder loan facility from Meridian "
                "Holdings BV. The facility has been discussed but not executed.",
                "Financial statements for 2025 have been prepared by management and have not "
                "been audited. The company last obtained an audit opinion for the 2023 "
                "financial year.",
            ]),
            ("5. Technology and Operations", [
                "The platform maintains 99.7 per cent availability measured over the 2025 "
                "calendar year. Two unplanned outages occurred, of 40 minutes and 3 hours.",
                "Client data is encrypted in transit. Encryption at rest is described as "
                "planned for the second half of 2026.",
                "Access to production systems is granted through a central directory. "
                "Multi-factor authentication is enforced for administrative accounts.",
            ]),
            ("6. Compliance", [
                "Meridian maintains an anti-money-laundering policy which is reviewed annually. "
                "Customer due diligence is performed at onboarding by the client services team.",
                "The company has not yet appointed a dedicated money laundering reporting "
                "officer. Compliance responsibilities currently sit with the Head of Operations.",
            ]),
        ],
    )

    make_docx(
        OUT / "aml_policy.docx",
        "Meridian Clearing Services Ltd - AML and Financial Crime Policy v2.1",
        [
            ("Purpose and Scope", [
                "This policy sets out Meridian's approach to preventing money laundering and "
                "terrorist financing. It applies to all employees and contractors.",
                "The policy was last reviewed in June 2024 and is scheduled for review annually.",
            ]),
            ("Customer Due Diligence", [
                "Prospective institutional clients are subject to due diligence prior to "
                "onboarding. Documentation collected includes certificate of incorporation, "
                "register of directors and evidence of regulatory status where applicable.",
                "Beneficial ownership is identified to the first corporate layer. The policy "
                "does not require identification of ultimate beneficial owners beyond that layer.",
                "Enhanced due diligence is applied where a client is assessed as higher risk. "
                "The criteria for that assessment are not documented in this policy.",
            ]),
            ("Sanctions and Screening", [
                "Clients are screened against applicable sanctions lists at onboarding. "
                "Ongoing screening is performed on an ad hoc basis when the operations team "
                "becomes aware of a change in circumstances.",
                "Politically exposed person screening is not currently performed.",
            ]),
            ("Transaction Monitoring", [
                "Unusual activity identified by the operations team during daily reconciliation "
                "is escalated to the Head of Operations.",
                "Meridian does not currently operate an automated transaction monitoring system. "
                "Implementation is under consideration.",
            ]),
            ("Reporting", [
                "Suspicious activity is reported to the relevant financial intelligence unit. "
                "Two internal escalations were recorded in 2025; neither resulted in an external "
                "report.",
            ]),
            ("Training and Assurance", [
                "All staff complete financial crime training on joining. Refresher training was "
                "last delivered in 2023.",
                "No independent testing of the financial crime framework has been performed.",
            ]),
        ],
    )

    make_xlsx(
        OUT / "financial_summary.xlsx",
        {
            "Balance Sheet": [
                ["Meridian Clearing Services Ltd", "", ""],
                ["Position as at", "2025-12-31", "UNAUDITED - MANAGEMENT ACCOUNTS"],
                ["", "", ""],
                ["Item", "EUR 2025", "EUR 2024"],
                ["Cash and cash equivalents", 4120000, 5380000],
                ["Client money held (segregated)", 61400000, 58200000],
                ["Trade receivables", 1840000, 1610000],
                ["Intangible assets", 2210000, 1980000],
                ["Total assets", 69570000, 67170000],
                ["Client money liabilities", 61400000, 58200000],
                ["Trade payables", 1970000, 1740000],
                ["Total liabilities", 63370000, 59940000],
                ["Net assets / own funds", 6200000, 7230000],
            ],
            "Income Statement": [
                ["Item", "EUR 2025", "EUR 2024"],
                ["Revenue", 8940000, 9310000],
                ["Operating expenses", -9620000, -8850000],
                ["Operating profit / (loss)", -680000, 460000],
                ["Finance costs", -420000, -390000],
                ["Profit / (loss) for the year", -1100000, 70000],
            ],
            "Capital": [
                ["Metric", "EUR", "Note"],
                ["Own funds", 6200000, "Management figure, unaudited"],
                ["Estimated requirement", 5800000, "Company's own estimate"],
                ["Surplus", 400000, "6.9% headroom"],
                ["Audit status 2025", "NOT AUDITED", "Last audited FY2023"],
            ],
        },
    )

    make_pdf(
        OUT / "security_overview.pdf",
        "Meridian Clearing Services Ltd - Information Security Overview",
        [
            ("Security Governance", [
                "Meridian takes information security extremely seriously and maintains a "
                "comprehensive security posture aligned to industry best practice.",
                "Security is overseen by the Head of Technology. There is no dedicated "
                "information security officer.",
                "The company is not certified to ISO 27001 or any equivalent standard. "
                "Certification is a stated objective for 2027.",
            ]),
            ("Technical Controls", [
                "Network traffic is encrypted using TLS 1.2 or above. Production databases are "
                "not currently encrypted at rest.",
                "Privileged access requires multi-factor authentication. Access reviews are "
                "performed annually.",
                "Patching is performed on a monthly cycle for non-critical systems. Critical "
                "vulnerabilities are addressed on a best-efforts basis.",
            ]),
            ("Testing and Assurance", [
                "A penetration test was performed by an external party in 2023. The report "
                "identified four high-severity findings. Remediation status for those findings "
                "is not recorded in this document.",
                "No penetration test has been performed since 2023.",
                "Vulnerability scanning is performed quarterly against internet-facing systems.",
            ]),
            ("Incidents", [
                "One security incident was recorded in 2025 involving unauthorised access to a "
                "staff email account. No client data was assessed as affected. A post-incident "
                "review was completed.",
            ]),
        ],
    )


if __name__ == "__main__":
    main()
