"""Manual smoke test against the configured real LLM provider."""

import asyncio

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import DocPilotError
from app.core.logging import configure_logging
from app.llm.factory import build_llm


class Ping(BaseModel):
    pong: str


async def _run() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)
    if settings.primary_provider_missing_key:
        print(
            f"Provider '{settings.primary_provider}' requires an API key. "
            "Configure it in .env or set PRIMARY_PROVIDER=ollama."
        )
        return 2

    llm = build_llm(settings)
    try:
        result = await llm.complete_json(
            [
                {"role": "system", "content": "Reply with one JSON object."},
                {"role": "user", "content": 'Return {"pong": "hello"} exactly.'},
            ],
            Ping,
        )
    except DocPilotError as exc:
        print(f"LLM ping failed [{exc.code}]: {exc}")
        return 1

    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
