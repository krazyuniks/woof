"""E146 fixture — Pydantic model referenced by a contract_decision.

The model intentionally lives outside ``webapp/`` so that the woof check_cd
test does not need to instantiate the real GTS webapp module graph.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CommentEdit(BaseModel):
    """Body schema for PATCH /api/v1/comments/<id>."""

    body: str = Field(min_length=1, max_length=10000)
