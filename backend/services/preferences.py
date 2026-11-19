# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared service for building the instance's preference objects.

Every route that needs a ``searx.preferences.Preferences`` instance builds
it here, so the four copies of the builder cannot drift apart.
"""

from __future__ import annotations

from searx import settings
from searx.engines import categories as engine_categories
from searx.engines import engines
from searx.plugins import STORAGE as plugins_storage
from searx.preferences import Preferences


def build_preferences() -> Preferences:
    """Build a Preferences instance from the instance defaults.

    Mirrors ``searx.webapp.pre_request``: the same themes, the same
    category list, the same engine and plugin registries, and no
    client-supplied state (the client document is applied separately
    via ``parse_dict``).
    """
    themes = [settings.get("ui", {}).get("default_theme", "simple")]
    return Preferences(
        themes=themes,
        categories=list(engine_categories.keys()),
        engines=engines,  # pyright: ignore[reportArgumentType]
        plugins=plugins_storage,
        client=None,
    )
