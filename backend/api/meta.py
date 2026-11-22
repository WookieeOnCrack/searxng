# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/meta — instance-wide metadata served to the SPA at boot."""

from __future__ import annotations

import msgspec
from fastapi import APIRouter

from backend.models.api import MetaResponse
from searx import settings
from searx.version import VERSION_STRING

router = APIRouter(tags=["meta"])


@router.get(
    "/meta",
    response_model=MetaResponse,
    summary="Get instance metadata",
    description=(
        "Return the instance-wide metadata of this SearXNG instance: the brand "
        "configuration, the instance version and the output formats it can "
        "serve.\n\n"
        "The engine catalogue lives in `/api/v1/engines` and the preference "
        "catalogue in `/api/v1/preferences`; this endpoint duplicates neither."
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
        brand=brand,
        version=VERSION_STRING,
        formats=settings.get("search", {}).get("formats", ["html", "json", "rss"]),
    )
