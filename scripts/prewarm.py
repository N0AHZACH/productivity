"""Populate the server hot cache for a fast recording session."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _post(base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/agent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--example",
        action="append",
        default=["examples/standard_request.json", "examples/complex_request.json"],
        help="Request JSON file; repeat for a custom set.",
    )
    args = parser.parse_args()

    for relative in args.example:
        path = ROOT / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        started = time.perf_counter()
        try:
            result = _post(args.url, payload)
        except (OSError, urllib.error.URLError) as exc:
            print(f"Could not warm {path}: {exc}")
            return 1
        elapsed = time.perf_counter() - started
        print(
            json.dumps(
                {
                    "example": str(path.relative_to(ROOT)),
                    "status": result.get("status"),
                    "plan_source": result.get("plan_source_detail"),
                    "filename": result.get("filename"),
                    "elapsed_seconds": round(elapsed, 2),
                    "hot_cache": any(
                        event.get("data", {}).get("hot_cache")
                        for event in result.get("trace", [])
                    ),
                    "note": "Repeat the same request in the browser for hot-cache replay.",
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
