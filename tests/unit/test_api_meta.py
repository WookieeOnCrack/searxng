# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/meta endpoint."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)

#: ``/meta`` owns instance-wide metadata only. The engine, category, locale
#: and theme catalogues live in ``/engines`` and ``/preferences``.
CATALOGUE_FIELDS = {"engines", "categories", "locales", "styles", "default_theme"}


def test_meta_returns_exactly_the_instance_metadata():
    res = client.get("/api/v1/meta")
    assert res.status_code == 200
    assert set(res.json()) == {"brand", "version", "formats"}


def test_meta_does_not_duplicate_the_catalogues():
    data = client.get("/api/v1/meta").json()
    for field in CATALOGUE_FIELDS:
        assert field not in data


def test_meta_reports_the_instance_version_and_formats():
    data = client.get("/api/v1/meta").json()
    assert data["version"]
    assert isinstance(data["formats"], list) and len(data["formats"]) > 0
    assert isinstance(data["brand"], dict)


# --- cross-endpoint catalogue ownership -----------------------------------
#
# The three endpoints split one former payload three ways.  Each endpoint's
# own tests pin *its* shape; only a comparison of the three live payloads
# proves the separation.  A re-added engine catalogue under a new key in
# `/meta` or `/preferences` -- or a second plugin map -- would leave every
# per-endpoint test green.  These checks are the lock on that contract.
#
# They are deliberately payload-to-payload: the shared facts (engine names,
# plugin ids) are read from `/engines`, never from the production helpers,
# so a helper that stops splitting the data cannot hide behind them.

#: Fields `/preferences` must never return again.  Every one of them had a
#: duplicate owner before the separation.
PREFERENCES_CATALOGUE_FIELDS = {
    "engines",
    "categories",
    "locales",
    "formats",
    "styles",
    "plugins",
    "engine_pairs",
    "default",
    "current",
}

#: The exact field set of one canonical engine record (pinned in
#: ``test_api_engines.py``).  Any dictionary carrying all of them is an
#: engine catalogue, wherever it is found.
ENGINE_RECORD_FIELDS = {
    "name",
    "url",
    "description",
    "shortcut",
    "categories",
    "default_enabled",
    "capabilities",
}

#: The exact field set of one plugin state record.
PLUGIN_STATE_FIELDS = {"name", "description", "preference_section", "default_enabled", "enabled"}


def _get_json(path):
    """GET ``path`` with a clean cookie jar and return the decoded body."""
    client.cookies.clear()
    res = client.get(path)
    assert res.status_code == 200, path
    return res.json()


def _walk(node, path=()):
    """Yield ``(path, node)`` for every node of a JSON payload.

    ``path`` is the tuple of dictionary keys and list indexes that reaches
    the node, so a test can assert *where* a structure occurs, not only
    that it exists.
    """
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, path + (index,))


def test_meta_and_preferences_expose_no_foreign_catalogue():
    """/meta keeps instance metadata; /preferences keeps preferences only."""
    meta = _get_json("/api/v1/meta")
    preferences = _get_json("/api/v1/preferences")

    repeated = set(meta) & CATALOGUE_FIELDS
    assert not repeated, f"/meta returns removed catalogue fields {sorted(repeated)}"

    repeated = set(preferences) & PREFERENCES_CATALOGUE_FIELDS
    assert not repeated, f"/preferences returns removed catalogue fields {sorted(repeated)}"


def test_only_the_engines_endpoint_carries_engine_records():
    """One engine record per engine, and only in /engines."""
    engines = _get_json("/api/v1/engines")
    meta = _get_json("/api/v1/meta")
    preferences = _get_json("/api/v1/preferences")

    assert engines, "the canonical engine catalogue must not be empty"
    for record in engines:
        assert set(record) == ENGINE_RECORD_FIELDS, record.get("name")
        assert not {"settings", "engine_states", "plugin_states"} & set(record)

    for name, payload in (("meta", meta), ("preferences", preferences)):
        repeats = [
            path for path, node in _walk(payload) if isinstance(node, dict) and ENGINE_RECORD_FIELDS <= set(node)
        ]
        assert not repeats, f"/{name} repeats the engine catalogue at {repeats}"


def test_engine_names_in_preferences_are_pair_identifiers_only():
    """A pair key may name an engine; no other structure may."""
    engines = _get_json("/api/v1/engines")
    preferences = _get_json("/api/v1/preferences")
    engine_names = {record["name"] for record in engines}
    membership = {record["name"]: record["categories"] for record in engines}

    states = preferences["engine_states"]
    assert states

    for pair, enabled in states.items():
        name, separator, category = pair.rpartition("__")
        assert separator, f"{pair!r} is not an <engine>__<category> key"
        assert name in engine_names, f"{pair!r} names an engine /engines does not publish"
        assert category in membership[name], f"{pair!r} names a category the engine is not registered in"
        assert isinstance(enabled, bool), pair

    # The map is keyed by pairs.  A dictionary keyed by plain engine names
    # would be the second catalogue this change removes.
    for path, node in _walk(preferences):
        if isinstance(node, dict):
            assert not set(node) & engine_names, f"engine names used as keys at {path}"


def test_plugin_ids_appear_only_in_the_plugin_state_map():
    """Plugin metadata is advertised once, under /preferences.plugin_states."""
    engines = _get_json("/api/v1/engines")
    meta = _get_json("/api/v1/meta")
    preferences = _get_json("/api/v1/preferences")

    plugin_ids = set(preferences["plugin_states"])
    assert plugin_ids

    for path, node in _walk(preferences):
        if isinstance(node, dict) and "preference_section" in node:
            assert set(node) == PLUGIN_STATE_FIELDS, f"plugin record at {path} has {sorted(node)}"
            assert path[:1] == ("plugin_states",), f"plugin record outside plugin_states at {path}"

    # Plugin ids are dictionary keys exactly once: the state map itself.
    for path, node in _walk(preferences):
        if isinstance(node, dict) and path != ("plugin_states",):
            assert not set(node) & plugin_ids, f"plugin ids used as keys at {path}"

    # Neither of the other endpoints enumerates plugins at all.
    for name, payload in (("meta", meta), ("engines", engines)):
        found = [
            path
            for path, node in _walk(payload)
            if isinstance(node, dict) and {"plugins", "plugin_states", "preference_section"} & set(node)
        ]
        assert not found, f"/{name} carries plugin data at {found}"
