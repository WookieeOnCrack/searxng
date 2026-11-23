# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/autocomplete — return autocomplete suggestions for a query prefix."""

from __future__ import annotations

import contextlib
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from backend.models.api import AutocompleteResponse
from backend.services.preferences import build_preferences
from searx.answerers import STORAGE as answerers_storage
from searx.autocomplete import search_autocomplete
from searx.query import RawTextQuery
from searx.result_types import Answer

router = APIRouter(tags=["autocomplete"])


@router.get(
    "/autocomplete",
    response_model=AutocompleteResponse,
    summary="Autocomplete a query",
    description=(
        "Return completion suggestions for a partially typed query.\n\n"
        "The suggestions come from the instance's configured autocomplete backend "
        "and from its answerers; when neither produces anything for the prefix, "
        "the list is empty. `count` caps the number of suggestions returned."
    ),
    operation_id="autocomplete",
)
async def autocomplete(  # pylint: disable=invalid-name
    q: Annotated[str, Query(description="Query prefix")] = "",
    lang: Annotated[str, Query()] = "all",  # why is this not being used?
    method: Annotated[Literal["GET", "POST"], Query()] = "POST",  # why is this not being used?
    count: Annotated[int, Query(ge=1, le=30)] = 10,
) -> AutocompleteResponse:
    return await run_in_threadpool(_autocomplete, q, lang, method, count)


def _autocomplete(query: str, _lang: str, _method: str, count: int) -> AutocompleteResponse:
    # Build preferences to get the autocomplete backend and locale.
    prefs = build_preferences()

    # Set blocked engines.
    disabled_engines = prefs.engines.get_disabled()

    # Parse query.
    raw_text_query = RawTextQuery(query, disabled_engines)
    sug_prefix = raw_text_query.getQuery()

    # Run answerers (inner results).
    results: list[str] = []
    results.extend(obj.answer for obj in answerers_storage.ask(sug_prefix) if isinstance(obj, Answer))
    # Normal autocompletion results only appear if no inner results returned
    # and there is a query part.
    if len(raw_text_query.autocomplete_list) == 0 and len(sug_prefix) > 0:
        sxng_locale = prefs.get_value("language") or "all"  # or the lang param?
        backend_name = prefs.get_value("autocomplete") or "duckduckgo"

        with contextlib.suppress(KeyError, AttributeError):
            results.extend(
                raw_text_query.changeQuery(result).getFullQuery()
                for result in search_autocomplete(backend_name, sug_prefix, sxng_locale)
                if result != sug_prefix
            )
    # Autocomplete list from the query (e.g. "q !bang").
    if len(raw_text_query.autocomplete_list) > 0:
        results.extend(
            raw_text_query.get_autocomplete_full_query(autocomplete_text)
            for autocomplete_text in raw_text_query.autocomplete_list
        )
    return AutocompleteResponse(suggestions=results[:count])
