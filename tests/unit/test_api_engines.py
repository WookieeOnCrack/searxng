# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/engines endpoint."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

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


def test_engines_payload_matches_the_contract():
    """Every engine carries exactly the fields of the engine contract.

    The field set is pinned here on purpose. Asserting against
    ``EngineMeta.model_fields`` instead would be tautological: FastAPI
    filters the response through the response model, so the payload can
    never disagree with it. Pinning the set is what catches a field that
    is added to or removed from the model without the contract being
    updated deliberately.
    """
    res = client.get("/api/v1/engines")
    data = res.json()
    expected = {"name", "url", "description", "category", "shortcut"}
    for engine in data:
        assert set(engine) == expected
