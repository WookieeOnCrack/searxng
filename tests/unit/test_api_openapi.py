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

from backend.app import app, create_app
from backend.openapi import TAGS
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
