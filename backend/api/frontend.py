# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/{theme}/{page} — Jinja2 template bridge.

Serves the existing SearXNG Jinja2 templates via FastAPI's Jinja2Templates.
This is a temporary bridge until the React SPA (Phase 2) is built and served
from frontend/dist at the web root. It builds its template context standalone
(no searx.webapp import, no Flask request context).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from flask_babel import gettext  # pyright: ignore[reportMissingTypeStubs]

import searx.answerers
import searx.favicons.proxy
from searx import get_setting, logger, settings
from searx.autocomplete import backends as autocomplete_backends_dict
from searx.engines import categories, engines
from searx.locales import LOCALE_NAMES, RTL_LOCALES
from searx.metrics import counter, get_reliabilities, histogram
from searx.plugins import STORAGE as plugins_storage
from searx.preferences import Preferences
from searx.version import GIT_URL, VERSION_STRING
from searx.webadapter import get_selected_categories
from searx.webutils import get_themes, group_engines_in_tab

router = APIRouter(tags=["frontend"])

logger = logger.getChild('frontend')

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CATEGORY = "search"


def _build_preferences() -> Preferences:
    themes = [settings.get("ui", {}).get("default_theme", "simple")]
    return Preferences(
        themes,
        list(categories.keys()),
        engines,  # pyright: ignore[reportArgumentType] TODO: Upstream PR - Preferences engines param type hinting
        plugins_storage,
        None,
    )


def _url_for(endpoint: str, **values) -> str:
    """Minimal stand-in for the legacy custom_url_for."""
    qs = "&".join(f"{k}={v}" for k, v in values.items())
    return f"/{endpoint}" + (f"?{qs}" if qs else "")


def _translations() -> dict[str, str]:
    """Same translation dict as the legacy webapp's get_translations()."""
    return {
        "no_item_found": gettext("No item found"),
        "Source": gettext("Source"),
        "error_loading_next_page": gettext("Error loading the next page"),
        "close": gettext("close"),
    }


def _client_settings(prefs: Preferences) -> dict[str, Any]:
    return {
        "plugins": prefs.plugins.get_enabled(),
        "autocomplete": prefs.get_value("autocomplete"),
        "autocomplete_min": get_setting("search.autocomplete_min"),
        "method": prefs.get_value("method"),
        "translations": _translations(),
        "search_on_category_select": prefs.get_value("search_on_category_select"),
        "hotkeys": prefs.get_value("hotkeys"),
        "url_formatting": prefs.get_value("url_formatting"),
        "theme_static_path": _url_for("static", filename="themes/simple"),
        "results_on_new_tab": prefs.get_value("results_on_new_tab"),
        "favicon_resolver": prefs.get_value("favicon_resolver"),
        "query_in_title": prefs.get_value("query_in_title"),
        "safesearch": prefs.get_value("safesearch"),
        "theme": prefs.get_value("theme"),
        "doi_resolver": prefs.get_value("doi_resolver"),
    }


@router.get(
    "/{theme_name}/{page_name}",
    response_class=HTMLResponse,
    # This route renders HTML, not API data, and it is a catch-all: as a
    # schema operation it would tell a code generator that
    # /api/v1/<anything>/<anything> is a parameterizable API endpoint.
    # Keep it out of the OpenAPI document; the JSON contract of the
    # instance is the /api/v1 routers.
    include_in_schema=False,
    summary="Serve a frontend page",
    description=(
        "Render one of the server-side (no-JS) frontend pages of the selected "
        "theme, for example the search page or the preferences page.\n\n"
        "This endpoint returns HTML and is not part of the JSON API contract."
    ),
    operation_id="get_frontend_page",
)
async def frontend_view(
    request: Request,
    theme_name: str,
    page_name: str,
) -> HTMLResponse:
    """Serve a Jinja2 template page."""
    # Loader root is searx/templates: pages reference "simple/base.html" etc.
    templates = Jinja2Templates(directory=str(REPO_ROOT / "searx" / "templates"))
    # Flask-Babel normally injects these as Jinja globals; do it standalone.
    templates.env.globals["_"] = gettext
    templates.env.globals["gettext"] = gettext
    # Match the legacy webapp's Jinja environment setup.
    templates.env.trim_blocks = True
    templates.env.lstrip_blocks = True
    templates.env.add_extension("jinja2.ext.loopcontrols")
    templates.env.filters["group_engines_in_tab"] = group_engines_in_tab

    prefs = _build_preferences()
    client_settings = _client_settings(prefs)
    locale = prefs.get_value("locale")

    # Templates expect Flask request attributes (request.args/.form/.endpoint).
    request.args = request.query_params  # type: ignore
    request.form = {}  # type: ignore
    request.endpoint = page_name  # type: ignore

    enabled_names = {eng[0] for eng in prefs.engines.get_enabled()}
    enabled_categories = {cat for name in enabled_names for cat in engines[name].categories}
    categories_as_tabs = list(settings["categories_as_tabs"].keys())
    engines_by_category = {
        c: sorted((e for e in categories[c] if e.name in enabled_names), key=lambda e: e.name) for c in categories_as_tabs
    }
    engine_shortcuts = {e.name: e.shortcut or "" for e in engines.values()}

    supports = {e.name: {"safesearch": e.safesearch, "time_range_support": e.time_range_support} for e in engines.values()}
    enable_metrics = bool(get_setting("general.enable_metrics"))
    stats = {}
    max_rate95 = 0
    for e in engines.values():
        if enable_metrics:
            h = histogram("engine", e.name, "time", "total")
            median = round(h.percentage(50), 1) if h.count > 0 else None  # pyright: ignore[reportOptionalMemberAccess]
            rate95 = round(h.percentage(95), 1) if h.count > 0 else None  # pyright: ignore[reportOptionalMemberAccess]
            max_rate95 = max(max_rate95, rate95 or 0)
            successful = counter("engine", e.name, "search", "count", "successful")
            result_count = (
                int(histogram("engine", e.name, "result", "count").sum / float(successful)) if successful else 0  # pyright: ignore[reportOptionalMemberAccess]
            )
            stats[e.name] = {
                "time": median,
                "rate80": round(h.percentage(80), 1) if h.count > 0 else None,  # pyright: ignore[reportOptionalMemberAccess]
                "rate95": rate95,
                "warn_timeout": e.timeout > settings["outgoing"]["request_timeout"],
                "result_count": result_count,
            }
        else:
            stats[e.name] = {
                "time": None,
                "rate80": None,
                "rate95": None,
                "warn_timeout": e.timeout > settings["outgoing"]["request_timeout"],
                "result_count": 0,
            }

    context = {
        "preferences": prefs,
        "preferences_page": page_name == "preferences",
        "engines_with_tokens": any(getattr(e, "tokens", None) for e in engines.values()),
        "locales": LOCALE_NAMES,
        "current_locale": locale or "en",
        "image_proxy": bool(get_setting("server.image_proxy")),
        "engines_by_category": engines_by_category,
        "stats": stats,
        "max_rate95": max_rate95,
        "reliabilities": get_reliabilities(list(engines.keys())),
        "supports": supports,
        "answer_storage": searx.answerers.STORAGE.info,
        "disabled_engines": [e for e, _ in prefs.engines.get_disabled()],
        "autocomplete_backends": list(autocomplete_backends_dict),
        "favicon_resolver_names": list(searx.favicons.proxy.CFG.resolver_map.keys()) if searx.favicons.proxy.CFG else [],
        "shortcuts": engine_shortcuts,
        "themes": get_themes(REPO_ROOT / "searx" / "templates"),
        "plugins_storage": plugins_storage.info,
        "current_doi_resolver": prefs.get_value("doi_resolver"),
        "allowed_plugins": [p.id for p in plugins_storage],
        "preferences_url_params": prefs.get_as_url_params(),
        "locked_preferences": get_setting("preferences").lock,
        "doi_resolvers": get_setting("doi_resolvers", {}),
        "client_settings": base64.b64encode(json.dumps(client_settings).encode()).decode(),
        "endpoint": page_name,
        "cookies": {},
        "errors": [],
        "link_token": "",
        "categories_as_tabs": categories_as_tabs,
        "categories": [c for c in categories_as_tabs if c in enabled_categories],
        "DEFAULT_CATEGORY": DEFAULT_CATEGORY,
        "selected_categories": get_selected_categories(prefs, {}),
        "sxng_locales": [],
        "locale_rfc5646": locale or "en",
        "rtl": locale in RTL_LOCALES,
        "current_language": prefs.get_value("language") or "all",
        "search_formats": [f for f in settings["search"]["formats"] if f != "html"],
        "instance_name": get_setting("general.instance_name"),
        "searxng_version": VERSION_STRING,
        "searxng_git_url": GIT_URL,
        "enable_metrics": enable_metrics,
        "get_setting": get_setting,
        "donation_url": get_setting("general.donation_url"),
        "url_for": _url_for,
        "image_proxify": lambda x: x,
        "favicon_url": lambda x: x,
        "cache_url": settings["ui"]["cache_url"],
        "get_result_template": lambda x: x,
        "opensearch_url": _url_for("opensearch.xml"),
        "urlparse": lambda x: x,
    }
    context.update(client_settings)

    return templates.TemplateResponse(request, f"{theme_name}/{page_name}.html", context)
