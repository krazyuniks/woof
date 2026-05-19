"""Module entry point: ``python -m woof`` dispatches to the CLI."""

from __future__ import annotations

from woof.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
