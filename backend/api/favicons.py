# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/favicons — favicon proxy.

Wraps the kernel's favicon proxy (search_favicon + HMAC auth) and returns
image bytes with the correct Content-Type.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from searx import settings
from searx.favicons import proxy
from searx.favicons.proxy import search_favicon
from searx.webutils import is_hmac_of

router = APIRouter(tags=["favicons"])


@router.get(
    "/favicons",
    response_class=Response,
    summary="Fetch a favicon",
    description=(
        "Return the favicon of a domain as image bytes, fetched through the "
        "instance's favicon proxy.\n\n"
        "`authority` is the bare domain (an RFC 3986 authority); `h` is an "
        "optional HMAC of the authority signed with the instance secret key, "
        "which is verified when supplied. When the domain has no favicon, a "
        "transparent 1x1 PNG placeholder is returned."
    ),
    operation_id="get_favicon",
)
def favicon(
    authority: str = Query(..., description="RFC 3986 authority (domain)"),
    h: str = Query("", description="HMAC of authority (optional for API use)"),
    format: str = Query("png", description="Favicon format"),  # never used??
    size: int = Query(64, description="Favicon size in pixels"),  # never used??
) -> Response:
    """Return the favicon for the given authority as image bytes.

    If an HMAC is provided, it is verified against the server secret key
    (mirroring the Flask favicon_proxy route).
    """
    if not authority or "/" in authority:
        raise HTTPException(status_code=400, detail="Malformed authority")

    # Verify HMAC if provided.
    if h and not is_hmac_of(settings["server"]["secret_key"], authority.encode(), h):
        raise HTTPException(status_code=400, detail="Invalid HMAC")

    # Determine the resolver (from settings, default to first available).
    resolver = settings.get("search", {}).get("favicon_resolver")
    if not resolver and proxy.CFG.resolver_map:
        # Fall back to the first available resolver.
        resolver = next(iter(proxy.CFG.resolver_map.keys()), None)

    data, mime = search_favicon(resolver, authority) if resolver else (None, None)

    if data is not None and mime is not None:
        return Response(
            content=data,
            media_type=mime,
            headers={"Cache-Control": f"max-age={proxy.CFG.max_age}"},
        )

    # No favicon found; return a transparent 1x1 PNG placeholder.
    transparent_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\x06\x00\x00\x00\x00PNG\x00\x00\x00\x0cIDATX"
        b"\x9c\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    )
    return Response(
        content=transparent_png,
        media_type="image/png",
        headers={"Cache-Control": f"max-age={proxy.CFG.max_age}"},
    )
