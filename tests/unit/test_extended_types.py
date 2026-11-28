# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the framework-neutral request model."""

import asyncio

from starlette.requests import Request

from backend.api.search import first_value_mapping, searx_request
from searx.extended_types import CIHeaders


def test_ci_headers_keep_first():
    """Duplicate request headers follow Werkzeug's first-value semantics."""
    headers = CIHeaders((("X-Test", "first"), ("x-test", "second")))

    assert headers["X-Test"] == "first"
    assert headers.get("x-test") == "first"


def test_first_mapping_keeps_first():
    """Duplicate query and form values follow Werkzeug's first-value semantics."""
    mapping = first_value_mapping((("category", "first"), ("category", "second")))

    assert mapping["category"] == "first"


def test_fastapi_keeps_first():
    """FastAPI request data follows Werkzeug's first-value semantics."""
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "server": ("example.test", 80),
        "client": ("192.0.2.1", 1234),
        "root_path": "",
        "path": "/api/v1/search",
        "raw_path": b"/api/v1/search",
        "query_string": b"q=first&q=second",
        "headers": [
            (b"host", b"example.test"),
            (b"x-test", b"first"),
            (b"x-test", b"second"),
        ],
    }

    async def request_data():
        return await searx_request(Request(scope))

    req = asyncio.run(request_data())

    assert req.args["q"] == "first"
    assert req.headers["x-test"] == "first"
