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
        "Return the canonical catalogue of search engines registered on this "
        "instance. Each engine appears exactly once with its name, website, "
        "description, keyboard shortcut, full category membership, instance "
        "default state and query capabilities.\n\n"
        "Pass `category` to restrict the list to the engines registered in a "
        "given category; the filter matches the full membership list."
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
