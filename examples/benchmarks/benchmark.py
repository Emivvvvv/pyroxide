"""Compatibility entry point for the named benchmark manifest controller."""

from __future__ import annotations

import sys
from pathlib import Path

from runner import main as runner_main


def main() -> int:
    arguments = sys.argv[1:]
    if "--manifest" not in arguments:
        arguments = ["--manifest", str(Path(__file__).with_name("manifests") / "smoke.toml"), *arguments]
    return runner_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
