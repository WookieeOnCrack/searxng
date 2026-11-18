# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/preferences — return the preferences page data as JSON."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.models.api import CategoryMeta, EngineMeta, LocaleMeta, PreferencesResponse
from searx import settings
from searx.engines import categories, engines
from searx.locales import LOCALE_NAMES, RTL_LOCALES
from searx.plugins import STORAGE as plugins_storage
from searx.preferences import Preferences
from searx.settings_defaults import OUTPUT_FORMATS

router = APIRouter(tags=["preferences"])


def _build_preferences() -> Preferences:
    """Build a Preferences instance the same way webapp.pre_request does."""
    themes: list[str] = [settings.get("ui", {}).get("default_theme", "simple")]
    client_pref = None
    return Preferences(
        themes=themes,
        categories=list(categories.keys()),
        engines=engines,  # pyright: ignore[reportArgumentType]
        plugins=plugins_storage,
        client=client_pref,
    )


@router.get(
    "/preferences",
    response_model=PreferencesResponse,
    summary="Get the preference catalogue",
    description=(
        "Return the preference catalogue of this instance: the selectable "
        "categories, engines and locales, the available output "
        "formats and styles, the default and current values of every "
        "preference, and the list of preferences the operator has locked.\n\n"
        "The endpoint takes no parameters and reports the server-side state; it "
        "does not read or write any per-client preference."
    ),
    operation_id="get_preferences",
)
def get_preferences() -> PreferencesResponse:
    prefs = _build_preferences()

    # Collect current values from the preferences object.
    # NOTE: the endpoint is stateless -- no client preference cookie or form
    # is parsed here -- so the preferences object still holds the instance
    # defaults and `current` equals `default`. They stay separate fields in
    # the contract so a future per-client mode can populate `current` alone.
    current: dict[str, Any] = {}
    for name, setting in prefs.key_value_settings.items():
        current[name] = setting.get_value()

    # Collect default values from the settings.
    default: dict[str, Any] = {}
    for name, setting in prefs.key_value_settings.items():
        default[name] = setting.get_value()

    # Locked preferences.
    locked: list[str] = list(prefs.cfg.lock)

    # Categories.
    categories_list: list[CategoryMeta] = [
        CategoryMeta(
            name=c,
            title=c.replace("_", " ").title(),
            engines=[e.name for e in engines.values() if c in (e.categories or [])],
        )
        for c in categories
    ]

    # Engines.
    engines_list: list[EngineMeta] = [EngineMeta.from_engine(e) for e in engines.values()]

    # Locales.
    locales: list[LocaleMeta] = [
        LocaleMeta(tag=tag, title=LOCALE_NAMES.get(tag, tag), rtl=tag in RTL_LOCALES) for tag in LOCALE_NAMES
    ]

    return PreferencesResponse(
        categories=categories_list,
        engines=engines_list,
        locales=locales,
        formats=OUTPUT_FORMATS,
        styles=["auto", "light", "dark", "black"],
        default=default,
        current=current,
        locked=locked,
    )
