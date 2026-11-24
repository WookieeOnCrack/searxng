# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name
# pylint: disable=protected-access,too-few-public-methods
"""Tests for the opt-in rate limiting middleware.

The middleware is registered by :py:func:`backend.app.create_app` only when
``server.rate_limit.enabled`` is true.  These tests pin the wiring (the
middleware is opt-in, CORS stays the outermost layer), the client IP
resolution behind a reverse proxy and the behaviour of the limiter itself.
"""

from __future__ import annotations

import types
from contextlib import contextmanager
from typing import cast
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.middleware import ratelimiter
from backend.middleware.ratelimiter import RateLimiter
from searx import settings

# --------------------------------------------------------------------------
# test doubles
# --------------------------------------------------------------------------


class FakePipeline:
    """Minimal stand-in for the Valkey pipeline used by the middleware."""

    def __init__(self, store):
        self.store = store
        self.commands = []

    def incr(self, key):
        self.commands.append(("incr", key))
        return self

    def expire(self, key, ttl, nx=False):
        self.commands.append(("expire", key, ttl, nx))
        return self

    async def execute(self):
        results = []
        for cmd in self.commands:
            if cmd[0] == "incr":
                self.store[cmd[1]] = self.store.get(cmd[1], 0) + 1
                results.append(self.store[cmd[1]])
            else:
                results.append(True)
        return results


class FakeValkey:
    """In-memory stand-in for the async Valkey client."""

    def __init__(self):
        self.store = {}

    def pipeline(self):
        return FakePipeline(self.store)


@contextmanager
def fake_valkey():
    client = FakeValkey()
    with patch.object(ratelimiter.valkey, "from_url", return_value=client):
        yield client


@contextmanager
def rate_limit_settings(
    *,
    enabled=True,
    requests_per_minute=2,
    paths=None,
    trusted_proxies=None,
    secret: str | None = "a-strong-secret",
    valkey_url: str | None = "valkey://localhost:6379",
    server_secret_key: str | None = "a-strong-secret",
):
    """Temporarily override the rate limiter settings."""
    server = settings.setdefault("server", {})
    valkey_settings = settings.setdefault("valkey", {})

    old_rate_limit = server.get("rate_limit")
    old_valkey_url = valkey_settings.get("url")
    old_secret_key = server.get("secret_key")

    server["secret_key"] = server_secret_key
    server["rate_limit"] = {
        "enabled": enabled,
        "requests_per_minute": requests_per_minute,
        "paths": ["/api/v1/test-only"] if paths is None else paths,
        "trusted_proxies": ["127.0.0.0/8", "::1"] if trusted_proxies is None else trusted_proxies,
        "secret": secret,
    }
    valkey_settings["url"] = valkey_url
    try:
        yield
    finally:
        if old_rate_limit is None:
            server.pop("rate_limit", None)
        else:
            server["rate_limit"] = old_rate_limit
        valkey_settings["url"] = old_valkey_url
        server["secret_key"] = old_secret_key


def _request(peer, headers=None):
    """Build a request stub with the attributes the middleware reads."""
    stub = types.SimpleNamespace(
        client=types.SimpleNamespace(host=peer),
        headers=headers or {},
    )
    return cast(Request, cast(object, stub))


def _middleware(**kwargs):
    config = ratelimiter.RateLimitConfig(
        enabled=kwargs.pop("enabled", True),
        requests_per_minute=kwargs.pop("requests_per_minute", 2),
        paths=tuple(kwargs.pop("paths", ("/api/v1/search",))),
        trusted_proxies=tuple(kwargs.pop("trusted_proxies", ("127.0.0.0/8", "::1"))),
        secret=kwargs.pop("secret", "a-strong-secret"),
    )
    return RateLimiter(FastAPI(), config=config, **kwargs)


# --------------------------------------------------------------------------
# wiring: the limiter is opt-in and CORS stays the outermost layer
# --------------------------------------------------------------------------


def test_middleware_is_not_registered_by_default():
    with rate_limit_settings(enabled=False):
        app = create_app()
    assert all(middleware.cls is not RateLimiter for middleware in app.user_middleware)


def test_middleware_is_registered_when_enabled():
    with rate_limit_settings(enabled=True):
        app = create_app()
    assert any(middleware.cls is RateLimiter for middleware in app.user_middleware)


def test_cors_is_the_outermost_layer():
    """CORS has to wrap the limiter.

    Otherwise preflight requests are counted against the quota and a 429
    response is handed to the browser without the CORS headers.
    """
    with rate_limit_settings(enabled=True):
        app = create_app()

    classes = [getattr(middleware.cls, "__name__", "") for middleware in app.user_middleware]
    assert "RateLimiter" in classes
    assert "CORSMiddleware" in classes
    assert classes.index("CORSMiddleware") < classes.index("RateLimiter")


def test_preflight_requests_do_not_consume_the_quota():
    with rate_limit_settings(enabled=True, requests_per_minute=1), fake_valkey():
        app = create_app()

        @app.get("/api/v1/test-only")
        def _test_only():
            return {"ok": True}

        client = TestClient(app)
        for _ in range(5):
            res = client.options(
                "/api/v1/test-only",
                headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
            )
            assert res.status_code == 200

        res = client.get("/api/v1/test-only")
        assert res.status_code == 200


def test_rate_limited_response_carries_cors_headers():
    with rate_limit_settings(enabled=True, requests_per_minute=1), fake_valkey():
        app = create_app()

        @app.get("/api/v1/test-only")
        def _test_only():
            return {"ok": True}

        client = TestClient(app)
        assert client.get("/api/v1/test-only").status_code == 200

        res = client.get("/api/v1/test-only", headers={"Origin": "http://localhost:5173"})
        assert res.status_code == 429
        assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


# --------------------------------------------------------------------------
# configuration: broken configurations must not break the instance
# --------------------------------------------------------------------------


def test_no_valkey_url_does_not_break_the_instance():
    with rate_limit_settings(enabled=True, valkey_url=None), fake_valkey():
        app = create_app()

        @app.get("/api/v1/test-only")
        def _test_only():
            return {"ok": True}

        client = TestClient(app)
        assert client.get("/api/v1/test-only").status_code == 200


def test_no_secret_does_not_break_the_instance():
    with rate_limit_settings(enabled=True, secret=None, server_secret_key=None), fake_valkey():
        app = create_app()

        @app.get("/api/v1/test-only")
        def _test_only():
            return {"ok": True}

        client = TestClient(app)
        assert client.get("/api/v1/test-only").status_code == 200


# --------------------------------------------------------------------------
# client IP resolution
# --------------------------------------------------------------------------


def test_ipv4_mapped_ipv6_peer_is_trusted():
    """A peer reported as ``::ffff:127.0.0.1`` is a loopback peer."""
    limiter = _middleware(trusted_proxies=("127.0.0.0/8",))
    request = _request("::ffff:127.0.0.1", {"X-Forwarded-For": "203.0.113.7"})
    assert limiter._get_client_ip(request) == "203.0.113.7"


def test_untrusted_peer_cannot_spoof_the_client_ip():
    limiter = _middleware(trusted_proxies=("127.0.0.0/8",))
    request = _request("198.51.100.9", {"X-Forwarded-For": "203.0.113.7"})
    assert limiter._get_client_ip(request) == "198.51.100.9"


def test_trusted_proxies_are_configurable():
    limiter = _middleware(trusted_proxies=("10.0.0.0/8",))
    request = _request("10.1.2.3", {"X-Forwarded-For": "203.0.113.7"})
    assert limiter._get_client_ip(request) == "203.0.113.7"


def test_rightmost_untrusted_address_wins():
    limiter = _middleware(trusted_proxies=("10.0.0.0/8",))
    request = _request("10.1.2.3", {"X-Forwarded-For": "203.0.113.7, 10.1.2.4"})
    assert limiter._get_client_ip(request) == "203.0.113.7"


def test_client_ip_without_forwarded_header():
    limiter = _middleware()
    request = _request("127.0.0.1")
    assert limiter._get_client_ip(request) == "127.0.0.1"


# --------------------------------------------------------------------------
# the limiter itself
# --------------------------------------------------------------------------


def test_requests_over_the_limit_are_rejected():
    with fake_valkey():
        app = FastAPI()
        app.add_middleware(
            RateLimiter,
            config=ratelimiter.RateLimitConfig(
                enabled=True, requests_per_minute=2, paths=("/api/v1/search",), secret="a-strong-secret"
            ),
            valkey_url="valkey://localhost:6379",
        )

        @app.get("/api/v1/search")
        def _search():
            return {"ok": True}

        client = TestClient(app)
        assert client.get("/api/v1/search").status_code == 200
        assert client.get("/api/v1/search").status_code == 200

        res = client.get("/api/v1/search")
        assert res.status_code == 429
        assert res.headers["Retry-After"] == "60"
        assert res.json()["detail"]


def test_other_paths_are_not_rate_limited():
    with fake_valkey():
        app = FastAPI()
        app.add_middleware(
            RateLimiter,
            config=ratelimiter.RateLimitConfig(
                enabled=True, requests_per_minute=1, paths=("/api/v1/search",), secret="a-strong-secret"
            ),
            valkey_url="valkey://localhost:6379",
        )

        @app.get("/other")
        def _other():
            return {"ok": True}

        client = TestClient(app)
        for _ in range(5):
            assert client.get("/other").status_code == 200
