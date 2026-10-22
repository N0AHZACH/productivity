"""Manual smoke test against the configured real LLM provider."""

import asyncio

from pydantic import BaseModel

from app.core.config import get_settings
from app.llm.factory import build_llm


class Ping(BaseModel):
    pong: str


async def main() -> None:
    settings = get_settings()
    llm = build_llm(settings)
    result = await llm.complete_json(
        [
            {"role": "system", "content": "Reply with one JSON object."},
            {"role": "user", "content": 'Return {"pong": "hello"} exactly.'},
        ],
        Ping,
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
