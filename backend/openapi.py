# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAPI metadata for the SearXNG API.

The OpenAPI document is the machine-readable contract of the API: API-only
consumers (AI agents, SDK generators, the Swagger UI) read it instead of
the source code. It therefore has to be *written*, not merely generated --
every operation needs a tag, a human-readable summary, a description that
says what the endpoint actually returns, and a stable operation id.

Two rules govern this module:

* **One tag per router**, 1:1 with ``backend/api/*.py``. A tag is declared
  on the router itself (``APIRouter(tags=[...])``), so reading a router
  module tells you its tag, and a new router gets its own tag by
  convention rather than being folded into an unrelated group.
* **Operation ids are stable and explicit.** FastAPI's fallback ids embed
  the path and the HTTP method (``search_api_v1_search_get``), which
  changes when a route moves and is useless to a code generator.

The version in ``info.version`` is taken from :mod:`searx.version` so the
published schema always describes the running instance.
"""

from __future__ import annotations

from typing import Any

#: The tag catalogue: tag name -> description. The description is what
#: Swagger UI shows next to the tag heading, so it is written for a human
#: reading the docs, not for a machine.
TAGS: dict[str, str] = {
    "meta": (
        "Static metadata about this SearXNG instance: the registered engines, "
        "the available categories, the supported locales, the brand configuration "
        "and the instance version. An API client typically fetches this once at "
        "startup to build its engine, category and language pickers."
    ),
    "search": (
        "Run a search query across the enabled engines and return the aggregated "
        "results. This is the core endpoint of the API. The default response is "
        "the current JSON contract; `?format=compat` returns the legacy "
        "`?format=json` shape of the Flask webapp for existing consumers."
    ),
    "preferences": (
        "The preference catalogue of this instance: the selectable categories, "
        "engines, locales and output formats, together with the "
        "default and current values and the list of preferences locked by the "
        "instance operator."
    ),
    "autocomplete": (
        "Completion suggestions for a partially typed query, produced by the "
        "instance's configured autocomplete backend and its answerers."
    ),
    "engines": (
        "The search engines registered on this instance, with their website, "
        "description, category and keyboard shortcut. Pass `category` to "
        "restrict the list to a single category."
    ),
    "stats": (
        "Runtime metrics of the search engines: how many results each engine "
        "returned, how often it errored, its score per result and its response "
        "time, plus the aggregated totals. The numbers come from the instance's "
        "own metrics and reset when the instance restarts."
    ),
    "favicons": (
        "Favicon proxy: fetch the favicon of a domain through the instance. "
        "`authority` is the bare domain (an RFC 3986 authority) and `h` is an "
        "optional HMAC of the authority signed with the instance secret key. "
        "Returns the image bytes, or a transparent 1x1 PNG placeholder when the "
        "domain has no favicon."
    ),
    "images": (
        "Image proxy: fetch a remote image through the instance's network layer, "
        "so the client never talks to the image host directly. `h` is an optional "
        "HMAC of the URL signed with the instance secret key. Returns the image "
        "bytes with the upstream content type."
    ),
    "frontend": (
        "The server-rendered (no-JS) HTML frontend of this instance, rendered "
        "from the Jinja2 templates of the selected theme. Not part of the JSON "
        "API contract."
    ),
    "system": (
        "Deployment-level endpoints of the instance itself, such as the health "
        "check used by orchestrators and load balancers."
    ),
}

#: The OpenAPI ``info.description`` -- the introduction shown above the
#: endpoint list in Swagger UI.
API_DESCRIPTION = (
    "The HTTP API of a SearXNG instance: a privacy-respecting metasearch "
    "engine that queries multiple search providers and returns their results "
    "aggregated as JSON.\n\n"
    "All endpoints live under `/api/v1` and return JSON unless stated "
    "otherwise. This document is the machine-readable contract of the API; "
    "the interactive documentation is served at `/docs` (Swagger UI) and "
    "`/redoc`, and the raw schema at `/openapi.json`."
)


def openapi_tags() -> list[dict[str, str]]:
    """Return the tag catalogue in the shape ``FastAPI(openapi_tags=...)`` wants."""
    return [{"name": name, "description": description} for name, description in TAGS.items()]


def prune_unused_tags(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop the catalogue entries of tags that no operation carries.

    Swagger UI and ReDoc both seed a section for *every* tag declared at
    the top level of the document, whether or not an operation uses it. A
    tag without operations therefore renders as a heading with nothing
    under it, which reads as a broken document.

    The ``frontend`` tag is the case in point: the Jinja bridge is a
    router like any other, but its only route serves HTML and is excluded
    from the schema, so it has no operation to show.
    """
    used = {
        tag
        for path_item in schema.get("paths", {}).values()
        for operation in path_item.values()
        if isinstance(operation, dict)
        for tag in operation.get("tags", [])
    }
    if "tags" in schema:
        schema["tags"] = [tag for tag in schema["tags"] if tag.get("name") in used]
    return schema
