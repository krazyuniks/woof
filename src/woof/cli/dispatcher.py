"""Dispatch adapter for woof subprocesses.

The full dispatch implementation lives in woof/bin/woof (cmd_dispatch).
This module exposes a thin programmatic interface for testing and future
migration of the dispatch logic out of the monolithic script.
"""

from __future__ import annotations
