# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=import-outside-toplevel, global-statement
"""Initialize the kernel (engines, network, metrics, processors) and load settings.

This is the FastAPI backend's standalone equivalent of the Flask app's
``searx.webapp.init()`` — it performs the same initialization steps but does
NOT require a Flask app and does not import ``searx.webapp`` (which would drag
in the legacy Flask side effects). Idempotent — call once at app startup.
"""

from __future__ import annotations

from searx import settings
from searx.locales import locales_initialize
from searx.plugins import STORAGE as plugins_storage
from searx.search import initialize as search_initialize
from searx.settings_defaults import SCHEMA, apply_schema
from searx.settings_loader import load_settings
from searx.valkeydb import initialize as valkey_initialize

_initialized = False


def init_kernel(check_network: bool = False) -> None:
    """Idempotent kernel init. Call once at app startup.

    Mirrors ``searx.webapp.init()`` step by step, minus the Flask-only parts
    (secret-key production check, limiter):

    - Loads settings from settings.yml (and user overrides) and applies the
      settings schema, so all defaults (e.g. ``outgoing``) exist.
    - Creates the DB schema (first connect), inits the favicon proxy config
      (``Preferences`` reads ``searx.favicons.proxy.CFG``), locales, Valkey
      (no-op without ``valkey.url``) and the plugin storage.
    - Initializes the search kernel: engines, network, metrics, processors.
    """
    global _initialized
    if _initialized:
        return

    # 1. Settings: load default + user settings, then apply the schema.
    cfg, _msg = load_settings(load_user_settings=True)
    cfg = cfg or {}
    apply_schema(cfg, SCHEMA, [])
    settings.clear()
    settings.update(cfg)

    # 2. DB schema is created with the first connect (mirrors webapp.init()).
    from searx.data import get_cache
    from searx.enginelib import ENGINES_CACHE

    conn = get_cache().connect()
    conn.close()
    conn = ENGINES_CACHE.connect()
    conn.close()

    # 3. Favicon proxy config (searx.favicons.proxy.CFG must not be None --
    #    searx.preferences.Preferences reads CFG.resolver_map).
    import searx.favicons

    searx.favicons.init()

    # 4. Locales, Valkey, plugins.
    locales_initialize()
    valkey_initialize()
    plugins_storage.load_settings(searx.get_setting("plugins"))
    # Plugin.init() implementations do not use the Flask app; pass None.
    plugins_storage.init(None)  # pyright: ignore[reportArgumentType]

    # 5. Engines, network, metrics, processors.
    enable_metrics = bool(searx.get_setting("general.enable_metrics"))
    search_initialize(settings["engines"], check_network=check_network, enable_metrics=enable_metrics)

    _initialized = True
