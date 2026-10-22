"""Visual themes for deterministic Word rendering."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Theme:
    """Fonts, colors, and furniture for one document style."""

    font_body: str = "Calibri"
    font_heading: str = "Calibri"
    accent_hex: str = "1F4E79"
    accent_light_hex: str = "DCE6F1"
    body_pt: float = 11
    h1_pt: float = 20
    h2_pt: float = 15
    h3_pt: float = 12
    logo_path: Path | str | None = None
    footer_text: str = ""


DEFAULT_THEME = Theme()

SLATE_THEME = Theme(
    font_body="Aptos",
    font_heading="Aptos Display",
    accent_hex="334155",
    accent_light_hex="E2E8F0",
    body_pt=10.5,
    h1_pt=19,
    h2_pt=14,
    h3_pt=11.5,
    footer_text="Confidential",
)
