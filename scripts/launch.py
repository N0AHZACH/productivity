"""Launch the local Productivity demo with one command."""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}/"


def _is_ready(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/ready", timeout=0.75) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _open_when_ready(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_ready(host, port):
            webbrowser.open(_url(host, port))
            return
        time.sleep(0.25)
    print(
        f"Productivity is running at {_url(host, port)}; open it manually if the browser "
        "did not launch.",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the Productivity live demo.")
    parser.add_argument("--host", default=os.getenv("PRODUCTIVITY_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PRODUCTIVITY_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument("--reload", action="store_true", help="Reload when files change.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    args = parser.parse_args()

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    url = _url(args.host, args.port)

    if _is_ready(args.host, args.port):
        print(f"Productivity is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    if _port_is_open(args.host, args.port):
        print(
            f"Port {args.port} is already in use by another service. "
            f"Try: productivity --port {args.port + 1}",
            file=sys.stderr,
        )
        return 1

    print(f"Starting Productivity at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Thread(
            target=_open_when_ready,
            args=(args.host, args.port),
            daemon=True,
        ).start()

    import uvicorn

    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            reload_dirs=[str(ROOT)] if args.reload else None,
            log_level="info",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
