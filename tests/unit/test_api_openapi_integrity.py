# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the integrity of the OpenAPI document.

A schema that does not resolve is worse than a schema that carries less
information: a consumer (or a code generator) that follows a dangling
``$ref`` gets an error instead of a type. Every ``$ref`` in the document
must point at a schema that actually exists in ``components/schemas``.
"""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

import re

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)

schema = app.openapi()

REF_PATTERN = re.compile(r'"\$ref"\s*:\s*"#/components/schemas/([^"]+)"')


def _refs(node, found=None):
    """Collect every ``$ref`` target in a JSON-ish node."""
    if found is None:
        found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.add(value)
            else:
                _refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _refs(item, found)
    return found


def test_every_ref_resolves_to_an_existing_component_schema():
    components = schema.get("components", {}).get("schemas", {})
    missing = set()
    for ref in _refs(schema):
        name = ref.rsplit("/", 1)[-1]
        if name not in components:
            missing.add(ref)
    assert not missing, f"dangling $ref(s) in the OpenAPI document: {sorted(missing)}"


def test_search_response_schemas_are_published():
    """The two shapes the search endpoint can return must be in the schema."""
    components = schema["components"]["schemas"]
    assert "SearchResponse" in components
    assert "CompatSearchResponse" in components


def test_search_response_documents_both_shapes():
    operation = schema["paths"]["/api/v1/search"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    refs = {ref.rsplit("/", 1)[-1] for ref in _refs(response_schema)}
    assert refs == {"SearchResponse", "CompatSearchResponse"}
    # `anyOf`, not `oneOf`: a SearchResponse body also satisfies the
    # CompatSearchResponse schema (the extra keys are ignored), so
    # "exactly one of these" would be false.
    assert "oneOf" not in response_schema
    assert len(response_schema["anyOf"]) == 2


def test_search_documents_the_400_response():
    """The endpoint raises HTTPException(400) for invalid parameters."""
    operation = schema["paths"]["/api/v1/search"]["get"]
    assert "400" in operation["responses"]
