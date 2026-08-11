"""Backward-compatible single-worker launcher for the modular HTTP adapter."""

from __future__ import annotations

import argparse

import uvicorn

from api.app import create_default_app


# Importing the module constructs only configuration and lazy registry metadata.
# Heavyweight models are checked during lifespan startup, never at import time.
app = create_default_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Private face algorithm API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    uvicorn.run(
        "api.app:create_default_app",
        factory=True,
        host=arguments.host,
        port=arguments.port,
        workers=1,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
