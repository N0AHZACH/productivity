"""Render a hand-written DocumentSpec containing every block type."""

from pathlib import Path

from app.models.common import DocType
from app.models.document import (
    BulletList,
    Callout,
    DocumentSpec,
    Heading,
    NumberedList,
    PageBreak,
    Paragraph,
    Section,
    Table,
)
from app.render.docx_renderer import DocxRenderer
from app.render.themes import Theme


def demo_spec() -> DocumentSpec:
    return DocumentSpec(
        title="Northstar Retail Group",
        subtitle="Customer Support Automation: Project Plan",
        doc_type=DocType.PROJECT_PLAN,
        metadata={
            "author": "DocPilot",
            "date": "2026-10-22",
            "version": "1.0",
        },
        assumptions=[
            "No dedicated data engineer is available; assuming two analysts support the build.",
            "Peak-season change freeze begins 15 November; implementation must finish before it.",
        ],
        sections=[
            Section(
                id="s_objectives",
                heading="Objectives and Success Criteria",
                blocks=[
                    Paragraph(
                        text=(
                            "Northstar will launch an assisted customer-support chatbot in twelve "
                            "weeks. The program aims to deflect routine order-status and returns "
                            "questions while protecting customer satisfaction."
                        )
                    ),
                    Heading(text="Success measures", level=1),
                    BulletList(
                        items=[
                            "Resolve at least 35% of eligible contacts without an agent.",
                            "Maintain customer satisfaction at or above the pilot baseline.",
                            "Keep average first response time below two minutes.",
                        ]
                    ),
                    Callout(
                        kind="info",
                        text="Targets are measured over a four-week pilot before full rollout.",
                    ),
                ],
            ),
            Section(
                id="s_scope",
                heading="Scope and Workstreams",
                blocks=[
                    Paragraph(
                        text=(
                            "The first release covers tracked orders, returns, store hours and "
                            "password resets. Billing changes and subscription cancellation remain "
                            "out of scope until the second release."
                        )
                    ),
                    Table(
                        caption="Workstreams and owners",
                        columns=["Workstream", "Owner", "Deliverable", "Weeks"],
                        rows=[
                            ["Discovery", "CX", "Top-intent analysis", "2"],
                            ["Knowledge", "Operations", "Approved answer library", "6"],
                            ["Integration", "Engineering", "Commerce API connector", "8"],
                            ["Pilot", "CX", "Four-week assisted rollout", "4"],
                        ],
                    ),
                ],
            ),
            Section(
                id="s_timeline",
                heading="Timeline",
                blocks=[
                    Paragraph(
                        text=(
                            "The schedule is sequenced so content quality and API integration can "
                            "progress in parallel after discovery."
                        )
                    ),
                    NumberedList(
                        items=[
                            "Discovery and measurement baseline (weeks 1-2).",
                            "Knowledge base and integration builds (weeks 3-8).",
                            "Controlled pilot and tuning (weeks 9-12).",
                        ]
                    ),
                    Heading(text="Key dates", level=2),
                    Table(
                        caption="Milestone schedule",
                        columns=["Milestone", "Date"],
                        rows=[["Pilot go-live", "2026-11-02"], ["Rollout decision", "2026-12-01"]],
                    ),
                ],
            ),
            Section(
                id="s_budget",
                heading="Budget and Risks",
                blocks=[
                    Paragraph(
                        text=(
                            "The estimated investment covers engineering time, platform fees and "
                            "content operations, with a 10% contingency."
                        )
                    ),
                    Table(
                        caption="Phase budget",
                        columns=["Item", "Amount"],
                        rows=[
                            ["Integration engineering", "72,000"],
                            ["Platform licenses", "24,000"],
                            ["Content operations", "18,000"],
                            ["Contingency", "11,400"],
                            ["Total", "125,400"],
                        ],
                    ),
                    Callout(
                        kind="risk",
                        text=(
                            "Commerce API rate limits may slow response tests; the team will run "
                            "an early load test and reserve a fallback queue."
                        ),
                    ),
                    Callout(
                        kind="warning",
                        text=(
                            "The peak-season change freeze constrains any launch slip beyond "
                            "week 12."
                        ),
                    ),
                    Callout(
                        kind="decision",
                        text=(
                            "Prioritize a narrow, reliable launch over a broader feature set. The "
                            "recommendation balances speed, brand risk and support cost."
                        ),
                    ),
                    PageBreak(),
                ],
            ),
        ],
    )


def main() -> None:
    output = Path("examples/demo.docx")
    theme = Theme(footer_text="DocPilot | Confidential")
    path = DocxRenderer(theme).render_to(demo_spec(), output)
    print(f"Rendered {path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
