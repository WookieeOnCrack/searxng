# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for the /api/v1/search query-parameter contract.

Two classes of bug are covered here:

1. ``time_range`` was silently dropped. ``backend/api/search.py`` builds the
   kernel form with the key ``".time_range"``, but
   ``searx/webadapter.py:parse_time_range()`` reads ``form.get('time_range')``.
   The dotted key is ignored, so the engine query carried ``time_range=None``
   while ``start_info.time_range`` echoed the requested value back. An API
   consumer filtering by date got unfiltered results and believed it had
   filtered. These tests assert on the ``SearchQuery`` object that the kernel
   hands to the engine layer -- not on the echoed ``start_info`` value.

2. An invalid query parameter raised ``SearxParameterException`` (or
   ``ValidationException``) straight out of the endpoint, which FastAPI
   turned into an HTTP 500. A malformed request is a client error and must
   be answered with a 4xx, not a 500.
"""

from __future__ import annotations

import types
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

import backend.api.search as search_api
from backend.app import app
from searx.search import Search
from searx.search.processors.abstract import EngineProcessor

# ``raise_server_exceptions=False`` so an unhandled exception in the endpoint
# surfaces as a 500 response instead of propagating out of the test.
client = TestClient(app, raise_server_exceptions=False)


@contextmanager
def capture_search_query():
    """Capture the SearchQuery the endpoint builds and hand it to the kernel.

    ``Search.search_multiple_requests`` is stubbed out so no engine request is
    actually dispatched (no network, no CAPTCHA noise), but everything up to
    that point -- form construction, ``parse_time_range()``, engine-ref
    selection -- runs for real.
    """
    captured: list[Any] = []

    original_get_search_query = search_api.get_search_query_from_webapp
    original_dispatch = Search.search_multiple_requests

    def spy_get_search_query(preferences, form):
        result = original_get_search_query(preferences, form)
        # The kernel returns a 5-tuple; the first element is the SearchQuery.
        captured.append(result[0])
        return result

    def no_dispatch(self, requests):  # pylint: disable=unused-argument
        return None

    search_api.get_search_query_from_webapp = spy_get_search_query
    Search.search_multiple_requests = no_dispatch
    try:
        yield captured
    finally:
        search_api.get_search_query_from_webapp = original_get_search_query
        Search.search_multiple_requests = original_dispatch


def build_engine_params(search_query, *, time_range_support: bool):
    """Build the RequestParams the kernel would send to an engine.

    Uses the real ``EngineProcessor.get_params()`` -- the code that turns a
    SearchQuery into the per-engine request params -- with a stub engine, so
    the assertion does not depend on which engines happen to be registered
    (the legacy test suite reloads the registry, and the registered set
    differs between an isolated run and a full-suite run).
    """
    engine = types.SimpleNamespace(
        name="stubengine",
        paging=False,
        max_page=0,
        time_range_support=time_range_support,
        language="",
    )
    processor = types.SimpleNamespace(engine=engine)
    return EngineProcessor.get_params(processor, search_query, "general")  # pyright: ignore[reportArgumentType]


def test_search_time_range_filter():
    """The engine query must carry the requested time_range filter.

    This is the regression test for the silently-dropped filter: the
    assertion is on the SearchQuery handed to the engine layer and on the
    request params derived from it, not on the echoed ``start_info`` value.
    """
    with capture_search_query() as captured:
        res = client.get("/api/v1/search", params={"q": "time range probe", "time_range": "day"})

    assert res.status_code == 200
    assert captured, "the endpoint did not run a search"

    search_query = captured[0]
    assert search_query.time_range == "day", "the engine query lost the time_range filter"

    # And the filter must survive into the params sent to an engine that
    # supports it.
    params = build_engine_params(search_query, time_range_support=True)
    assert params is not None
    assert params["time_range"] == "day", "the engine request params lost the time_range filter"


def test_no_time_range_no_filter():
    """Without the parameter, the engine query must carry no time_range filter."""
    with capture_search_query() as captured:
        res = client.get("/api/v1/search", params={"q": "time range probe"})

    assert res.status_code == 200
    assert captured, "the endpoint did not run a search"

    search_query = captured[0]
    assert search_query.time_range is None

    params = build_engine_params(search_query, time_range_support=True)
    assert params is not None
    assert params["time_range"] is None, "an unexpected time_range filter was sent to the engine"


def test_invalid_time_range_4xx():
    """An invalid time_range is a client error, not a server error."""
    res = client.get("/api/v1/search", params={"q": "probe", "time_range": "bogus"})
    assert res.status_code == 400, f"expected 400, got {res.status_code}: {res.text[:200]}"


def test_invalid_language_4xx():
    """An invalid language is a client error, not a server error."""
    res = client.get("/api/v1/search", params={"q": "probe", "language": "not-a-locale-code"})
    assert res.status_code == 400, f"expected 400, got {res.status_code}: {res.text[:200]}"


def test_invalid_category_4xx():
    """An invalid category is a client error, not a server error."""
    res = client.get("/api/v1/search", params={"q": "probe", "categories": "not_a_category"})
    assert res.status_code == 400, f"expected 400, got {res.status_code}: {res.text[:200]}"
