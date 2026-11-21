# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/engines — list engines, optionally filtered by category."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from backend.models.api import EngineMeta
from searx.engines import engines

router = APIRouter(tags=["engines"])


@router.get(
    "/engines",
    response_model=list[EngineMeta],
    summary="List the registered engines",
    description=(
        "Return the search engines registered on this instance, each with its "
        "name, website, description, primary category and keyboard "
        "shortcut.\n\n"
        "Pass `category` to restrict the list to the engines of a single "
        "category."
    ),
    operation_id="list_engines",
)
def list_engines(category: Annotated[str | None, Query()] = None) -> list[EngineMeta]:
    out: list[EngineMeta] = []
    for e in engines.values():
        cats = e.categories or ["general"]
        if category and category not in cats:
            continue
        out.append(EngineMeta.from_engine(e))
    return sorted(out)
