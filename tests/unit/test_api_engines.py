# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/engines endpoint."""
from fastapi.testclient import TestClient

from backend.app import app


client = TestClient(app)


def test_engines_returns_list():
    res = client.get("/api/v1/engines")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list) and len(data) > 0
    assert all("name" in e for e in data)


def test_engines_filter_by_category():
    res = client.get("/api/v1/engines", params={"category": "general"})
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)


def test_engines_have_required_fields():
    res = client.get("/api/v1/engines")
    data = res.json()
    for engine in data:
        assert "name" in engine
        assert "url" in engine
        assert "description" in engine
        assert "category" in engine
        assert "title" in engine
