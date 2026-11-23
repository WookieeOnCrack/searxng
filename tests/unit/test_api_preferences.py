# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/preferences endpoint.

The endpoint owns the *writable* preference contract: one descriptor per
setting, the effective state of every expressible engine pair, and the
metadata, instance default and effective state of every plugin.  These
tests pin that shape, the values it derives from upstream's ``Preferences``
objects, and the client-document semantics that must survive the change.
"""

import base64
import json
import zlib
from unittest.mock import patch

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
from searx.engines import categories as engine_categories
from searx.locales import LOCALE_NAMES
from searx.preferences import (
    BooleanSetting,
    EnumStringSetting,
    MapSetting,
    MultipleChoiceSetting,
)

client = TestClient(app)

#: The complete set of top-level fields of the response.  The old
#: duplicated catalogues (categories, engines, plugins, engine_pairs,
#: locales, formats, styles, default, current) are gone.
TOP_LEVEL_FIELDS = {
    "settings",
    "engine_states",
    "plugin_states",
    "client",
    "skipped",
    "locked",
    "token",
}

#: Every setting descriptor carries these fields.
DESCRIPTOR_FIELDS = {"type", "default", "current", "locked"}


def _get_preferences(**kwargs):
    """GET /api/v1/preferences with a clean cookie jar."""
    client.cookies.clear()
    res = client.get("/api/v1/preferences", **kwargs)
    assert res.status_code == 200
    return res.json()


def _post_preferences(body):
    """POST /api/v1/preferences with a clean cookie jar."""
    client.cookies.clear()
    res = client.post("/api/v1/preferences", json=body)
    assert res.status_code == 200
    return res.json()


def _token_for(document):
    """Return a signed token carrying ``document`` for this instance."""
    prefs = build_preferences()
    doc, skipped = filter_document(document, prefs)
    assert not skipped, skipped
    return mint_token(doc, prefs)


def _expected_type(setting):
    """Return the JSON type a setting's value serializes to."""
    if isinstance(setting, MultipleChoiceSetting):
        return "array"
    if isinstance(setting, BooleanSetting):
        return "boolean"
    if isinstance(setting, MapSetting):
        return "integer" if isinstance(setting.get_value(), int) else "string"
    return "string"


def test_top_level_fields():
    # Top level fields are exactly the same as the new contract
    data = _get_preferences()
    assert set(data) == TOP_LEVEL_FIELDS


def test_settings_writable_cats():
    # Settings contain every writable setting and default category
    prefs = build_preferences()
    data = _get_preferences()
    assert set(data["settings"]) == set(prefs.key_value_settings) | {"default_category"}


def test_setting_descriptors():
    """`choices` is present exactly when the setting has finite choices."""
    # Check every setting descriptor has the required fields
    prefs = build_preferences()
    data = _get_preferences()

    for name, descriptor in data["settings"].items():
        setting = prefs.key_value_settings.get(name)
        has_choices = setting is None or isinstance(setting, (MultipleChoiceSetting, EnumStringSetting, MapSetting))
        expected = DESCRIPTOR_FIELDS | ({"choices"} if has_choices else set())
        assert set(descriptor) == expected, f"{name}: {descriptor}"
        assert isinstance(descriptor["locked"], bool)


def test_setting_types():
    # check setting types describe the json value correctly
    prefs = build_preferences()
    data = _get_preferences()

    for name, setting in prefs.key_value_settings.items():
        assert data["settings"][name]["type"] == _expected_type(setting), name

    assert data["settings"]["categories"]["type"] == "array"
    assert data["settings"]["safesearch"]["type"] == "integer"
    assert data["settings"]["image_proxy"]["type"] == "boolean"
    assert data["settings"]["theme"]["type"] == "string"
    assert data["settings"]["default_category"]["type"] == "string"


def test_multiple_choice_order():
    # confirm multiple choices preserve order
    prefs = build_preferences()
    data = _get_preferences()
    setting = prefs.key_value_settings["categories"]
    values = [choice["value"] for choice in data["settings"]["categories"]["choices"]]
    assert values == list(getattr(setting, "choices"))


def test_categories_include_none():
    # categories should include the none sentinel
    data = _get_preferences()
    categories = data["settings"]["categories"]
    values = [choice["value"] for choice in categories["choices"]]
    assert "none" in values
    assert categories["default"] == ["general"]
    assert categories["current"] == ["general"]


def test_default_category_string():
    # default category should be a string setting without none
    data = _get_preferences()
    descriptor = data["settings"]["default_category"]

    assert descriptor["type"] == "string"
    assert descriptor["default"] == "general"
    assert descriptor["current"] == "general"
    assert descriptor["locked"] is False

    values = [choice["value"] for choice in descriptor["choices"]]
    assert values == list(engine_categories)
    assert "none" not in values


def test_default_category_current():
    # default category current should follow the client document
    token = _token_for({"default_category": "images"})
    data = _get_preferences(headers={"X-SearXNG-Preferences": token})

    descriptor = data["settings"]["default_category"]
    assert descriptor["default"] == "general"
    assert descriptor["current"] == "images"
    assert data["client"]["default_category"] == "images"


def test_locale_choices():
    # confirm locale choices include title and rtl
    data = _get_preferences()
    choices = data["settings"]["locale"]["choices"]

    for choice in choices:
        assert set(choice) == {"value", "title", "rtl"}
        assert isinstance(choice["rtl"], bool)

    by_value = {choice["value"]: choice for choice in choices}
    assert by_value[""]["title"] == "Default"
    assert by_value["de"]["title"] == LOCALE_NAMES["de"]
    assert by_value["ar"]["rtl"] is True
    assert by_value["en"]["rtl"] is False


def test_non_locale_choices():
    # confirm non-locale choices exclude rtl
    data = _get_preferences()
    for name in ("categories", "safesearch", "theme", "default_category"):
        for choice in data["settings"][name]["choices"]:
            assert "rtl" not in choice, name


def test_choice_titles():
    # confirm choice titles are deterministic english labels
    data = _get_preferences()

    categories = {c["value"]: c["title"] for c in data["settings"]["categories"]["choices"]}
    assert categories["general"] == "General"
    assert categories["images"] == "Images"
    assert categories["none"] == "None"

    safesearch = {c["value"]: c["title"] for c in data["settings"]["safesearch"]["choices"]}
    assert safesearch == {0: "Off", 1: "Moderate", 2: "Strict"}

    theme = {c["value"]: c["title"] for c in data["settings"]["theme"]["choices"]}
    assert theme["simple"] == "Simple"

    default_category = {c["value"]: c["title"] for c in data["settings"]["default_category"]["choices"]}
    assert default_category["general"] == "General"
    assert default_category["images"] == "Images"


def test_engine_states_pairs():
    # confirm engine states are the exppressible pairs
    prefs = build_preferences()
    data = _get_preferences()

    assert data["engine_states"]
    assert set(data["engine_states"]) == set(prefs.engines.choices)
    for pair, state in data["engine_states"].items():
        assert isinstance(state, bool), pair


def test_advertised_engine_pairs():
    # confirm advertised engine pairs are legal values
    data = _get_preferences()
    prefs = build_preferences()
    for pair in data["engine_states"]:
        applicable, skipped = filter_document({"disabled_engines": [pair]}, prefs)
        assert applicable["disabled_engines"] == [pair]
        assert not skipped


def test_engine_states_client_doc():
    # confirm engine states follow the client document
    prefs = build_preferences()
    pair = next(pair for pair, enabled in prefs.engines.default_choices.items() if not enabled)
    token = _token_for({"enabled_engines": [pair]})
    data = _get_preferences(headers={"X-SearXNG-Preferences": token})

    assert data["engine_states"][pair] is True


def test_plugin_states_installed():
    # confirm plugin states keys are the installed plugins
    prefs = build_preferences()
    data = _get_preferences()

    assert data["plugin_states"]
    assert set(data["plugin_states"]) == set(prefs.plugins.choices)


def test_plugin_states_metadata():
    # confirm plugin states carry metadata and effective state
    data = _get_preferences()

    for state in data["plugin_states"].values():
        assert set(state) == {"name", "description", "preference_section", "default_enabled", "enabled"}
        assert "id" not in state, "the plugin id is the dictionary key"
        assert isinstance(state["default_enabled"], bool)
        assert isinstance(state["enabled"], bool)

    tracker = data["plugin_states"]["tracker_url_remover"]
    assert tracker["name"] == "Tracker URL remover"
    assert tracker["preference_section"] == "privacy"


def test_plugin_ids_occur_once():
    data = _get_preferences()
    ids = list(data["plugin_states"])
    assert len(ids) == len(set(ids))


def test_advertised_plugins_values():
    # confirm advertised plugins are legal values
    data = _get_preferences()
    prefs = build_preferences()
    for plugin_id in data["plugin_states"]:
        applicable, skipped = filter_document({"disabled_plugins": [plugin_id]}, prefs)
        assert applicable["disabled_plugins"] == [plugin_id]
        assert not skipped


def test_plugin_states_client_doc():
    # confirm plugin states follow the client document
    token = _token_for({"disabled_plugins": ["calculator"]})
    data = _get_preferences(headers={"X-SearXNG-Preferences": token})

    calculator = data["plugin_states"]["calculator"]
    assert calculator["default_enabled"] is True
    assert calculator["enabled"] is False


def test_plugin_states_no_default():
    """A plugin absent from the default choices reports disabled, not a crash."""
    # confirm plugin states degrade to disabled when default is missing
    prefs = build_preferences()
    plugin_id = sorted(prefs.plugins.choices)[0]
    prefs.plugins.default_choices = {
        key: value for key, value in prefs.plugins.default_choices.items() if key != plugin_id
    }
    with patch("backend.api.preferences.build_preferences", return_value=prefs):
        data = _get_preferences()

    assert data["plugin_states"][plugin_id]["default_enabled"] is False


def test_get_no_client_document():
    # confirm get without a client document responds with instance defaults
    prefs = build_preferences()
    data = _get_preferences()

    for name, descriptor in data["settings"].items():
        assert descriptor["default"] == descriptor["current"], name

    assert data["engine_states"] == {pair: bool(state) for pair, state in prefs.engines.default_choices.items()}
    for plugin_id, state in data["plugin_states"].items():
        assert state["enabled"] is state["default_enabled"], plugin_id


def test_get_changed_settings():
    # confirm get reports the changed settings values
    token = _token_for({"image_proxy": True})
    data = _get_preferences(headers={"X-SearXNG-Preferences": token})

    assert data["settings"]["image_proxy"]["default"] is False
    assert data["settings"]["image_proxy"]["current"] is True


def test_shared_builder_is_used():
    prefs = build_preferences()
    assert prefs is not None
    assert prefs.key_value_settings["categories"].get_value() == ["general"]


def test_get_reports_client():
    data = _get_preferences()
    assert not data["client"]
    assert data["token"] == ""


def test_get_applies_client_doc():
    token = _token_for({"categories": ["general"]})
    data = _get_preferences(headers={"X-SearXNG-Preferences": token})
    assert data["client"]["categories"] == ["general"]
    assert data["token"] == token


def test_get_reads_the_cookie():
    token = _token_for({"categories": ["general"]})
    client.cookies.clear()
    client.cookies.set("sxng_preferences", token)
    res = client.get("/api/v1/preferences")
    assert res.status_code == 200
    assert res.json()["client"]["categories"] == ["general"]


def test_get_reads_the_query_param():
    token = _token_for({"categories": ["general"]})
    data = _get_preferences(params={"preferences": token})
    assert data["client"]["categories"] == ["general"]


def test_query_param_wins():
    """The query parameter is the most explicit transport, so it wins."""
    token = _token_for({"categories": ["general"]})
    data = _get_preferences(
        params={"preferences": token},
        headers={"X-SearXNG-Preferences": "not-a-token"},
    )
    assert data["client"]["categories"] == ["general"]


def test_malformed_token_rejected():
    client.cookies.clear()
    res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": "not-a-token"})
    assert res.status_code == 400
    assert res.json()["detail"].startswith("preferences_invalid")


def test_get_rejects_bad_signature():
    token = _token_for({"categories": ["general"]})
    payload, _, _ = token.partition(".")
    client.cookies.clear()
    res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": payload + "." + ("0" * 64)})
    assert res.status_code == 403
    assert res.json()["detail"].startswith("preferences_invalid_signature")


def test_get_rejects_a_stale_token():
    """A signed token whose epoch no longer matches the instance is stale."""
    token = _token_for({"categories": ["general"]})
    payload, _, _ = token.partition(".")
    body = json.loads(zlib.decompress(base64.urlsafe_b64decode(payload)))
    body["epoch"] = "0" * 16
    fresh = base64.urlsafe_b64encode(
        zlib.compress(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    ).decode("ascii")
    stale = fresh + "." + sign_payload(fresh)
    client.cookies.clear()
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
    prefs = build_preferences()
    theme = prefs.key_value_settings["theme"].get_value()
    doc, skipped = filter_document({"theme": theme}, prefs)
    assert not skipped
    token = mint_token(doc, prefs)

    original = settings["ui"]["default_theme"]
    try:
        # The instance stops offering that theme.
        settings["ui"]["default_theme"] = "dark"
        client.cookies.clear()
        res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": token})
    finally:
        settings["ui"]["default_theme"] = original

    assert res.status_code == 200
    data = res.json()
    assert "theme" not in data["client"]
    assert any("theme" in note for note in data["skipped"])


def test_query_param_in_schema():
    """The `preferences` query parameter is documented in the OpenAPI schema."""
    operation = app.openapi()["paths"]["/api/v1/preferences"]["get"]
    names = {param["name"] for param in operation.get("parameters") or []}
    assert "preferences" in names


def test_token_header_in_schema():
    """The `X-SearXNG-Preferences` header is documented in the OpenAPI schema."""
    operation = app.openapi()["paths"]["/api/v1/preferences"]["get"]
    names = {param["name"] for param in operation.get("parameters") or []}
    assert "X-SearXNG-Preferences" in names


def test_post_returns_token():
    data = _post_preferences({"categories": ["general"]})
    assert data["token"]
    assert data["client"]["categories"] == ["general"]
    assert data["settings"]["categories"]["current"] == ["general"]


def test_post_sets_cookie():
    client.cookies.clear()
    res = client.post("/api/v1/preferences", json={"categories": ["general"]})
    assert "sxng_preferences" in res.cookies
    # Do NOT compare res.cookies[...] to the token directly. A cookie value
    # containing "=" is quoted by http.cookies, and httpx returns the quoted
    # form -- verified: a token ending in "==" comes back as
    # '"eyJhIjoxfQ==.deadbeef"'. Strip the quotes instead.
    assert res.cookies["sxng_preferences"].strip('"') == res.json()["token"]


def test_post_response_structure():
    # check test post response has the new structure
    data = _post_preferences({"image_proxy": True, "default_category": "images"})

    assert set(data) == TOP_LEVEL_FIELDS
    assert set(data["settings"]) >= {"image_proxy", "default_category"}
    assert data["settings"]["image_proxy"]["current"] is True
    assert data["settings"]["image_proxy"]["default"] is False
    assert data["settings"]["default_category"]["current"] == "images"

    prefs = build_preferences()
    assert set(data["engine_states"]) == set(prefs.engines.choices)
    assert set(data["plugin_states"]) == set(prefs.plugins.choices)


def test_post_skips_unknown_key():
    """An unknown key is skipped, not rejected -- and it is reported."""
    data = _post_preferences({"nope": 1, "categories": ["general"]})
    # The rest of the document still applied.
    assert data["client"]["categories"] == ["general"]
    # The unknown key did not.
    assert "nope" not in data["client"]
    # And the client is told it was skipped.
    assert any("nope" in note for note in data["skipped"])


def test_post_invalid_keys():
    """The request schema documents the body; it does not validate it.

    An instance may be handed a template document written for a different
    instance, so the runtime keeps accepting -- and skipping -- keys the
    schema never advertised.  Publishing the schema must not turn a skip
    into a 422.
    """
    # confirm post will accept keys not in the published schema
    data = _post_preferences({"image_proxy": True, "not_a_setting": "x"})

    assert data["settings"]["image_proxy"]["current"] is True
    assert "not_a_setting" not in data["client"]
    assert any("not_a_setting" in note for note in data["skipped"])


def test_post_skips_bad_engine():
    """A template naming an engine this instance lacks still applies."""
    data = _post_preferences({"disabled_engines": ["no such engine__general"], "categories": ["general"]})
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
    data = _post_preferences({})
    assert data["client"] == {}
    assert data["token"]
    for name, descriptor in data["settings"].items():
        assert descriptor["current"] == descriptor["default"], name


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
