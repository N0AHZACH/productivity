"""DocumentSpec contracts: the typed boundary between LLM and renderer."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.common import DocType


class Heading(BaseModel):
    """A heading inside a section."""

    type: Literal["heading"] = "heading"
    level: int = Field(2, ge=1, le=3)
    text: str


class Paragraph(BaseModel):
    """A prose paragraph."""

    type: Literal["paragraph"] = "paragraph"
    text: str


class BulletList(BaseModel):
    """An unordered list."""

    type: Literal["bullets"] = "bullets"
    items: list[str] = Field(min_length=1)


class NumberedList(BaseModel):
    """An explicitly numbered list."""

    type: Literal["numbered"] = "numbered"
    items: list[str] = Field(min_length=1)


class Table(BaseModel):
    """A rectangular table with optional caption."""

    type: Literal["table"] = "table"
    caption: str | None = None
    columns: list[str] = Field(min_length=1)
    rows: list[list[str]]

    @model_validator(mode="after")
    def _rectangular(self) -> "Table":
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError("every table row must match the column count")
        return self


class Callout(BaseModel):
    """A highlighted information, warning, risk, or decision box."""

    type: Literal["callout"] = "callout"
    kind: Literal["info", "warning", "risk", "decision"] = "info"
    text: str


class PageBreak(BaseModel):
    """A forced page break."""

    type: Literal["page_break"] = "page_break"


Block = Annotated[
    Heading | Paragraph | BulletList | NumberedList | Table | Callout | PageBreak,
    Field(discriminator="type"),
]


class Section(BaseModel):
    """One titled section composed of rich blocks."""

    id: str
    heading: str
    blocks: list[Block] = Field(min_length=1)


class SectionDraft(BaseModel):
    """LLM output for one section before it receives a stable ID."""

    heading: str
    blocks: list[Block] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_block_types(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        blocks = value.get("blocks")
        if not isinstance(blocks, list):
            return value
        aliases = {
            "bullet": "bullets",
            "bullet-list": "bullets",
            "bullet_list": "bullets",
            "bulletlist": "bullets",
            "unordered-list": "bullets",
            "unordered_list": "bullets",
            "ordered-list": "numbered",
            "ordered_list": "numbered",
            "numbered-list": "numbered",
            "numbered_list": "numbered",
        }
        normalized = []
        for block in blocks:
            if isinstance(block, dict):
                block_type = block.get("type")
                if isinstance(block_type, str) and block_type in aliases:
                    block = {**block, "type": aliases[block_type]}
            normalized.append(block)
        return {**value, "blocks": normalized}


class DocumentSpec(BaseModel):
    """A complete, renderable document with no binary content."""

    title: str
    subtitle: str | None = None
    doc_type: DocType
    metadata: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(min_length=1)
