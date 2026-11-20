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
    verify_token,
)
from searx import settings

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

    The token is minted while the value is still valid and the instance
    drops the choice afterwards -- the realistic sequence, and the one
    that does not depend on the epoch's coverage.
    """
    client.cookies.clear()
    prefs = build_preferences()
    theme = prefs.key_value_settings["theme"].get_value()
    doc, skipped = filter_document({"theme": theme}, prefs)
    assert not skipped
    token = mint_token(doc, prefs)

    original = settings["ui"]["default_theme"]
    try:
        # The instance stops offering that theme.
        settings["ui"]["default_theme"] = "dark"
        res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": token})
    finally:
        settings["ui"]["default_theme"] = original

    assert res.status_code == 200
    data = res.json()
    assert "theme" not in data["client"]
    assert any("theme" in note for note in data["skipped"])


def test_query_param_in_schema():
    """The `preferences` query parameter is documented in the OpenAPI schema.

    The handler reads the transports off the raw request, so FastAPI
    cannot infer the parameter on its own -- but a client generating
    from the schema must still be able to discover it.
    """
    operation = app.openapi()["paths"]["/api/v1/preferences"]["get"]
    names = {param["name"] for param in operation.get("parameters") or []}
    assert "preferences" in names


def test_token_header_in_schema():
    """The `X-SearXNG-Preferences` header is documented in the OpenAPI schema."""
    operation = app.openapi()["paths"]["/api/v1/preferences"]["get"]
    names = {param["name"] for param in operation.get("parameters") or []}
    assert "X-SearXNG-Preferences" in names


def test_post_returns_token():
    client.cookies.clear()
    res = client.post("/api/v1/preferences", json={"categories": ["general"]})
    assert res.status_code == 200
    data = res.json()
    assert data["token"]
    assert data["client"]["categories"] == ["general"]
    assert data["current"]["categories"] == ["general"]


def test_post_sets_cookie():
    client.cookies.clear()
    res = client.post("/api/v1/preferences", json={"categories": ["general"]})
    assert "sxng_preferences" in res.cookies
    # Do NOT compare res.cookies[...] to the token directly. A cookie value
    # containing "=" is quoted by http.cookies, and httpx returns the quoted
    # form -- verified: a token ending in "==" comes back as
    # '"eyJhIjoxfQ==.deadbeef"'. Strip the quotes instead.
    assert res.cookies["sxng_preferences"].strip('"') == res.json()["token"]


def test_post_skips_unknown_key():
    """An unknown key is skipped, not rejected -- and it is reported."""
    client.cookies.clear()
    res = client.post("/api/v1/preferences", json={"nope": 1, "categories": ["general"]})
    assert res.status_code == 200
    data = res.json()
    # The rest of the document still applied.
    assert data["client"]["categories"] == ["general"]
    # The unknown key did not.
    assert "nope" not in data["client"]
    # And the client is told it was skipped.
    assert any("nope" in note for note in data["skipped"])


def test_post_skips_bad_engine():
    """A template naming an engine this instance lacks still applies."""
    client.cookies.clear()
    res = client.post(
        "/api/v1/preferences",
        json={"disabled_engines": ["no such engine__general"], "categories": ["general"]},
    )
    assert res.status_code == 200
    data = res.json()
    assert "disabled_engines" not in data["client"]
    assert data["client"]["categories"] == ["general"]
    assert any("disabled_engines" in note for note in data["skipped"])


def test_post_rejects_tokens():
    client.cookies.clear()
    res = client.post("/api/v1/preferences", json={"tokens": {"e": "s"}})
    assert res.status_code == 400
    assert res.json()["detail"].startswith("preferences_tokens_not_allowed")


def test_post_rejects_non_object():
    client.cookies.clear()
    res = client.post("/api/v1/preferences", json=[1, 2])
    assert res.status_code == 400
    assert res.json()["detail"].startswith("preferences_invalid")


def test_post_rejects_null_document():
    """A JSON `null` document is a malformed document, not a missing body.

    `httpx`'s ``json=None`` sends *no body at all* (content-length 0), which
    is a missing body -- FastAPI answers that with its own 422. A body that
    is literally the JSON value ``null`` is a document this endpoint cannot
    apply, and that is the spec's 400.
    """
    client.cookies.clear()
    res = client.post(
        "/api/v1/preferences",
        content=b"null",
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 400
    assert res.json()["detail"].startswith("preferences_invalid")


def test_post_rejects_string_body():
    client.cookies.clear()
    res = client.post(
        "/api/v1/preferences",
        content=b'"general"',
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 400
    assert res.json()["detail"].startswith("preferences_invalid")


def test_post_ignores_token():
    """A token sent with POST is discarded; the body is the whole truth."""
    client.cookies.clear()
    stale = "not-a-valid-token"
    res = client.post(
        "/api/v1/preferences",
        json={"categories": ["general"]},
        headers={"X-SearXNG-Preferences": stale},
    )
    assert res.status_code == 200
    data = res.json()
    # No 400/403/409 from the garbage token -- it was simply ignored.
    assert data["client"]["categories"] == ["general"]
    assert data["token"]
    assert data["token"] != stale


def test_post_empty_document_resets():
    """POST {} is the reset operation that DELETE would have been."""
    client.cookies.clear()
    res = client.post("/api/v1/preferences", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["client"] == {}
    assert data["token"]
    assert data["current"] == data["default"]


def test_post_empty_resets_cookie():
    client.cookies.clear()
    client.post("/api/v1/preferences", json={"categories": ["general"]})
    res = client.post("/api/v1/preferences", json={})
    # Strip the quotes http.cookies adds around a value containing "=".
    assert res.cookies["sxng_preferences"].strip('"') == res.json()["token"]
    # The new cookie carries the empty document.
    prefs = build_preferences()
    doc, skipped = verify_token(res.json()["token"], prefs)
    assert not doc
    assert not skipped


def test_no_delete_route():
    """There is deliberately no DELETE route."""
    client.cookies.clear()
    res = client.delete("/api/v1/preferences")
    # 405, not 404: the path is served by other methods, just not by DELETE.
    assert res.status_code == 405
    # And the published contract does not offer it either.
    operations = app.openapi()["paths"]["/api/v1/preferences"]
    assert "delete" not in operations
    assert "get" in operations
    assert "post" in operations


def test_post_replaces_doc():
    """POST replaces; it does not merge with a previous token."""
    client.cookies.clear()
    first = client.post("/api/v1/preferences", json={"categories": ["general"]})
    token = first.json()["token"]
    second = client.post(
        "/api/v1/preferences",
        json={"image_proxy": True},
        headers={"X-SearXNG-Preferences": token},
    )
    assert second.status_code == 200
    data = second.json()
    # The categories from the first document are gone.
    assert "categories" not in data["client"]
    assert data["client"]["image_proxy"] is True
