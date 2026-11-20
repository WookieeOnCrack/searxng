# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/preferences endpoint."""

import base64
import json
import zlib

from fastapi.testclient import TestClient

from backend.app import app
from backend.services.preferences import (
    build_preferences,
    filter_document,
    mint_token,
    sign_payload,
)

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


def test_shared_builder_is_used():
    prefs = build_preferences()
    assert prefs is not None
    assert prefs.key_value_settings["categories"].get_value() == ["general"]


def test_get_reports_client():
    client.cookies.clear()
    res = client.get("/api/v1/preferences")
    assert res.status_code == 200
    data = res.json()
    assert not data["client"]
    assert data["token"] == ""
    assert data["current"] == data["default"]


def test_advertised_names_valid():
    """Everything the catalogue advertises must be acceptable in a document."""
    client.cookies.clear()
    res = client.get("/api/v1/preferences")
    data = res.json()
    prefs = build_preferences()

    # Every advertised engine pair is a legal document value.
    assert data["engine_pairs"]
    for pair in data["engine_pairs"]:
        applicable, skipped = filter_document({"disabled_engines": [pair]}, prefs)
        assert applicable["disabled_engines"] == [pair]
        assert not skipped

    # Every advertised plugin id is a legal document value.
    assert data["plugins"]
    for plugin in data["plugins"]:
        applicable, skipped = filter_document({"disabled_plugins": [plugin["id"]]}, prefs)
        assert applicable["disabled_plugins"] == [plugin["id"]]
        assert not skipped

    # And the advertised sets are exactly the expressible ones.
    assert sorted(data["engine_pairs"]) == sorted(prefs.engines.choices)
    assert sorted(p["id"] for p in data["plugins"]) == sorted(prefs.plugins.choices)


def test_plugin_ids_are_unique():
    """A plugin id is advertised once, even if it is registered twice.

    The plugin storage can hold several objects for the same id, and the
    catalogue is keyed by id -- so the catalogue must not publish the same
    plugin twice.
    """
    client.cookies.clear()
    res = client.get("/api/v1/preferences")
    ids = [plugin["id"] for plugin in res.json()["plugins"]]
    assert len(ids) == len(set(ids))


def test_get_applies_client_doc():
    client.cookies.clear()
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": token})
    assert res.status_code == 200
    data = res.json()
    assert data["client"]["categories"] == ["general"]
    assert data["token"] == token


def test_get_reads_the_cookie():
    client.cookies.clear()
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    client.cookies.set("sxng_preferences", token)
    res = client.get("/api/v1/preferences")
    assert res.status_code == 200
    assert res.json()["client"]["categories"] == ["general"]


def test_get_reads_the_query_param():
    client.cookies.clear()
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    res = client.get("/api/v1/preferences", params={"preferences": token})
    assert res.status_code == 200
    assert res.json()["client"]["categories"] == ["general"]


def test_query_param_wins():
    """The query parameter is the most explicit transport, so it wins."""
    client.cookies.clear()
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    res = client.get(
        "/api/v1/preferences",
        params={"preferences": token},
        headers={"X-SearXNG-Preferences": "not-a-token"},
    )
    assert res.status_code == 200
    assert res.json()["client"]["categories"] == ["general"]


def test_malformed_token_rejected():
    client.cookies.clear()
    res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": "not-a-token"})
    assert res.status_code == 400
    assert res.json()["detail"].startswith("preferences_invalid")


def test_get_rejects_bad_signature():
    client.cookies.clear()
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    payload, _, _ = token.partition(".")
    res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": payload + "." + ("0" * 64)})
    assert res.status_code == 403
    assert res.json()["detail"].startswith("preferences_invalid_signature")


def test_get_rejects_a_stale_token():
    """A signed token whose epoch no longer matches the instance is stale."""
    client.cookies.clear()
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    payload, _, _ = token.partition(".")
    body = json.loads(zlib.decompress(base64.urlsafe_b64decode(payload)))
    body["epoch"] = "0" * 16
    fresh = base64.urlsafe_b64encode(
        zlib.compress(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    ).decode("ascii")
    stale = fresh + "." + sign_payload(fresh)
    res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": stale})
    assert res.status_code == 409
    assert res.json()["detail"].startswith("preferences_stale")


def test_get_reports_a_skipped_key():
    """A key the instance cannot apply is dropped from `client` and reported.

    The epoch covers only engine pairs, plugins and locks, so a document
    naming a value the instance rejects is *not* stale -- it is answered
    with 200, the key dropped from `client` and listed in `skipped`.
    """
    client.cookies.clear()
    prefs = build_preferences()
    # Mint a token whose document names a theme this instance does not
    # offer. The document is otherwise applicable, so the epoch matches.
    doc: dict[str, object] = {"theme": "no-such-theme"}
    token = mint_token(doc, prefs)
    res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": token})
    assert res.status_code == 200
    data = res.json()
    assert "theme" not in data["client"]
    assert any("theme" in note for note in data["skipped"])
