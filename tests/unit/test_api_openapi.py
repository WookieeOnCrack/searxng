# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the OpenAPI schema metadata.

The schema is the machine-readable contract for API-only consumers (AI
agents, SDK generators, the Swagger UI). It therefore has to carry more
than the bare path and response model: every operation needs a tag, a
human-readable summary, a description, and a stable operation id, and the
document has to advertise the version of the running instance.
"""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

from fastapi.testclient import TestClient

from backend.app import app
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


def test_preferences_description_matches_the_response_model():
    """The endpoint description must not advertise a field the model dropped.

    ``PreferencesResponse.languages`` was removed as a duplicate of
    ``locales``. The prose in the endpoint description and in the tag
    catalogue still named it, so the published OpenAPI document promised
    a field the payload never carried.
    """
    operation = schema["paths"]["/api/v1/preferences"]["get"]
    description = operation["description"]
    tag_description = next(t["description"] for t in schema["tags"] if t["name"] == "preferences")

    for text in (description, tag_description):
        assert "languages" not in text, f"description still advertises the removed 'languages' field: {text!r}"
