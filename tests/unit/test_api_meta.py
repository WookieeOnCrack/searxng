# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/meta endpoint."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

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


def test_meta_engines_payload_matches_the_contract():
    """Every engine carries exactly the fields of the engine contract.

    The field set is pinned here on purpose. Asserting against
    ``EngineMeta.model_fields`` instead would be tautological: FastAPI
    filters the response through the response model, so the payload can
    never disagree with it. Pinning the set is what catches a field that
    is added to or removed from the model without the contract being
    updated deliberately.
    """
    res = client.get("/api/v1/meta")
    data = res.json()
    expected = {"name", "url", "description", "category", "shortcut"}
    for engine in data["engines"]:
        assert set(engine) == expected


def test_meta_categories_have_engines():
    res = client.get("/api/v1/meta")
    data = res.json()
    for category in data["categories"]:
        assert "name" in category
        assert "title" in category
        assert "engines" in category
