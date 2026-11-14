# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/preferences endpoint."""

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_preferences_structure():
    res = client.get("/api/v1/preferences")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["locales"], list)
    assert isinstance(data["engines"], list)
    assert "formats" in data
    assert "current" in data


def test_preferences_has_categories():
    res = client.get("/api/v1/preferences")
    data = res.json()
    assert isinstance(data["categories"], list)
    assert len(data["categories"]) > 0


def test_preferences_has_locales():
    res = client.get("/api/v1/preferences")
    data = res.json()
    assert isinstance(data["locales"], list)
    assert len(data["locales"]) > 0
