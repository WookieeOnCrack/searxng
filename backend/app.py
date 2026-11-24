# SPDX-License-Identifier: AGPL-3.0-or-later
"""FastAPI application factory for the SearXNG backend.

Wraps the existing kernel (searx.search, searx.engines, …) and exposes
a versioned JSON API under /api/v1. Serves the built React SPA from
frontend/dist (created in Phase 2) at the web root.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from scalar_fastapi import AgentScalarConfig, Theme, add_scalar_reference

from backend.api import (
    autocomplete,
    engines,
    favicons,
    frontend,
    images,
    meta,
    preferences,
    search,
    stats,
)
from backend.middleware.ratelimiter import RateLimiter, load_config
from backend.openapi import API_DESCRIPTION, openapi_tags, prune_unused_tags
from backend.services.kernel import init_kernel
from searx.version import VERSION_STRING

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_kernel(check_network=False)
    yield


def create_app() -> FastAPI:
    # The version in the published schema must describe the running
    # instance, so it comes from searx.version rather than being pinned
    # here (it used to be hardcoded to "1.0").
    # pylint: disable=redefined-outer-name
    app = FastAPI(
        title="SearXNG API",
        lifespan=lifespan,
        description=API_DESCRIPTION,
        version=VERSION_STRING,
        openapi_tags=openapi_tags(),
        docs_url=None,
        redoc_url=None,
    )

    # FastAPI caches the generated schema, so the pruning has to happen
    # inside the generator rather than after it.
    _openapi = app.openapi

    def openapi() -> dict[str, Any]:
        return prune_unused_tags(_openapi())

    app.openapi = openapi

    # Initialize the kernel (engines, network, metrics, processors).
    # Done eagerly in the factory (not only in the startup event) so the app
    # is fully initialized even when used without a lifespan, e.g. by a
    # TestClient that is not used as a context manager.  Idempotent.
    #
    # This is done before the middleware is registered, because the rate
    # limiter is opt-in and its configuration is read from the settings.
    init_kernel(check_network=False)

    # The rate limiter is opt-in: it is only registered when it is enabled
    # explicitly (server.rate_limit.enabled).  Instances that do not enable
    # it do not pay the cost of the middleware at all.
    #
    # It is registered *before* the CORS middleware, because Starlette runs
    # the middleware registered last as the outermost layer.  CORS has to
    # stay outermost: preflight requests are then answered by CORSMiddleware
    # without consuming the quota and a 429 response still carries the CORS
    # headers.
    rate_limit_config = load_config()
    if rate_limit_config.enabled:
        app.add_middleware(RateLimiter, config=rate_limit_config)

    # Dev CORS (tighten for production; the SPA is same-origin in prod).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    # Register API routers.
    app.include_router(meta.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(preferences.router, prefix="/api/v1")
    app.include_router(autocomplete.router, prefix="/api/v1")
    app.include_router(engines.router, prefix="/api/v1")
    app.include_router(stats.router, prefix="/api/v1")
    app.include_router(favicons.router, prefix="/api/v1")
    app.include_router(images.router, prefix="/api/v1")
    app.include_router(frontend.router, prefix="/api/v1")

    # Register Scalar before the SPA root mount. Starlette matches routes in
    # registration order, so a later root mount would otherwise shadow /docs.
    add_scalar_reference(
        app,
        route="/docs",
        agent=AgentScalarConfig(disabled=True),
        theme=Theme.SATURN,
        show_developer_tools="never",
        telemetry=False,
        hide_client_button=True,
    )

    @app.get(
        "/healthz",
        tags=["system"],
        summary="Health check",
        description=(
            "Liveness probe for the instance. Returns `{\"status\": \"ok\"}` as "
            "long as the API process is up and serving requests. It performs no "
            "search and touches no engine."
        ),
        operation_id="health_check",
    )
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Load Jinja2 templates for the frontend.
    templates_path = REPO_ROOT / "searx" / "templates" / "simple"
    templates = Jinja2Templates(directory=str(templates_path))  # pylint: disable=unused-variable

    # Serve the built SPA (if present) at the web root.
    if FRONTEND_DIST.is_dir():
        assets = FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/static", StaticFiles(directory=str(assets)), name="static")
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="spa")

    return app


app = create_app()


def run() -> None:
    """Console-script entry point: serve the backend with uvicorn."""
    import uvicorn  # pylint: disable=import-outside-toplevel

    uvicorn.run(app, host="::", port=8080)
