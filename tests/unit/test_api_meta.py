# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/meta endpoint."""
from fastapi.testclient import TestClient

from backend.app import app


client = TestClient(app)


def test_meta_returns_engines_categories_locales():
    res = client.get("/api/v1/meta")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["engines"], list) and len(data["engines"]) > 0
    assert isinstance(data["categories"], list) and len(data["categories"]) > 0
    assert isinstance(data["locales"], list) and len(data["locales"]) > 0
    assert data["version"]


def test_meta_engines_have_required_fields():
    res = client.get("/api/v1/meta")
    data = res.json()
    for engine in data["engines"]:
        assert "name" in engine
        assert "url" in engine
        assert "description" in engine
        assert "category" in engine
        assert "title" in engine


def test_meta_categories_have_engines():
    res = client.get("/api/v1/meta")
    data = res.json()
    for category in data["categories"]:
        assert "name" in category
        assert "title" in category
        assert "engines" in category
