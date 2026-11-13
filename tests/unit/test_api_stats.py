# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/stats endpoint."""
from fastapi.testclient import TestClient

from backend.app import app


client = TestClient(app)


def test_stats_returns_engines():
    res = client.get("/api/v1/stats")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["engines"], list)
    assert "total" in data


def test_stats_engines_have_required_fields():
    res = client.get("/api/v1/stats")
    data = res.json()
    for engine in data["engines"]:
        assert "name" in engine
        assert "url" in engine
        assert "category" in engine
        assert "title" in engine
        assert "result_count" in engine
        assert "error_count" in engine
        assert "score_per_result" in engine
        assert "time" in engine


def test_stats_errors_returns_dict():
    res = client.get("/api/v1/stats/errors")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)
