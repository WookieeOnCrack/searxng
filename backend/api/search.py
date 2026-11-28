# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/search — run a search through the kernel and return JSON results."""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from timeit import default_timer
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from backend.models.api import (
    AnswerItem,
    CompatSearchResponse,
    ErrorResponse,
    ResultItem,
    SearchResponse,
)
from backend.services.preferences import build_preferences
from searx.engines import categories as engine_categories
from searx.exceptions import SearxParameterException
from searx.extended_types import SXNG_Request, request_scope
from searx.preferences import ValidationException
from searx.search import SearchWithPlugins
from searx.search.processors import PROCESSORS
from searx.webadapter import get_search_query_from_webapp, get_selected_categories
from searx.webutils import exception_classname_to_text

if TYPE_CHECKING:
    from types import ModuleType

    from searx.enginelib import Engine
    from searx.preferences import Preferences
    from searx.result_types import LegacyResult, MainResult
    from searx.result_types.answer import BaseAnswer
    from searx.results import ResultContainer, UnresponsiveEngine
    from searx.search.models import SearchQuery
    from searx.search.processors import ProcessorMap


router = APIRouter(tags=["search"])

#: The ``error_type`` the engine layer records when an engine answered with
#: a CAPTCHA challenge. ``searx/network/raise_for_httperror.py`` raises
#: ``SearxEngineCaptchaException``, and ``EngineProcessor.handle_exception``
#: stores the exception's qualified class name in ``UnresponsiveEngine``.
CAPTCHA_ERROR_TYPE = "searx.exceptions.SearxEngineCaptchaException"


def first_value_mapping(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Build a mapping that keeps the first value for duplicate keys.

    Werkzeug's ``MultiDict.items()`` and ``Headers.get()`` return the first
    value when a key occurs more than once.  Request data converted from
    Starlette uses the same rule so both frontends expose identical values.
    """
    result: dict[str, str] = {}
    for key, value in items:
        result.setdefault(key, value)
    return result


async def searx_request(request: Request) -> SXNG_Request:
    form: dict[str, str] = {}
    if request.method in ("POST", "PUT"):
        form = first_value_mapping(
            (key, value) for key, value in (await request.form()).multi_items() if isinstance(value, str)
        )
    return SXNG_Request(
        form=form,
        args=first_value_mapping(request.query_params.multi_items()),
        headers=first_value_mapping((k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in request.headers.raw),
        cookies=request.cookies,
        remote_addr=request.client.host if request.client else "",
        path=request.url.path,
        scheme=request.url.scheme,
        host=request.headers.get("host", ""),
        query_string=request.url.query.encode("latin-1") if request.url.query else b"",
        method=request.method,
        endpoint="",  # only applies to flask webapp
    )


@router.get(
    "/search",
    response_model=None,
    summary="Run a search",
    description=(
        "Run a search query across the enabled engines and return the aggregated "
        "results.\n\n"
        "The default (`format=json`) response is the current API contract: a "
        "`SearchResponse` with the results, answers, corrections, suggestions, "
        "infoboxes and the list of unresponsive engines. `format=compat` returns "
        "the legacy `?format=json` shape of the Flask webapp instead, for "
        "consumers that have not migrated yet.\n\n"
        "An empty `q` returns an empty result set rather than an error. An "
        "invalid `language`, `safesearch`, `time_range`, `timeout_limit` or "
        "`categories` value is rejected with HTTP 400."
    ),
    operation_id="search",
    responses={
        200: {
            "description": "Search results",
            # The endpoint returns one of two shapes, selected by the
            # `format` parameter. Declaring the union here makes FastAPI
            # publish both models in components/schemas -- without it the
            # $refs below point at schemas that do not exist, which makes
            # the whole document invalid.
            #
            # The generated schema is an `anyOf`, not a `oneOf`, and that
            # is deliberate: a SearchResponse body also satisfies the
            # CompatSearchResponse schema (the extra keys are ignored), so
            # "exactly one of these" would be false.
            "model": SearchResponse | CompatSearchResponse,
        },
        400: {
            "description": (
                "The request was rejected because a query parameter is invalid "
                "(unknown `language`, `categories`, or an out-of-range "
                "`safesearch`, `time_range` or `timeout_limit` value)."
            ),
            "model": ErrorResponse,
        },
    },
)
async def search(  # pylint: disable=invalid-name,redefined-builtin,too-many-locals,too-many-arguments
    req: Annotated[SXNG_Request, Depends(searx_request)],
    q: Annotated[str, Query(min_length=0, description="Search query")] = "",
    pageno: Annotated[int, Query(ge=1)] = 1,
    language: Annotated[str, Query()] = "all",
    safesearch: Annotated[int, Query(ge=0, le=2)] = 0,
    # Deliberately NOT a Literal: the kernel's `parse_time_range()`
    # (searx/webadapter.py) is the single validator for this parameter and
    # raises SearxParameterException for an unknown value, which the endpoint
    # turns into HTTP 400 -- the same answer the legacy Flask app gives. A
    # Literal here would make FastAPI reject the request with 422 first, and
    # the endpoint's 400 handler would never run.
    time_range: Annotated[str | None, Query()] = None,
    timeout_limit: Annotated[float | None, Query()] = None,
    categories: Annotated[str, Query(description="Comma-separated categories")] = "",
    response_format: Annotated[
        str,
        Query(
            alias="format",
            pattern="^(json|compat)$",
            description=(
                "Response format. 'json' (default) is the current API contract. "
                "'compat' is the legacy Flask webapp `?format=json` contract, kept "
                "for existing consumers."
            ),
        ),
    ] = "json",
) -> CompatSearchResponse | SearchResponse:
    # pylint: disable=assigning-non-slot
    # Function is async with thread offloading. Long term goal is for upstream SearXNG
    # to remove the dependency on Flask's request context / sxng_request for native
    # async processing.

    # A missing/empty query is a valid "no results" request: return a
    # well-formed empty response instead of erroring out. The kernel's
    # webadapter raises SearxParameterException for an empty q, so short-
    # circuit here to keep the endpoint a clean 200.
    if not q:
        if response_format == "compat":
            return CompatSearchResponse(query=q)
        return SearchResponse(
            query=q,
            pageno=pageno,
            languages=[language],
            categories=[c for c in categories.split(",") if c],
            engines=[],
            start_info={"time_range": time_range or "", "locale": language},
            results=[],
            results_per_category={},
            engines_captcha=[],
            engines_deactivated=[],
            engines_incompatible=[],
            answers=[],
            corrections=[],
            suggestions=[],
            infoboxes=[],
            unresponsive_engines=[],
        )

    # Build the form dict the kernel's webadapter expects.
    # NOTE: the key must be "time_range" — `searx.webadapter.parse_time_range()`
    # reads `form.get('time_range')`. A dotted ".time_range" key is silently
    # ignored, which would make the API report a filter it never applied.
    form = {
        "q": q,
        "pageno": str(pageno),
        "language": language,
        "safesearch": str(safesearch),
        "time_range": time_range or "",
        "timeout_limit": str(timeout_limit) if timeout_limit else "",
        "categories": categories,
    }

    preferences = build_preferences()
    try:
        preferences.parse_dict(form)
    except ValidationException as exc:
        # An unknown category (or other invalid preference value) is a
        # client error. The legacy Flask app answers these with HTTP 400
        # and a JSON body {"error": "<message>"} (searx/webapp.py:index_error).
        # Letting the exception escape turns it into a 500, which tells the
        # caller "the server is broken" when the request was simply invalid.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = await run_in_threadpool(
            _run_search_in_thread, req, form, preferences, response_format, language, time_range
        )
    except SearxParameterException as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    return result


def _run_search_in_thread(
    req: SXNG_Request,
    form: dict[str, str],
    preferences: Preferences,
    response_format: str,
    language: str,
    time_range: str | None,
) -> CompatSearchResponse | SearchResponse:
    """Run the search inside a worker thread for async offloading"""
    # Run the kernel search inside a SXNG request_scope context: the
    # kernel expects one (copy_current_request_context in
    # search_multiple_requests, sxng_request.preferences/form in
    # locales.localeselector and plugins). Mirrors webapp.pre_request.
    with request_scope(req):  # replaces the puppet flask context
        req.preferences = preferences
        req.start_time = default_timer()
        req.render_time = 0.0
        req.timings = []
        req.errors = []
        req.form = form

        try:
            sq, raw, _unknown, _notoken, locale = get_search_query_from_webapp(preferences, form)
        except SearxParameterException as exc:
            # Invalid time_range / language / safesearch / timeout_limit.
            # Same reasoning as above: a malformed query parameter is a
            # client error, not a server error. The legacy app returns 400
            # with the exception's message.
            raise HTTPException(status_code=400, detail=exc.message) from exc

        # Run the kernel search. sxng_request is the LocalProxy; inside the
        # pushed context SearchWithPlugins resolves it via
        # ._get_current_object().
        search_obj = SearchWithPlugins(sq, req, [])
        result_container = search_obj.search()

    # Serialize into the API contract.
    if response_format == "compat":
        # Legacy contract: exactly the top-level keys and result shape that
        # the Flask webapp's `?format=json` produced, so existing consumers
        # keep working unchanged.
        return CompatSearchResponse(
            query=sq.query,
            results=[_compat_result_to_dict(r) for r in result_container.get_ordered_results()],
            answers=[_compat_answer_to_dict(a) for a in result_container.answers],
            corrections=list(result_container.corrections),
            infoboxes=[_compat_infobox_to_dict(i) for i in result_container.infoboxes],
            suggestions=list(result_container.suggestions),
            unresponsive_engines=_unresponsive_engines(result_container),
        )

    results = [_result_to_item(r) for r in result_container.get_ordered_results()]
    answers = [_answer_to_item(a) for a in result_container.answers]
    results_per_category: dict[str, int] = {}
    for r in results:
        results_per_category[r.category] = results_per_category.get(r.category, 0) + 1

    # The REQUESTED categories, not ``sq.categories``: the latter is derived
    # from the selected engine refs, so a category whose engines are all
    # switched off would not appear in it at all.
    if raw.specific:
        # A bang query ("!engine" / "!category") selects its engines
        # explicitly and ignores the form's categories (searx/webadapter.py:
        # get_search_query_from_webapp), so the scope is the category of the
        # bang.
        scope_categories = [ref.category for ref in raw.enginerefs if ref.category in engine_categories]
    else:
        scope_categories = get_selected_categories(preferences, form)

    engine_states = _engine_states(
        result_container.unresponsive_engines,
        sq,
        scope_categories,
        engine_categories,
        preferences.engines.get_disabled(),
        PROCESSORS,
    )

    return SearchResponse(
        query=sq.query,
        pageno=sq.pageno,
        languages=[language],
        categories=sq.categories,
        engines=[e.name for e in sq.engineref_list],
        start_info={"time_range": time_range or "", "locale": locale},
        results=results,
        results_per_category=results_per_category,
        engines_captcha=engine_states["engines_captcha"],
        engines_deactivated=engine_states["engines_deactivated"],
        engines_incompatible=engine_states["engines_incompatible"],
        answers=answers,
        corrections=list(result_container.corrections),
        suggestions=list(result_container.suggestions),
        infoboxes=[_compat_infobox_to_dict(i) for i in result_container.infoboxes],
        unresponsive_engines=_unresponsive_engines(result_container),
    )


def _result_to_item(result: MainResult | LegacyResult) -> ResultItem:
    """Convert a result (MainResult/LegacyResult) to a ResultItem (backend.models.api.ResultItem) for the API.

    ``as_dict()`` exposes the plain msgspec field names (``engine``,
    ``category``, ``score``, ``content``, ``author``, ``pubdate``,
    ``metadata``, ``length``, ...) — there are no dotted ``".engine"``
    aliases.

    ``length`` is a ``datetime.timedelta`` on the engine layer; the legacy
    ``?format=json`` encoder serialised it as a number of seconds, while the
    human-readable form (``"30:42"``) is what the Jinja templates show. We
    expose the readable string.
    """
    d = result.as_dict()
    length = d.get("length")
    if isinstance(length, datetime.timedelta):
        length = str(length)
    return ResultItem(
        url=d.get("url") or "",
        title=d.get("title") or "",
        block=d.get("content") or "",
        img_thumbnail=d.get("img_src") or d.get("thumbnail") or None,
        width=d.get("width"),
        height=d.get("height"),
        engine=d.get("engine") or "",
        category=d.get("category") or "",
        score=d.get("score") or 0,
        urls=d.get("urls") or [],
        children=d.get("children") or [],
        author=d.get("author") or "",
        pubdate=d.get("pubdate") or "",
        metadata=d.get("metadata") or "",
        length=length or "",
    )


def _compat_result_to_dict(result: MainResult | LegacyResult) -> dict[str, Any]:
    """Legacy result shape: the raw ``as_dict()`` with JSON-safe values.

    Mirrors ``searx.webutils.JSONEncoder``: ``datetime`` -> ISO 8601,
    ``timedelta`` -> seconds, ``set`` -> list.
    """
    d = dict(result.as_dict())
    for key, value in d.items():
        if isinstance(value, datetime.datetime):
            d[key] = value.isoformat()
        elif isinstance(value, datetime.timedelta):
            d[key] = value.total_seconds()
        elif isinstance(value, (set, frozenset)):
            d[key] = list(value)
    return d


def _compat_answer_to_dict(answer: BaseAnswer) -> dict[str, Any]:
    """Legacy answer shape (the raw ``as_dict()`` with JSON-safe values)."""
    d = dict(answer.as_dict())
    for key, value in d.items():
        if isinstance(value, datetime.datetime):
            d[key] = value.isoformat()
        elif isinstance(value, datetime.timedelta):
            d[key] = value.total_seconds()
        elif isinstance(value, (set, frozenset)):
            d[key] = list(value)
    return d


def _compat_infobox_to_dict(infobox: LegacyResult) -> dict[str, Any]:
    """Legacy infobox shape (LegacyResult dicts, JSON-safe values)."""
    try:
        raw = infobox.as_dict()
    except AttributeError:
        raw = dict(infobox)
    out = {}
    for key, value in raw.items():
        if isinstance(value, datetime.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, datetime.timedelta):
            out[key] = value.total_seconds()
        elif isinstance(value, (set, frozenset)):
            out[key] = list(value)
        else:
            out[key] = value
    return out


def _captcha_engines(unresponsive_engines: set[UnresponsiveEngine]) -> list[str]:
    """Names of the engines that answered with a CAPTCHA challenge.

    The engine layer records the failing exception's qualified class name in
    ``UnresponsiveEngine.error_type`` (``searx/search/processors/abstract.py:
    handle_exception``), so a CAPTCHA shows up as
    ``searx.exceptions.SearxEngineCaptchaException``. An engine suspended
    earlier because of a CAPTCHA carries the same reason.
    """
    return sorted({u.engine for u in unresponsive_engines if u.error_type == CAPTCHA_ERROR_TYPE})


def _deactivated_engines(
    unresponsive_engines: set[UnresponsiveEngine],
    query_categories: list[str],
    categories_map: dict[str, list[Engine | ModuleType]],
    disabled_engines: list[tuple[str, str]],
    selected_engines: set[str],
) -> list[str]:
    """Names of the engines that are switched off and were not queried.

    Two upstream sources feed this list:

    - engines that were already suspended when the search started. The
      engine layer skips them in ``EngineProcessor.extend_container_if_suspended``
      and records them in ``unresponsive_engines`` with ``suspended=True``.
    - engines the user turned off. ``Preferences.engines.get_disabled()``
      returns ``(engine_name, category)`` pairs and the engine layer skips an
      engine per *category* pair (``searx/webadapter.py:
      get_engineref_from_category_list``), so an engine only counts as
      deactivated when it is disabled in every queried category it belongs
      to -- otherwise it still runs for the other category.
    """
    deactivated = {u.engine for u in unresponsive_engines if u.suspended}

    disabled_pairs = set(disabled_engines)
    engine_categories_of: dict[str, set[str]] = {}
    for category in query_categories:
        for engine in categories_map.get(category, []):
            engine_categories_of.setdefault(engine.name, set()).add(category)

    for name, cats in engine_categories_of.items():
        if name in selected_engines:
            # The engine ran for at least one of its categories.
            continue
        if all((name, category) in disabled_pairs for category in cats):
            deactivated.add(name)

    return sorted(deactivated)


def _incompatible_engines(search_query: SearchQuery, processors: ProcessorMap) -> list[str]:
    """Names of the engines that were selected but cannot serve the search.

    Two ways an engine is selected yet unable to answer:

    - it has no registered processor at all -- the engine's ``init()`` failed
      and ``ProcessorMap.init`` never registered it (``searx/search/
      processors/__init__.py``), so ``Search._get_requests`` skips it.
    - ``EngineProcessor.get_params()`` returns ``None``, which means the
      search condition is not supported by the engine: no paging support on
      page > 1, the page is beyond ``max_page``, the requested time range is
      unsupported, or the query does not match a specialised engine's syntax
      (currency, dictionary, URL search).

    An engine that can serve the query in at least one of its categories is
    not incompatible -- it still contributes results. The same rule the
    engine layer applies in ``Search._get_requests``.
    """
    served: dict[str, bool] = {}
    for engine_ref in search_query.engineref_list:
        processor = processors.get(engine_ref.name)
        if processor is None:
            served.setdefault(engine_ref.name, False)
            continue
        try:
            params = processor.get_params(search_query, engine_ref.category)
        except Exception:  # pylint: disable=broad-except
            # A broken processor must not break the response; treat the
            # engine as incompatible (it produced no params).
            params = None
        served[engine_ref.name] = served.get(engine_ref.name, False) or params is not None

    return sorted(name for name, did_serve in served.items() if not did_serve)


def _engine_states(
    unresponsive_engines: set[UnresponsiveEngine],
    search_query: SearchQuery,
    query_categories: list[str],
    categories_map: dict[str, list[Engine | ModuleType]],
    disabled_engines: list[tuple[str, str]],
    processors: ProcessorMap,
) -> dict[str, list[str]]:
    """Derive the three engine-state lists of the search response."""
    selected_engines = {ref.name for ref in search_query.engineref_list}
    return {
        "engines_captcha": _captcha_engines(unresponsive_engines),
        "engines_deactivated": _deactivated_engines(
            unresponsive_engines,
            query_categories,
            categories_map,
            disabled_engines,
            selected_engines,
        ),
        "engines_incompatible": _incompatible_engines(search_query, processors),
    }


def _unresponsive_engines(result_container: ResultContainer) -> list[list[str]]:
    """Legacy ``unresponsive_engines`` shape: a list of [engine, message].

    Mirrors ``searx.webutils.get_translated_errors()`` but without the
    Flask-Babel translation (the FastAPI backend does not initialise
    Flask-Babel, so ``gettext()`` would raise ``KeyError: 'babel'``).
    The untranslated engine-class message is used instead.
    """
    out = []
    for unresponsive in sorted(result_container.unresponsive_engines, key=lambda e: e.engine):
        msg = exception_classname_to_text.get(unresponsive.error_type) or exception_classname_to_text[None]
        if unresponsive.suspended:
            msg = f"Suspended: {msg}"
        out.append([unresponsive.engine, msg])
    return out


def _answer_to_item(answer: BaseAnswer) -> AnswerItem:
    """Convert an answer (searx.result_types.answer.Answer) to an AnswerItem (backend.models.api.AnswerItem)."""
    d = answer.as_dict()
    return AnswerItem(
        title=d.get("title") or "",
        url=d.get("url") or "",
        content=d.get("answer") or d.get("content") or "",
        engine=d.get("engine") or "",
        is_infopage=bool(d.get("is_infopage", False)),
    )
