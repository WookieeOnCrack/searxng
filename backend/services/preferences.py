# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared service for building the instance's preference objects.

Every route that needs a ``searx.preferences.Preferences`` instance builds
it here, so the four copies of the builder cannot drift apart.

The module also owns the client preference document: :func:`filter_document`
reduces an untrusted JSON document to the subset this instance can apply
(and reports what was skipped), and :func:`normalize_document` converts that
subset into the string form upstream's ``Preferences.parse_dict()`` expects.
"""

from __future__ import annotations

from collections.abc import Container

from searx import settings
from searx.engines import categories as engine_categories
from searx.engines import engines
from searx.plugins import STORAGE as plugins_storage
from searx.preferences import (
    MAP_STR2BOOL,
    BooleanSetting,
    EnumStringSetting,
    MapSetting,
    MultipleChoiceSetting,
    Preferences,
    Setting,
)

#: Fork-only document key: the default search category. Not an upstream
#: ``key_value_settings`` entry -- it is resolved in the backend.
DEFAULT_CATEGORY_KEY = "default_category"

#: The upstream keys that carry engine and plugin deltas rather than a
#: single absolute value.
ENGINE_KEYS = ("disabled_engines", "enabled_engines")
PLUGIN_KEYS = ("disabled_plugins", "enabled_plugins")


class PreferencesError(Exception):
    """A client preference error carrying a status code and an error code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


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


def allowed_keys(prefs: Preferences) -> list[str]:
    """Return the document keys this instance understands, sorted."""
    return sorted(set(prefs.key_value_settings) | set(ENGINE_KEYS) | set(PLUGIN_KEYS) | {DEFAULT_CATEGORY_KEY})


def filter_document(raw: dict[str, object], prefs: Preferences) -> tuple[dict[str, object], list[str]]:
    """Return (applicable document, human-readable notes for skipped keys).

    The document is applied leniently: a key this instance cannot apply is
    skipped and reported, not rejected. The single exception is ``tokens``,
    which is forbidden outright because engine API secrets must never ride
    in a portable blob.
    """
    if "tokens" in raw:
        raise PreferencesError(
            400,
            "preferences_tokens_not_allowed",
            "engine tokens are not allowed in a preference document",
        )

    allowed = set(allowed_keys(prefs))
    applicable: dict[str, object] = {}
    skipped: list[str] = []

    for key, value in raw.items():
        reason = _skip_reason(key, value, prefs, allowed)
        if reason is None:
            applicable[key] = value
        else:
            skipped.append(f"{key}: {reason}")

    return applicable, skipped


def normalize_document(doc: dict[str, object], prefs: Preferences) -> dict[str, str]:
    """Convert an applicable document into the string form parse_dict() takes.

    The document must be the *applicable* one returned by
    :func:`filter_document`: every value here has already been checked
    against the instance, so the helpers below never have to guess. A
    value that slipped through raises ``ValueError`` rather than
    silently normalizing to something the instance would reject.
    """
    normalized: dict[str, str] = {}

    for key, value in doc.items():
        if key == DEFAULT_CATEGORY_KEY:
            # Fork-only key: upstream's parse_dict() does not know it, and
            # the backend resolves it separately.
            continue
        setting = prefs.key_value_settings.get(key)
        if isinstance(setting, MultipleChoiceSetting):
            normalized[key] = _join_list(value)
        elif isinstance(setting, BooleanSetting):
            normalized[key] = _bool_str(value)
        elif isinstance(setting, MapSetting):
            normalized[key] = _map_str(setting, value)
        elif key in ENGINE_KEYS or key in PLUGIN_KEYS:
            normalized[key] = _join_list(value)
        else:
            normalized[key] = str(value)

    # Upstream dispatches the engine branch on the presence of the
    # ``disabled_engines`` key and the plugin branch on the presence of
    # ``disabled_plugins``: an enabled-only document would be dropped.
    for key in ENGINE_KEYS + PLUGIN_KEYS:
        normalized.setdefault(key, "")

    return normalized


def _skip_reason(
    key: str,
    value: object,
    prefs: Preferences,
    allowed: set[str],
) -> str | None:
    """Return why this key cannot be applied, or None when it can."""
    if key not in allowed:
        return "unknown key"

    if key == DEFAULT_CATEGORY_KEY:
        return _category_reason(value)

    if key in ENGINE_KEYS:
        return _list_reason(value, prefs.engines.choices, "unknown engine pair")

    if key in PLUGIN_KEYS:
        return _list_reason(value, prefs.plugins.choices, "unknown plugin")

    return _setting_reason(prefs.key_value_settings[key], value)


def _category_reason(value: object) -> str | None:
    """Return why a ``default_category`` value cannot be applied."""
    # The fork-only key takes the real categories, not the "none" sentinel
    # the upstream ``categories`` setting also accepts.
    if isinstance(value, str) and value in engine_categories:
        return None
    return "unknown category"


def _setting_reason(setting: Setting, value: object) -> str | None:
    """Return why a ``key_value_settings`` value cannot be applied."""
    # A locked setting is skipped upstream (``parse_dict`` continues on it),
    # so this instance cannot apply it either -- and keeping it in the
    # document would report a value that never takes effect.
    if setting.locked:
        return "locked by this instance"

    if isinstance(setting, MultipleChoiceSetting):
        return _list_reason(value, list(setting.choices), "unknown choice")

    if isinstance(setting, BooleanSetting):
        return _bool_reason(value)

    if isinstance(setting, MapSetting):
        return _map_reason(setting, value)

    if isinstance(setting, EnumStringSetting):
        return _enum_reason(setting, value)

    return "unsupported setting"


def _enum_reason(setting: EnumStringSetting, value: object) -> str | None:
    """Return why an enum-valued setting cannot be applied."""
    # The membership check is deliberate: upstream silently coerces an
    # unknown value (e.g. a language code) to the instance default.
    if isinstance(value, str) and value in list(setting.choices):
        return None
    return "unknown choice"


def _bool_reason(value: object) -> str | None:
    """Return why a boolean value cannot be applied."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value in MAP_STR2BOOL:
        return None
    return "not a boolean value"


def _map_reason(setting: MapSetting, value: object) -> str | None:
    """Return why a map setting value cannot be applied."""
    # ``bool`` is a subclass of ``int``: test it first, or ``true`` would
    # pass the integer check.
    if isinstance(value, bool):
        return "not a valid choice"
    if isinstance(value, int) and value in setting.map.values():
        return None
    if isinstance(value, str) and value in setting.map:
        return None
    return "not a valid choice"


def _list_reason(value: object, choices: Container[str], label: str) -> str | None:
    """Return why a list-valued key cannot be applied, or None when it can."""
    if not isinstance(value, list):
        return "not a list"
    for item in value:
        if not isinstance(item, str):
            return "not a list of strings"
        if item not in choices:
            return f"{label}: {item}"
    return None


def _join_list(value: object) -> str:
    """Join a list of strings into the comma-separated form upstream expects.

    The document has already been filtered, so every element is a string.
    A non-string element means the caller passed an unfiltered document:
    raise rather than drop elements silently (dropping one would change
    what the client asked for).
    """
    if not isinstance(value, list):
        raise ValueError(f"expected a list, got {type(value).__name__}")
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"expected a list of strings, got {type(item).__name__}")
    return ",".join(value)


def _bool_str(value: object) -> str:
    """Render a boolean setting value as the string upstream parses."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str) and value in MAP_STR2BOOL:
        return "1" if MAP_STR2BOOL[value] else "0"
    raise ValueError(f"not a boolean value: {value!r}")


def _map_str(setting: MapSetting, value: object) -> str:
    """Render a map setting value as the key upstream's ``map`` holds."""
    if isinstance(value, bool):
        raise ValueError(f"not a valid choice: {value!r}")
    if isinstance(value, str) and value in setting.map:
        return value
    if isinstance(value, int):
        for key, mapped in setting.map.items():
            if mapped == value:
                return key
    raise ValueError(f"not a valid choice: {value!r}")
