# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=too-few-public-methods
"""Rate limiter middleware of the FastAPI backend.

The limiter counts the requests of a client in a fixed time window and
rejects the requests above the limit with a ``429``.  The counters live in a
Valkey DB (:ref:`settings valkey`).

The limiter is **opt-in**: it is only registered when
``server.rate_limit.enabled`` is ``true`` (see
:py:func:`backend.app.create_app`).  Instances that do not enable it do not
pay the cost of the middleware at all.

Privacy: only a hash of the client IP is stored in the Valkey DB, never the
IP itself.  The hash is salted with a secret (``server.rate_limit.secret``,
defaults to ``server.secret_key``) and rotated daily.
"""

from __future__ import annotations

import hashlib
import ipaddress
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import valkey.asyncio as valkey
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from searx import get_setting, logger

logger = logger.getChild("rate-limit")

#: Length of the counting window in seconds.
WINDOW_SECONDS = 60

#: Fallback used when the IP of the client can not be determined.
NO_CLIENT_IP = "0.0.0.0"


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration of the rate limiter.

    The values are read from the ``server.rate_limit`` section of the
    settings (see :py:func:`load_config`).
    """

    enabled: bool = False
    requests_per_minute: int = 30
    paths: tuple[str, ...] = ("/api/v1/search",)
    trusted_proxies: tuple[str, ...] = ()
    secret: str | None = None
    window_seconds: int = WINDOW_SECONDS


def _as_list(value: object) -> list[str]:
    """Normalize a setting that may be a list or a comma separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_int(value: object, default: int) -> int:
    """Normalize a setting that may be an int or a string."""
    if isinstance(value, bool):
        return default
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def load_config() -> RateLimitConfig:
    """Read the rate limiter configuration from the settings."""
    secret = get_setting("server.rate_limit.secret", None) or get_setting("server.secret_key", None)
    return RateLimitConfig(
        enabled=bool(get_setting("server.rate_limit.enabled", False)),
        requests_per_minute=_as_int(get_setting("server.rate_limit.requests_per_minute", 30), 30),
        paths=tuple(_as_list(get_setting("server.rate_limit.paths", ("/api/v1/search",)))),
        trusted_proxies=tuple(_as_list(get_setting("server.rate_limit.trusted_proxies", ()))),
        secret=secret if isinstance(secret, str) and secret else None,
    )


def _normalize_path(path: str) -> str:
    """Normalize a path for the comparison: a trailing slash is ignored."""
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an IP address, IPv4-mapped IPv6 addresses are unwrapped.

    A client of an IPv4 socket connected to a dual stack server is reported
    as ``::ffff:127.0.0.1`` -- without unwrapping, such an address would not
    match the ``127.0.0.0/8`` network.
    """
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _trusted_networks(networks: Iterable[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    result = []
    for network in networks:
        try:
            result.append(ipaddress.ip_network(network, strict=False))
        except ValueError:
            logger.error("invalid trusted proxy network: %s", network)
    return tuple(result)


class RateLimiter(BaseHTTPMiddleware):
    """Rate limit the requests of a client, backed by a Valkey DB.

    The middleware is a no-op when the Valkey DB is not configured.
    """

    def __init__(
        self,
        app,
        *,
        config: RateLimitConfig | None = None,
        valkey_url: str | None = None,
    ):
        super().__init__(app)
        self.config: RateLimitConfig = config if config is not None else load_config()
        self.rate_limit: int = self.config.requests_per_minute
        self.paths: frozenset[str] = frozenset(_normalize_path(path) for path in self.config.paths)
        self.trusted_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = _trusted_networks(
            self.config.trusted_proxies
        )
        self.secret: str = self.config.secret or ""
        self.window_seconds: int = self.config.window_seconds
        self.valkey: valkey.Valkey | None = self._create_client(valkey_url)
        self._last_error_log: float = 0.0

    def _create_client(self, valkey_url: str | None) -> valkey.Valkey | None:
        """Create the Valkey client, ``None`` if no DB is configured."""
        if valkey_url is None:
            url = get_setting("valkey.url", None)
        else:
            url = valkey_url

        if not isinstance(url, str) or not url.strip():
            logger.info("no Valkey DB configured -- the rate limiter is disabled")
            return None

        if not self.secret:
            logger.error(
                "no secret configured (server.rate_limit.secret / server.secret_key) -- the rate limiter is disabled"
            )
            return None

        return valkey.from_url(url, decode_responses=True)

    def _is_trusted(self, ip_address_: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(net.version == ip_address_.version and ip_address_ in net for net in self.trusted_networks)

    def _get_client_ip(self, request: Request) -> str:
        """Determine the IP of the client.

        The ``X-Forwarded-For`` header is only evaluated when the request
        comes from a trusted proxy, otherwise a client could spoof its IP.
        """
        peer = request.client.host if request.client else None
        peer_ip = _parse_ip(peer) if peer else None
        if peer_ip is None:
            return NO_CLIENT_IP

        if not self._is_trusted(peer_ip):
            return peer_ip.compressed

        forwarded_for = request.headers.get("X-Forwarded-For")
        if not forwarded_for:
            return peer_ip.compressed

        # The rightmost address that is not in a trusted network is the
        # client (the last proxy appends the address of the client).
        for part in reversed([part.strip() for part in forwarded_for.split(",")]):
            ip = _parse_ip(part)
            if ip is not None and not self._is_trusted(ip):
                return ip.compressed

        return peer_ip.compressed

    def _privacy_key(self, client_ip: str) -> str:
        """Hashes the client IP with a daily rotating salt."""
        day = date.today().isoformat()
        # Keep the salt parameter <= 16 bytes for blake2b
        h = hashlib.blake2b(
            f"{client_ip}|{day}".encode(),
            salt=self.secret.encode()[:16],
            digest_size=32,
        )
        return f"rl:search:{h.hexdigest()}"

    def _log_error(self, exc: Exception) -> None:
        """Log an error of the Valkey DB, but do not flood the log."""
        now = time.monotonic()
        if now - self._last_error_log > 60:
            self._last_error_log = now
            logger.error("Valkey rate limiter error: %s", exc)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self.valkey is None:
            return await call_next(request)
        # Only rate limit the configured routes to keep asset loading fast
        if _normalize_path(request.url.path) not in self.paths:
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        key = self._privacy_key(client_ip)

        try:
            # Fixed-window atomic execution in Valkey
            # Increment the counter and set a TTL if it's a new window
            pipe = self.valkey.pipeline()
            pipe.incr(key)
            pipe.expire(key, self.window_seconds, nx=True)  # nx=True requires modern Redis/Valkey
            count, _ = await pipe.execute()

            if count > self.rate_limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please slow down."},
                    headers={"Retry-After": str(self.window_seconds)},
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._log_error(exc)
            # fail open

        return await call_next(request)
