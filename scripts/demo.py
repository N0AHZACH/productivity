"""Run Productivity from the command line using the configured real LLM provider."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.agent.orchestrator import Orchestrator
from app.container import Container
from app.core.config import get_settings
from app.core.errors import DocPilotError
from app.core.logging import configure_logging


def _load_request(path: Path) -> str:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("request"), str):
        raise ValueError(f"{path} must contain a JSON object with a string 'request' field")
    return str(payload["request"])


async def _run(example: Path) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)
    if settings.primary_provider_missing_key:
        print(
            f"Provider '{settings.primary_provider}' requires an API key. "
            "Configure it in .env or set PRIMARY_PROVIDER=ollama."
        )
        return 2

    try:
        request = _load_request(example)
    except (OSError, ValueError) as exc:
        print(f"Could not load request file: {exc}")
        return 2
    container = Container.build(settings)
    print(f"Running {example.name} with {settings.primary_provider} ({settings.model_strong})...")
    try:
        state = await Orchestrator(container).run(request)
    except DocPilotError as exc:
        print(f"Run failed [{exc.code}]: {exc}")
        return 1

    summary = {
        "run_id": state.run_id,
        "status": state.status.value,
        "document_path": state.document_path,
        "plan_source": state.plan_source,
        "sections": len(state.spec.sections) if state.spec else 0,
        "reflection_passed": [report.passed for report in state.reflection],
        "warnings": state.warnings,
    }
    print(json.dumps(summary, indent=2))
    return 0 if state.status.value in {"completed", "completed_with_warnings"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--example",
        type=Path,
        default=Path("examples/standard_request.json"),
        help="JSON request file",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.example))


if __name__ == "__main__":
    raise SystemExit(main())
