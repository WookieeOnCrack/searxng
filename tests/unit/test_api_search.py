# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/search endpoint."""

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_search_returns_results():
    res = client.get("/api/v1/search", params={"q": "open source search", "pageno": 1})
    assert res.status_code == 200
    data = res.json()
    assert data["query"] == "open source search"
    assert data["pageno"] == 1
    assert isinstance(data["results"], list)
    assert "results_per_category" in data


def test_search_requires_q():
    res = client.get("/api/v1/search")
    assert res.status_code == 200
    data = res.json()
    assert "results" in data


def test_search_answers_field():
    res = client.get("/api/v1/search", params={"q": "python"})
    assert res.status_code == 200
    data = res.json()
    assert "answers" in data
    assert isinstance(data["answers"], list)


def test_result_field_names():
    """_result_to_dict must map MainResult's plain field names, not dotted keys."""
    # pylint: disable=import-outside-toplevel
    from backend.api.search import _answer_to_item, _result_to_item
    from searx.result_types import MainResult
    from searx.result_types.answer import Answer

    r = MainResult(
        url="https://example.org/x",
        title="Example",
        content="some content",
        engine="testengine",
        category="general",
        score=5.0,
    )
    d = _result_to_item(r)
    assert d.engine == "testengine"
    assert d.category == "general"
    assert d.score == 5.0
    assert d.block == "some content"
    assert d.url == "https://example.org/x"

    a = Answer(answer="42", url="https://example.org/a", engine="calculator")
    ad = _answer_to_item(a)
    assert ad.content == "42"
    assert ad.engine == "calculator"
