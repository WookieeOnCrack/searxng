# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=too-few-public-methods
"""A simple rate limiter middleware using Valkey"""

import hashlib
import ipaddress
from collections.abc import Sequence
from datetime import date

import valkey.asyncio as valkey
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from searx import logger, settings

logger = logger.getChild("rate-limit")


class RateLimiter(BaseHTTPMiddleware):
    """Simple rate limiter using Valkey"""

    def __init__(
        self,
        app,
        *,
        requests_per_minute: int = 30,
        path: str = "/api/v1/search",
        trusted_proxies: Sequence[str] | None = None,
        secret_salt: str | None = None,
        valkey_url: str | None = None,
    ):
        super().__init__(app)
        self.rate_limit = requests_per_minute
        self.path = path
        self.secret_salt = (secret_salt or settings["server"]["secret_key"]).encode()
        self.trusted_networks = [ipaddress.ip_network(p) for p in (trusted_proxies or ["127.0.0.0/8", "::1"])]
        if valkey_url:
            self.valkey = valkey.from_url(valkey_url)
        elif not isinstance(settings.get("valkey", {}).get("url"), bool):
            self.valkey = valkey.from_url(
                settings.get("valkey", {}).get("url", "valkey://valkey:6379"),
                decode_responses=True,
            )
        else:
            self.valkey = None

    def _get_client_ip(self, request: Request) -> str:
        # Direct peer
        peer = request.client.host if request.client else "0.0.0.0"
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            return "0.0.0.0"

        # Only trust X-Forwarded-For if the direct peer is a trusted proxy
        if not any(peer_ip in net for net in self.trusted_networks):
            return peer

        xff = request.headers.get("X-Forwarded-For")
        if not xff:
            return peer

        # Take the rightmost address that is *not* in a trusted network
        # (common when the last proxy appends)
        for part in reversed([p.strip() for p in xff.split(",")]):
            try:
                ip = ipaddress.ip_address(part)
                if not any(ip in net for net in self.trusted_networks):
                    return str(ip)
            except ValueError:
                continue
        return peer

    def _privacy_key(self, client_ip: str) -> str:
        """
        Hashes the client IP with a daily rotating salt.
        """
        day = date.today().isoformat()
        # Keep the salt parameter ≤ 16 bytes for blake2b
        h = hashlib.blake2b(
            f"{client_ip}|{day}".encode(),
            salt=self.secret_salt[:16],
            digest_size=32,
        )
        return f"rl:search:{h.hexdigest()}"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.valkey:
            return await call_next(request)
        # Only rate limit search routes to keep asset loading fast
        if request.url.path != self.path:
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        key = self._privacy_key(client_ip)

        try:
            # Fixed-window atomic execution in Valkey
            # Increment the counter and set a 60s TTL if it's a new window
            pipe = self.valkey.pipeline()
            pipe.incr(key)
            pipe.expire(key, 60, nx=True)  # nx=True requires modern Redis/Valkey
            count, _ = await pipe.execute()

            if count > self.rate_limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please slow down."},
                    headers={"Retry-After": "60"},
                )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Valkey rate limiter error: %s", e)
            # fail open

        return await call_next(request)
