# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared service for building the instance's preference objects.

Every route that needs a ``searx.preferences.Preferences`` instance builds
it here, so the four copies of the builder cannot drift apart.

The module also owns the client preference document: :func:`filter_document`
reduces an untrusted JSON document to the subset this instance can apply
(and reports what was skipped), and :func:`normalize_document` converts that
subset into the string form upstream's ``Preferences.parse_dict()`` expects.

Finally it owns the client-held token: :func:`mint_token` stamps a document
with an *epoch* -- a digest of the instance defaults the document is
interpreted against -- and signs it, while :func:`verify_token` checks the
signature and the epoch and reports the document that survived.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zlib
from collections.abc import Container
from typing import TypeGuard

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
from searx.webutils import is_hmac_of, new_hmac

#: Fork-only document key: the default search category. Not an upstream
#: ``key_value_settings`` entry -- it is resolved in the backend.
DEFAULT_CATEGORY_KEY = "default_category"

#: The upstream keys that carry engine and plugin deltas rather than a
#: single absolute value.
ENGINE_KEYS = ("disabled_engines", "enabled_engines")
PLUGIN_KEYS = ("disabled_plugins", "enabled_plugins")

#: Separator between the payload and the hex signature of a token.
TOKEN_SEPARATOR = "."

#: Maximum size of the decompressed token payload (bytes).
MAX_PAYLOAD_BYTES = 65536

#: Number of hex characters of the epoch digest that are kept.
EPOCH_LENGTH = 16

#: The hex digits an epoch may consist of.
HEX_DIGITS = frozenset("0123456789abcdef")


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


def _secret_key() -> str:
    """Return the instance secret key used to sign preference tokens."""
    return settings["server"]["secret_key"]


def _canonical_json(obj: object) -> bytes:
    """Serialize an object to canonical (sorted, compact) JSON bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _named_pairs(doc: dict[str, object]) -> set[str]:
    """Return the engine pairs the document names, across disabled and enabled."""
    return _named_entries(doc, ENGINE_KEYS)


def _named_plugins(doc: dict[str, object]) -> set[str]:
    """Return the plugin ids the document names, across disabled and enabled."""
    return _named_entries(doc, PLUGIN_KEYS)


def _named_entries(doc: dict[str, object], keys: tuple[str, ...]) -> set[str]:
    """Return the string entries the document lists under any of ``keys``."""
    named: set[str] = set()
    for key in keys:
        value = doc.get(key)
        if isinstance(value, list):
            named.update(item for item in value if isinstance(item, str))
    return named


def compute_epoch(doc: dict[str, object], prefs: Preferences) -> str:
    """Return the 16-hex-char epoch of a document against the instance defaults.

    ``doc`` is the *applicable* document (the filtered one), not the
    submitted one: the epoch is computed over exactly the pairs and plugins
    the client's state will be interpreted against.

    A pair or plugin the instance no longer has contributes ``null`` rather
    than raising ``KeyError``. That ``null`` changes the canonical JSON and
    therefore the digest, so a token minted before an engine was removed is
    detected as stale instead of crashing the request.
    """
    payload = {
        "engines": {pair: prefs.engines.default_choices.get(pair) for pair in sorted(_named_pairs(doc))},
        "plugins": {plugin: prefs.plugins.default_choices.get(plugin) for plugin in sorted(_named_plugins(doc))},
        "locks": sorted(prefs.cfg.lock),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()[:EPOCH_LENGTH]


def sign_payload(payload: str) -> str:
    """Return the hex HMAC of a base64url payload, using the instance secret key."""
    return new_hmac(_secret_key(), payload.encode("utf-8"))


def mint_token(doc: dict[str, object], prefs: Preferences) -> str:
    """Mint a signed, epoch-stamped token carrying a document.

    ``doc`` must be the *applicable* document -- the one
    :func:`filter_document` returned, not the submitted one. The epoch is
    computed over the pairs and plugins the document names, so a document
    that names a pair this instance lacks would mint an epoch counting a
    pair that :func:`verify_token` later drops, and the token would be
    stale on first use.

    The token is ``payload [ "." signature ]``, where the payload is the
    base64url of the zlib-compressed canonical JSON of ``{"epoch": ...,
    "doc": ...}`` and the signature is the hex HMAC of the payload string.
    """
    body = {"epoch": compute_epoch(doc, prefs), "doc": doc}
    payload = base64.urlsafe_b64encode(zlib.compress(_canonical_json(body))).decode("ascii")
    return payload + TOKEN_SEPARATOR + sign_payload(payload)


def decode_token(token: str) -> tuple[dict[str, object], str, bool]:
    """Split a token into (document, epoch, signed).

    This validates the *envelope* only: that the payload decodes and has the
    expected shape. It neither validates the document's content nor checks
    the signature or the epoch -- that is :func:`verify_token`'s job.

    Every decode failure becomes a ``PreferencesError``: an untrusted token
    string can fail in several ways (``binascii.Error``, ``zlib.error``,
    ``json.JSONDecodeError``), and none of them may escape as a 500.
    """
    payload, separator, _signature = token.partition(TOKEN_SEPARATOR)
    try:
        body = _decode_payload(payload)
    except (binascii.Error, zlib.error, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise PreferencesError(400, "preferences_invalid", "the preference token is malformed") from exc
    doc, epoch = _token_body(body)
    return doc, epoch, bool(separator)


def verify_token(token: str, prefs: Preferences) -> tuple[dict[str, object], list[str]]:
    """Verify a token and return (filtered document, skipped notes).

    A malformed token is a 400, a signature that does not verify is a 403,
    and a signed token whose epoch no longer matches the instance defaults
    is a 409. An unsigned token is accepted, but carries no verifiable epoch
    -- only the epoch comparison is skipped for it, the document is still
    filtered.
    """
    payload, _, signature = token.partition(TOKEN_SEPARATOR)
    doc, epoch, signed = decode_token(token)
    if signed and not _signature_matches(payload, signature):
        raise PreferencesError(
            403,
            "preferences_invalid_signature",
            "the preference token signature does not verify",
        )
    filtered, skipped = filter_document(doc, prefs)
    if signed and compute_epoch(filtered, prefs) != epoch:
        raise PreferencesError(
            409,
            "preferences_stale",
            "the preference token is stale; re-fetch the preferences and post a new document",
        )
    return filtered, skipped


def _decode_payload(payload: str) -> object:
    """Decode the base64url/zlib payload of a token into its JSON body."""
    # The base64 payload of a legal token is at most a few times
    # MAX_PAYLOAD_BYTES; refuse anything wildly larger before allocating
    # for it. This is a cheap guard against an attacker-controlled string.
    if len(payload) > 4 * MAX_PAYLOAD_BYTES:
        raise PreferencesError(400, "preferences_invalid", "the preference token is malformed")
    raw = base64.urlsafe_b64decode(payload)
    decompressor = zlib.decompressobj()
    data = decompressor.decompress(raw, MAX_PAYLOAD_BYTES)
    if decompressor.unconsumed_tail:
        raise PreferencesError(
            400,
            "preferences_invalid",
            "the preference token payload is too large",
        )
    # A stream truncated after its last data block still yields the full
    # JSON, and a second member appended after a complete stream is simply
    # ignored -- both are malformed payloads, not valid ones.
    if not decompressor.eof or decompressor.unused_data:
        raise PreferencesError(400, "preferences_invalid", "the preference token is malformed")
    return json.loads(data)


def _token_body(body: object) -> tuple[dict[str, object], str]:
    """Return the (document, epoch) of a decoded token body."""
    if not isinstance(body, dict):
        raise PreferencesError(400, "preferences_invalid", "the preference token is malformed")
    doc = body.get("doc")
    epoch = body.get("epoch")
    if not isinstance(doc, dict):
        raise PreferencesError(400, "preferences_invalid", "the preference token is malformed")
    if not _is_epoch(epoch):
        raise PreferencesError(400, "preferences_invalid", "the preference token is malformed")
    return doc, epoch


def _is_epoch(value: object) -> TypeGuard[str]:
    """Return whether the value is a 16-hex-char epoch."""
    return isinstance(value, str) and len(value) == EPOCH_LENGTH and all(char in HEX_DIGITS for char in value)


def _signature_matches(payload: str, signature: str) -> bool:
    """Return whether the signature is the HMAC of the payload.

    ``hmac.compare_digest`` raises a ``TypeError`` on a non-ASCII
    signature, so the comparison is guarded: a signature that cannot even
    be compared is simply a signature that does not verify.
    """
    try:
        return is_hmac_of(_secret_key(), payload.encode("utf-8"), signature)
    except (TypeError, ValueError):
        return False
