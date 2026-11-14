# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for the ``?format=compat`` legacy contract.

``?format=compat`` is the alias that keeps existing API consumers (scripts,
AI agents, integrations) working unchanged while they migrate to the richer
``?format=json`` contract. It must therefore reproduce the legacy Flask
webapp's ``?format=json`` shape exactly.

The legacy shape is defined by ``searx/webutils.py:get_json_response()``:
seven top-level keys, and per-result the RAW ``as_dict()`` payload (21
fields) rather than the trimmed ``ResultItem``. These tests pin both, so a
future change to the modern contract cannot silently break the compat alias.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.search import _compat_result_to_dict
from backend.app import app
from searx.result_types import MainResult

client = TestClient(app, raise_server_exceptions=False)

#: The exact top-level keys of the legacy ``?format=json`` response.
LEGACY_KEYS = {
    "query",
    "results",
    "answers",
    "corrections",
    "infoboxes",
    "suggestions",
    "unresponsive_engines",
}


def test_compat_legacy_keys():
    """The compat response must carry the legacy keys -- no more, no fewer."""
    res = client.get("/api/v1/search", params={"q": "compat probe", "format": "compat"})
    assert res.status_code == 200
    data = res.json()
    assert set(data.keys()) == LEGACY_KEYS, f"compat keys changed: {sorted(data.keys())}"


def test_compat_legacy_payload():
    """A compat result must be the raw as_dict() payload, not the trimmed one.

    The modern ``ResultItem`` drops 18 of the 21 fields ``MainResult`` exposes
    (and renames ``content`` to ``block``). Consumers of the legacy contract
    rely on those fields, so the compat path must keep them.
    """
    result = MainResult(
        url="https://example.org/x",
        title="Example",
        content="some content",
        engine="testengine",
        category="general",
        score=5.0,
        author="An Author",
        pubdate="2026-01-01",
        metadata="some metadata",
    )

    compat = _compat_result_to_dict(result)

    # Fields the modern ResultItem drops -- they must survive here.
    assert compat["author"] == "An Author"
    assert compat["pubdate"] == "2026-01-01"
    assert compat["metadata"] == "some metadata"
    assert "publishedDate" in compat
    assert "length" in compat
    assert "thumbnail" in compat
    assert "img_src" in compat
    assert "views" in compat
    assert "template" in compat
    assert "parsed_url" in compat

    # The legacy field name for the body text is "content", NOT "block".
    assert compat["content"] == "some content"
    assert "block" not in compat


# pylint: disable=invalid-name
def test_compat_unresponsive_engines():
    """``unresponsive_engines`` entries must stay ``[engine, message]`` pairs."""
    res = client.get("/api/v1/search", params={"q": "compat probe", "format": "compat"})
    assert res.status_code == 200
    for entry in res.json()["unresponsive_engines"]:
        assert isinstance(entry, list) and len(entry) == 2, f"unexpected shape: {entry!r}"
        assert isinstance(entry[0], str)
        assert isinstance(entry[1], str)


def test_compat_and_json_served():
    """The compat alias must not shadow the modern contract."""
    compat = client.get("/api/v1/search", params={"q": "compat probe", "format": "compat"})
    modern = client.get("/api/v1/search", params={"q": "compat probe", "format": "json"})

    assert compat.status_code == 200
    assert modern.status_code == 200

    compat_keys = set(compat.json().keys())
    modern_keys = set(modern.json().keys())

    assert compat_keys == LEGACY_KEYS
    assert modern_keys != LEGACY_KEYS, "the modern format must not collapse into the legacy shape"
    assert "results_per_category" in modern_keys
    assert "start_info" in modern_keys
