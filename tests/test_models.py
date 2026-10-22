"""Contract tests for the Pydantic spine."""

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.common import DocType, TaskStatus
from app.models.document import (
    Block,
    BulletList,
    Callout,
    DocumentSpec,
    Heading,
    NumberedList,
    PageBreak,
    Paragraph,
    Section,
    SectionDraft,
    Table,
)
from app.models.plan import Task, TaskList
from app.models.schemas import schema_for


def _task(task_id: str, *, deps: list[str] | None = None) -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        description="Do useful work",
        kind="write_section",
        section_title="Overview",
        depends_on=deps or [],
        acceptance="Section exists and answers the objective",
    )


def _full_spec() -> DocumentSpec:
    return DocumentSpec(
        title="Cloud Migration Proposal",
        subtitle="A safe path to the cloud",
        doc_type=DocType.PROPOSAL,
        metadata={"author": "DocPilot", "date": "2026-10-22", "version": "1.0"},
        assumptions=["Budget not provided; assuming 120,000 USD."],
        sections=[
            Section(
                id="s_t1",
                heading="Overview",
                blocks=[
                    Paragraph(text="Migration overview."),
                    Heading(level=2, text="Why now"),
                    BulletList(items=["Speed", "Resilience"]),
                    NumberedList(items=["Assess", "Migrate", "Optimize"]),
                    Table(
                        caption="Costs",
                        columns=["Item", "Amount"],
                        rows=[["Platform", "50,000"], ["Services", "70,000"]],
                    ),
                    Callout(kind="decision", text="Recommend a phased migration."),
                    PageBreak(),
                ],
            ),
            Section(
                id="s_t2",
                heading="Risks",
                blocks=[Paragraph(text="Delivery risk is manageable.")],
            ),
        ],
    )


def test_document_spec_json_round_trip() -> None:
    spec = _full_spec()
    restored = DocumentSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
    assert restored.sections[0].blocks[4].type == "table"


def test_table_rejects_ragged_rows() -> None:
    with pytest.raises(ValidationError, match="column count"):
        Table(columns=["A", "B"], rows=[["one"]])


def test_tasklist_rejects_duplicate_unknown_and_cycles() -> None:
    with pytest.raises(ValidationError, match="duplicate task ids"):
        TaskList(
            objective="Test",
            doc_type=DocType.PROJECT_PLAN,
            tasks=[_task("t1"), _task("t1")],
        )

    with pytest.raises(ValidationError, match="unknown tasks"):
        TaskList(
            objective="Test",
            doc_type=DocType.PROJECT_PLAN,
            tasks=[_task("t1", deps=["t9"])],
        )

    with pytest.raises(ValidationError, match="cycle"):
        TaskList(
            objective="Test",
            doc_type=DocType.PROJECT_PLAN,
            tasks=[_task("t1", deps=["t2"]), _task("t2", deps=["t1"])],
        )


def test_topological_layers_for_diamond() -> None:
    plan = TaskList(
        objective="Diamond",
        doc_type=DocType.PROJECT_PLAN,
        tasks=[
            _task("t1"),
            _task("t2", deps=["t1"]),
            _task("t3", deps=["t1"]),
            _task("t4", deps=["t2", "t3"]),
        ],
    )
    layers = plan.topological_layers()
    assert [[task.id for task in layer] for layer in layers] == [["t1"], ["t2", "t3"], ["t4"]]


def test_tool_task_requires_tool_name() -> None:
    with pytest.raises(ValidationError, match="must name a tool"):
        Task(
            id="t1",
            title="Find budget",
            description="Look up budget",
            kind="tool",
            acceptance="Budget exists",
        )


def test_discriminated_union_parses_every_block() -> None:
    adapter = TypeAdapter(Block)
    payloads = [
        {"type": "heading", "level": 2, "text": "Heading"},
        {"type": "paragraph", "text": "Paragraph"},
        {"type": "bullets", "items": ["one"]},
        {"type": "numbered", "items": ["two"]},
        {"type": "table", "columns": ["A"], "rows": [["1"]]},
        {"type": "callout", "kind": "warning", "text": "Careful"},
        {"type": "page_break"},
    ]
    parsed = [adapter.validate_python(payload) for payload in payloads]
    assert [block.type for block in parsed] == [
        "heading",
        "paragraph",
        "bullets",
        "numbered",
        "table",
        "callout",
        "page_break",
    ]
    assert parsed[0] == Heading(level=2, text="Heading")
    assert parsed[6] == PageBreak()


def test_section_draft_normalizes_common_block_aliases() -> None:
    draft = SectionDraft.model_validate(
        {
            "heading": "Plan",
            "blocks": [
                {"type": "bullet_list", "items": ["one", "two"]},
                {"type": "ordered_list", "items": ["first", "second"]},
            ],
        }
    )
    assert [block.type for block in draft.blocks] == ["bullets", "numbered"]


def test_schema_helper_is_json_serializable() -> None:
    schema = schema_for(DocumentSpec)
    assert schema["type"] == "object"
    assert "properties" in schema
    json.dumps(schema)


def test_status_enum_serializes_to_string() -> None:
    assert _task("t1").status is TaskStatus.PENDING
    assert json.loads(_task("t1").model_dump_json())["status"] == "pending"
