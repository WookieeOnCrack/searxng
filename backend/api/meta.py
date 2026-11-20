# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/meta — static metadata served to the SPA at boot."""

from __future__ import annotations

from operator import attrgetter

import msgspec
from fastapi import APIRouter

from backend.models.api import EngineMeta, MetaResponse
from searx import settings
from searx.engines import categories, engines
from searx.locales import LOCALE_NAMES, RTL_LOCALES
from searx.version import VERSION_STRING

router = APIRouter(tags=["meta"])


@router.get(
    "/meta",
    response_model=MetaResponse,
    summary="Get instance metadata",
    description=(
        "Return the static metadata of this SearXNG instance: the registered "
        "engines, the available categories, the supported locales, the brand "
        "configuration, the instance version and the output formats.\n\n"
        "The response never changes between calls, so a client normally fetches "
        "it once at startup to build its engine, category and language pickers."
    ),
    operation_id="get_meta",
)
def get_meta() -> MetaResponse:
    brand = settings.get("brand", {})
    if not isinstance(brand, dict):
        # settings["brand"] is a SettingsBrand (msgspec.Struct); convert to a
        # plain dict so Pydantic's dict-typed field accepts it.
        brand = msgspec.to_builtins(brand)
    return MetaResponse(
        engines=sorted(
            [EngineMeta.from_engine(e) for e in filter(lambda x: x.inactive is False, engines.values())],
            key=attrgetter("name"),
        ),
        categories=[
            {
                "name": c,
                "title": c.replace("_", " ").title(),
                "engines": [e.name for e in engines.values() if c in (e.categories or [])],
            }
            for c in categories
        ],
        locales=[
            {
                "tag": tag,
                "title": LOCALE_NAMES.get(tag, tag),
                "rtl": tag in RTL_LOCALES,
            }
            for tag in LOCALE_NAMES
        ],
        brand=brand,
        version=VERSION_STRING,
        default_theme=settings.get("ui", {}).get("default_theme", "simple"),
        formats=settings.get("search", {}).get("formats", ["html", "json", "rss"]),
        styles=["auto", "light", "dark", "black"],
    )
