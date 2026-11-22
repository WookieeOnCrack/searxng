# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/engines endpoint."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

from fastapi.testclient import TestClient

from backend.app import app
from searx.engines import engines

client = TestClient(app)

#: The exact field set of one engine in the canonical catalogue. Pinned on
#: purpose: FastAPI filters the payload through the response model, so
#: asserting against ``EngineMeta.model_fields`` would be tautological. A
#: pinned set is what catches a field added to or removed from the model
#: without the contract being updated deliberately.
EXPECTED_ENGINE_FIELDS = {
    "name",
    "url",
    "description",
    "shortcut",
    "categories",
    "default_enabled",
    "capabilities",
}

CAPABILITY_FIELDS = {"safesearch", "time_range_support", "paging"}


def _payload_by_name() -> dict[str, dict]:
    res = client.get("/api/v1/engines")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list) and len(data) > 0
    return {engine["name"]: engine for engine in data}


def _multi_category_engine():
    """Return an engine registered in more than one category.

    The catalogue is populated at import time by ``backend.app``; this
    instance always registers such engines, so a missing one means the
    fixture, not the contract, broke.
    """
    return next(engine for engine in engines.values() if len(engine.categories or []) > 1)


def test_engines_returns_list():
    res = client.get("/api/v1/engines")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list) and len(data) > 0
    assert all("name" in e for e in data)


def test_engines_payload_matches_the_contract():
    """Every engine carries exactly the fields of the engine contract."""
    for engine in _payload_by_name().values():
        assert set(engine) == EXPECTED_ENGINE_FIELDS


def test_engines_drop_the_legacy_singular_category_key():
    """``category`` was replaced by the full ``categories`` membership list."""
    for engine in _payload_by_name().values():
        assert "category" not in engine


def test_engines_categories_contain_every_membership():
    """``categories`` carries the complete membership, not just the first."""
    payload = _payload_by_name()
    engine = _multi_category_engine()
    assert payload[engine.name]["categories"] == list(engine.categories)


def test_engines_default_enabled_mirrors_upstream_disabled_flag():
    payload = _payload_by_name()
    for engine in engines.values():
        if engine.name not in payload:
            continue
        assert payload[engine.name]["default_enabled"] == (not engine.disabled)


def test_engines_capabilities_match_upstream_attributes():
    payload = _payload_by_name()
    for engine in engines.values():
        if engine.name not in payload:
            continue
        capabilities = payload[engine.name]["capabilities"]
        assert set(capabilities) == CAPABILITY_FIELDS
        assert capabilities == {
            "safesearch": bool(engine.safesearch),
            "time_range_support": bool(engine.time_range_support),
            "paging": bool(engine.paging),
        }


def test_engines_filter_by_category_matches_full_membership():
    """The filter matches any category an engine is registered in."""
    engine = _multi_category_engine()
    for category in engine.categories:
        data = client.get("/api/v1/engines", params={"category": category}).json()
        returned = {entry["name"]: entry for entry in data}
        assert engine.name in returned, f"{engine.name} missing from category {category!r}"
        assert returned[engine.name]["categories"] == list(engine.categories)
