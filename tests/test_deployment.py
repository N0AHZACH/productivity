"""Static deployment-context checks for files required by the container build."""

from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, pattern) or fnmatch(Path(path).name, pattern) for pattern in patterns)


def test_docker_context_contains_build_and_runtime_markdown() -> None:
    patterns = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    required = [
        "README.md",
        "pyproject.toml",
        "app/static/index.html",
        *(f"app/prompts/{path.name}" for path in (ROOT / "app/prompts").glob("*.md")),
    ]

    for relative_path in required:
        assert (ROOT / relative_path).is_file(), relative_path
        assert not _ignored(relative_path, patterns), relative_path
    assert _ignored(".env", patterns)
