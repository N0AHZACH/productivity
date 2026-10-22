"""Versioned system prompts and their loader."""

from pathlib import Path


def load_prompt(name: str) -> str:
    """Load a prompt markdown file by stem or filename."""
    filename = name if name.endswith(".md") else f"{name}.md"
    path = Path(__file__).parent / filename
    if not path.is_file():
        raise FileNotFoundError(f"Prompt not found: {filename}")
    return path.read_text(encoding="utf-8").strip()
