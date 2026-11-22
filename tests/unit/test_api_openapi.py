# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the OpenAPI schema metadata.

The schema is the machine-readable contract for API-only consumers (AI
agents, SDK generators, the Swagger UI). It therefore has to carry more
than the bare path and response model: every operation needs a tag, a
human-readable summary, a description, and a stable operation id, and the
document has to advertise the version of the running instance.
"""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.preferences import _setting_type
from backend.app import app, create_app
from backend.openapi import TAGS
from backend.services.preferences import build_preferences
from searx.version import VERSION_STRING

client = TestClient(app)

schema = app.openapi()

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# The tag of every documented router: one tag per router, 1:1 with
# backend/api/*.py. "frontend" is deliberately absent -- the Jinja bridge
# serves HTML and is excluded from the schema.
EXPECTED_TAGS = {
    "meta",
    "search",
    "preferences",
    "autocomplete",
    "engines",
    "stats",
    "favicons",
    "images",
    "system",
}


def _operations():
    """Yield (path, method, operation) for every operation in the schema."""
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_info_version_comes_from_searx_version():
    assert schema["info"]["version"] == VERSION_STRING


def test_every_operation_has_a_tag_with_a_description():
    for path, method, operation in _operations():
        tags = operation.get("tags")
        assert tags, f"{method.upper()} {path} has no tag"
        for tag in tags:
            assert tag in TAGS, f"{method.upper()} {path} uses unknown tag {tag!r}"
            assert TAGS[tag], f"tag {tag!r} has no description"


def test_every_operation_has_a_summary_and_a_description():
    for path, method, operation in _operations():
        assert operation.get("summary"), f"{method.upper()} {path} has no summary"
        assert operation.get("description"), f"{method.upper()} {path} has no description"


def test_operation_ids_are_unique_and_not_auto_generated():
    operation_ids = [operation["operationId"] for _, _, operation in _operations()]
    assert all(operation_ids)
    assert len(operation_ids) == len(set(operation_ids))
    for operation_id in operation_ids:
        # FastAPI's fallback ids embed the path and the method
        # ("search_api_v1_search_get") -- they are not stable across a
        # route move and are useless to a code generator.
        assert "_api_v1_" not in operation_id, f"auto-generated operation id {operation_id!r}"
        assert not operation_id.endswith("_get"), f"auto-generated operation id {operation_id!r}"


def test_top_level_tags_are_declared_and_described():
    declared = schema.get("tags")
    assert declared
    for tag in declared:
        assert tag.get("description"), f"tag {tag['name']!r} has no description"
        assert tag["name"] in TAGS, f"tag {tag['name']!r} is not in the catalogue"
        assert TAGS[tag["name"]]


def test_every_used_tag_is_declared_with_a_description():
    used = {tag for _, _, operation in _operations() for tag in operation.get("tags", [])}
    declared = {tag["name"]: tag for tag in schema.get("tags", [])}
    for name in used:
        assert name in declared, f"tag {name!r} is missing from the schema"
        assert declared[name].get("description"), f"tag {name!r} has no description"


def test_no_empty_tag_section_is_published():
    """A declared tag with no operation renders as an empty heading.

    Swagger UI and ReDoc both seed a section for *every* top-level tag
    in the document, whether or not an operation carries it. A tag with
    no operations therefore shows up as a heading with nothing under it,
    which reads as a broken document.
    """
    used = {tag for _, _, operation in _operations() for tag in operation.get("tags", [])}
    declared = {tag["name"] for tag in schema.get("tags", [])}
    assert declared - used == set(), f"tags without operations: {sorted(declared - used)}"


def test_each_router_has_its_own_tag():
    used = {tag for _, _, operation in _operations() for tag in operation.get("tags", [])}
    assert used == EXPECTED_TAGS


def test_frontend_html_route_is_excluded_from_the_schema():
    assert "/api/v1/{theme_name}/{page_name}" not in schema["paths"]


def test_healthz_is_documented_under_the_system_tag():
    operation = schema["paths"]["/healthz"]["get"]
    assert operation["tags"] == ["system"]
    assert operation["operationId"] == "health_check"


def test_factory_app_exposes_scalar_docs():
    """Every app produced by the factory exposes the API reference."""
    fresh_app = create_app()
    with TestClient(fresh_app) as fresh_client:
        response = fresh_client.get("/docs")
    assert response.status_code == 200
    assert "Scalar" in response.text


def test_scalar_docs_precede_spa_mount():
    """The SPA catch-all must not shadow the Scalar reference route."""
    with TemporaryDirectory() as temp_dir:
        dist = Path(temp_dir)
        (dist / "index.html").write_text("<!doctype html><title>SPA</title>", encoding="utf-8")
        with patch("backend.app.FRONTEND_DIST", dist):
            fresh_app = create_app()
            with TestClient(fresh_app) as fresh_client:
                docs_response = fresh_client.get("/docs")
                spa_response = fresh_client.get("/")
    assert docs_response.status_code == 200
    assert "Scalar" in docs_response.text
    assert spa_response.status_code == 200
    assert "<title>SPA</title>" in spa_response.text


def test_description_names_scalar():
    """The published description must name the UI that actually answers."""
    description = schema["info"]["description"]
    assert "Scalar" in description
    assert "/docs" in description


def test_description_has_no_redoc():
    """`/redoc` is disabled, so the description must not advertise it."""
    description = schema["info"]["description"]
    assert "/redoc" not in description


def _published_tag_description(name):
    """Return the description of ``name`` as published in the schema."""
    matches = [tag["description"] for tag in schema["tags"] if tag["name"] == name]
    assert matches, f"tag {name!r} is not published in the schema"
    return matches[0]


def test_meta_tag_description_matches_the_lean_payload():
    """The meta tag must not advertise the catalogue the payload dropped.

    ``/api/v1/meta`` now returns ``brand``, ``version`` and ``formats``
    only. The published tag prose still promised engines, categories and
    locales, so an API client reading the document was told to build its
    pickers from a response that no longer carries them.
    """
    description = _published_tag_description("meta")
    for field in ("brand", "version", "formats"):
        assert field in description, f"meta tag description omits the published field {field!r}: {description!r}"
    for absent in ("engine", "categor", "locale"):
        assert absent not in description.lower(), f"meta tag description still advertises {absent!r}: {description!r}"


def test_engines_tag_description_matches_the_catalogue_payload():
    """The engines tag must describe the fields the payload actually carries.

    The tag prose advertised a singular ``category`` attribute (removed in
    favour of full ``categories`` membership) and said nothing about
    ``default_enabled`` or ``capabilities``, disagreeing with both the
    endpoint description and the response model.
    """
    description = _published_tag_description("engines")
    for field in ("name", "url", "description", "shortcut", "categories", "default_enabled", "capabilities"):
        assert field in description, f"engines tag description omits {field!r}: {description!r}"
    # The only legitimate use of the singular word is the `category` query
    # parameter; anywhere else it advertises the removed `category` field.
    bare = description.replace("`category`", "")
    if "category" in bare:
        raise AssertionError(f"engines tag description advertises the removed 'category' field: {description!r}")


def test_preferences_descriptions_describe_the_new_catalogues():
    """Every preferences description must name the fields the payload carries.

    ``/api/v1/preferences`` owns three catalogues now: the per-setting
    descriptors (``settings``), the effective engine pair states
    (``engine_states``) and the plugin metadata/state map
    (``plugin_states``).  All three GET/POST descriptions and the tag
    prose have to advertise them.
    """
    operations = schema["paths"]["/api/v1/preferences"]
    texts = (
        operations["get"]["description"],
        operations["post"]["description"],
        _published_tag_description("preferences"),
    )

    for text in texts:
        for field in ("settings", "engine_states", "plugin_states"):
            assert field in text, f"preferences description omits {field!r}: {text!r}"


def test_preferences_descriptions_do_not_advertise_removed_catalogue_fields():
    """The prose must not promise a top-level field the payload dropped."""
    operations = schema["paths"]["/api/v1/preferences"]
    texts = (
        operations["get"]["description"],
        operations["post"]["description"],
        _published_tag_description("preferences"),
    )

    removed = ("engine_pairs", "`categories`", "`engines`", "`plugins`", "`locales`", "`formats`", "`styles`")
    for text in texts:
        for field in removed:
            assert field not in text, f"preferences description still advertises {field!r}: {text!r}"


# --- the POST request body schema -----------------------------------------
#
# The body stays a lenient, flat document at runtime, but the published
# schema must still tell a code generator which keys exist and what JSON
# values they carry.  FastAPI's generic ``Body`` schema (``{"title":
# "Body"}``) documents none of that, so the POST route publishes an
# explicit hand-authored schema instead.

#: Document keys whose value is a list of identifiers.
PREFERENCE_ARRAY_KEYS = {
    "categories",
    "disabled_engines",
    "enabled_engines",
    "disabled_plugins",
    "enabled_plugins",
}

#: Document keys whose value is a JSON boolean.
PREFERENCE_BOOLEAN_KEYS = {
    "image_proxy",
    "results_on_new_tab",
    "center_alignment",
    "query_in_title",
    "search_on_category_select",
}

#: Document keys whose value is a free-form JSON string.
PREFERENCE_STRING_KEYS = {
    "language",
    "locale",
    "autocomplete",
    "favicon_resolver",
    "method",
    "theme",
    "doi_resolver",
    "simple_style",
    "hotkeys",
    "url_formatting",
    "default_category",
}


def _post_request_schema():
    """Return the published ``application/json`` schema of the POST body."""
    request_body = schema["paths"]["/api/v1/preferences"]["post"]["requestBody"]
    return request_body["content"]["application/json"]["schema"]


def test_post_preferences_request_body_is_an_object_that_allows_extra_keys():
    """The body is a flat JSON object; unknown keys stay permitted.

    ``additionalProperties`` must stay open: the runtime skips a key it
    cannot apply rather than rejecting the request, so the schema must not
    tell a validating client that an unknown key is a hard error.
    """
    body = _post_request_schema()
    assert body["type"] == "object"
    assert body.get("additionalProperties") is True


def test_post_preferences_request_schema_documents_every_writable_key():
    """Every key the runtime accepts has to appear in the published schema.

    The schema is hand-authored, so it can drift from
    ``Preferences.key_value_settings``.  Pinning the property set to the
    runtime settings (plus the four list keys and ``default_category``)
    makes a newly added setting fail here instead of silently missing
    from the API reference.
    """
    properties = _post_request_schema()["properties"]
    runtime_keys = set(build_preferences().key_value_settings)

    expected = runtime_keys | PREFERENCE_ARRAY_KEYS | PREFERENCE_BOOLEAN_KEYS | PREFERENCE_STRING_KEYS | {"safesearch"}
    assert set(properties) == expected
    assert runtime_keys <= set(properties)


def test_post_preferences_request_schema_types_match_the_runtime_settings():
    """Each documented type must be the JSON type the setting really holds.

    A wrong type here is worse than no schema at all: a generated client
    would serialize the value the server then rejects.
    """
    properties = _post_request_schema()["properties"]
    for name, setting in build_preferences().key_value_settings.items():
        assert properties[name]["type"] == _setting_type(setting), name

    for name in PREFERENCE_ARRAY_KEYS:
        assert properties[name] == {"type": "array", "items": {"type": "string"}}, name
    for name in PREFERENCE_BOOLEAN_KEYS:
        assert properties[name] == {"type": "boolean"}, name
    for name in PREFERENCE_STRING_KEYS:
        assert properties[name] == {"type": "string"}, name


def test_post_preferences_request_schema_bounds_safesearch():
    """Safesearch is the one ranged integer: 0 (off) to 2 (strict)."""
    assert _post_request_schema()["properties"]["safesearch"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 2,
    }


def test_post_preferences_request_schema_does_not_advertise_tokens():
    """``tokens`` is a hard rejection, so it must not be documented.

    Listing it as a property would invite clients to send the one key the
    endpoint refuses with a 400.
    """
    assert "tokens" not in _post_request_schema()["properties"]
