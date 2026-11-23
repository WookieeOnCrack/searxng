# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/images — image proxy.

Streams images through the kernel's network layer (curl_cffi), mirroring
the Flask image_proxy route.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from searx import settings
from searx.network import stream as http_stream
from searx.utils import gen_useragent
from searx.webutils import is_hmac_of

router = APIRouter(tags=["images"])


@router.get(
    "/images",
    response_class=Response,
    summary="Fetch an image through the proxy",
    description=(
        "Stream a remote image through the instance's network layer, so the "
        "client never talks to the image host directly.\n\n"
        "`h` is an optional HMAC of the URL signed with the instance secret "
        "key, which is verified when supplied. The response carries the "
        "upstream content type; images larger than 5 MB are rejected."
    ),
    operation_id="get_image_proxy",
)
async def image_proxy(  # pylint: disable=invalid-name
    url: Annotated[str, Query(description="Image URL to proxy")],
    h: Annotated[str, Query(description="HMAC of URL (optional for API use)")] = "",
) -> Response:
    """Stream the image at the given URL through the kernel's network layer.

    Mirrors the Flask image_proxy route: verifies HMAC, streams the response,
    and returns image bytes with the original Content-Type.
    """
    return await run_in_threadpool(_fetch_image, url, h)


def _fetch_image(url: str, hmac_value: str) -> Response:
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")

    # Verify HMAC if provided.
    if hmac_value and not is_hmac_of(settings["server"]["secret_key"], url.encode(), hmac_value):
        raise HTTPException(status_code=400, detail="Invalid HMAC")

    maximum_size = 5 * 1024 * 1024  # 5MB cap

    try:
        request_headers = {
            "User-Agent": gen_useragent(),
            "Accept": "image/webp,*/*",
            "Sec-GPC": "1",
            "DNT": "1",
        }
        resp, stream = http_stream(method="GET", url=url, headers=request_headers, allow_redirects=True)
        content_length = resp.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > maximum_size:
            raise HTTPException(status_code=400, detail="Max size exceeded")

        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Upstream returned {resp.status_code}")

        content_type = resp.headers.get("Content-Type", "")
        if not content_type.startswith("image/") and not content_type.startswith("binary/octet-stream"):
            raise HTTPException(status_code=400, detail=f"Not an image: {content_type}")

        # Read the streamed content.
        content = b"".join(stream)
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "Content-Length": str(len(content)),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Image proxy error: {e}") from e
